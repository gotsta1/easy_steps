"""
BotHelp API client: identity export, confirmed field writes, and message triggers.

Used to send expiry notification messages to users in the BotHelp chat
by triggering pre-configured bot steps.

Uses v1 endpoint: POST /v1/subscribers/{subscriber_id}/bot
(v2 /subscribers/messenger/ is Facebook-only)
"""
from __future__ import annotations

import logging
import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

BOTHELP_OAUTH_URL = "https://oauth.bothelp.io/oauth2/token"
BOTHELP_API_BASE = "https://api.bothelp.io"

# Refresh token 5 minutes before actual expiry.
_TOKEN_REFRESH_MARGIN = 300


class BotHelpAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"BotHelp API error {status_code}: {detail}")


@dataclass(frozen=True)
class BotHelpSubscriber:
    subscriber_id: int
    telegram_user_id: int
    channel_id: str


def parse_subscriber(data: dict) -> BotHelpSubscriber | None:
    if not isinstance(data, dict):
        return None
    if data.get("channelType") != "telegram":
        return None
    values = [str(data.get(key, "")) for key in ("id", "userId")]
    if not all(v.isdigit() and int(v) > 0 for v in values):
        return None
    channel_id = str(data.get("channelId", ""))
    if not channel_id:
        return None
    return BotHelpSubscriber(int(values[0]), int(values[1]), channel_id)


@lru_cache(maxsize=1)
def get_bothelp_client() -> BotHelpClient:
    from app.core.config import get_settings

    settings = get_settings()
    return BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)


class BotHelpClient:
    """Manages OAuth2 tokens and exposes high-level API methods."""

    def __init__(self, client_id: str, client_secret: str, *, http_client=None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._request_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()
        self._http = http_client or httpx.AsyncClient(timeout=30)
        self._last_request_at = 0.0
        self.directory: dict[int, BotHelpSubscriber] | None = None
        self.directory_channel: str | None = None
        self.directory_attempted_at = 0.0
        self.directory_refreshed_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except ValueError as exc:
            raise BotHelpAPIError(502, "invalid_json_response") from exc

    async def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            resp = await self._http.post(
                BOTHELP_OAUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )

            if resp.status_code != 200:
                raise BotHelpAPIError(resp.status_code, "oauth_failed")
            try:
                data = resp.json()
                self._token = data["access_token"]
                expires_in = int(data.get("expires_in", 3600))
                if not isinstance(self._token, str) or not self._token:
                    raise ValueError("missing token")
            except (ValueError, KeyError, TypeError) as exc:
                raise BotHelpAPIError(502, "invalid_oauth_response") from exc
            self._token_expires_at = time.monotonic() + max(0, expires_in - _TOKEN_REFRESH_MARGIN)
            logger.info("bothelp_token_refreshed expires_in=%d", expires_in)
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Retry only idempotent reads/field replacements, never message triggers."""
        attempts = 3 if method in {"GET", "PATCH"} else 1
        for attempt in range(attempts):
            try:
                token = await self._ensure_token()
                async with self._request_lock:
                    await asyncio.sleep(max(0, .15 - (time.monotonic() - self._last_request_at)))
                    self._last_request_at = time.monotonic()
                    response = await self._http.request(
                        method, BOTHELP_API_BASE + path,
                        headers={"Authorization": f"Bearer {token}"}, **kwargs,
                    )
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise BotHelpAPIError(0, type(exc).__name__) from exc
            if response.status_code == 401:
                self._token = None
            if response.status_code in {401, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                retry_after = response.headers.get("Retry-After", "0")
                delay = min(60, int(retry_after)) if retry_after.isdigit() else 0
                await asyncio.sleep(max(2 ** attempt, delay))
                continue
            if response.status_code not in (200, 201, 204):
                raise BotHelpAPIError(response.status_code, "request_failed")
            return response
        raise BotHelpAPIError(0, "retry_exhausted")

    async def get_subscriber(self, subscriber_id: int) -> BotHelpSubscriber | None:
        response = await self._request("GET", "/v1/subscribers/", params={"after": subscriber_id - 1})
        data = self._json_object(response).get("data")
        if not isinstance(data, list):
            raise BotHelpAPIError(502, "invalid_directory_response")
        return next((parse_subscriber(x) for x in data if isinstance(x, dict) and str(x.get("id")) == str(subscriber_id)), None)

    async def fetch_directory(self, channel_id: str) -> dict[int, BotHelpSubscriber]:
        after = 0
        result: dict[int, BotHelpSubscriber] = {}
        for _ in range(1000):
            response = await self._request("GET", "/v1/subscribers/", params={"after": after})
            body = self._json_object(response)
            items = body.get("data")
            if not isinstance(items, list):
                raise BotHelpAPIError(502, "invalid_directory_response")
            for item in items:
                subscriber = parse_subscriber(item)
                if subscriber and subscriber.channel_id == channel_id:
                    if subscriber.subscriber_id in result:
                        raise BotHelpAPIError(502, "duplicate_directory_id")
                    result[subscriber.subscriber_id] = subscriber
            paging = body.get("paging", {})
            if not paging.get("next"):
                return result
            cursor = paging.get("cursor", {}).get("after")
            if not items or not isinstance(cursor, int) or cursor <= after:
                raise BotHelpAPIError(502, "non_advancing_directory_cursor")
            after = cursor
        raise BotHelpAPIError(502, "directory_page_limit")

    async def replace_custom_field(self, subscriber_id: int, field: str, value: str) -> None:
        path = "/" + field.replace("~", "~0").replace("/", "~1")
        response = await self._request(
            "PATCH", f"/v1/subscribers/{subscriber_id}/customFields",
            json=[{"op": "replace", "path": path, "value": value}],
        )
        success = self._json_object(response).get("success") is True
        if not success:
            raise BotHelpAPIError(502, "field_update_not_confirmed")

    async def trigger_bot_step(
        self,
        bothelp_subscriber_id: int,
        bot_referral: str,
        step_referral: str,
    ) -> None:
        """Start a bot at a specific step for a BotHelp subscriber."""
        await self._request(
            "POST", f"/v1/subscribers/{bothelp_subscriber_id}/bot",
            json={"botReferral": bot_referral, "stepReferral": step_referral},
        )

        logger.info(
            "bothelp_step_triggered sub_id=%d step=%s",
            bothelp_subscriber_id,
            step_referral,
        )
