"""
BotHelp API client — OAuth2 token management + bot step triggering.

Used to send expiry notification messages to users in the BotHelp chat
by triggering pre-configured bot steps.

Uses v1 endpoint: POST /v1/subscribers/{subscriber_id}/bot
(v2 /subscribers/messenger/ is Facebook-only)
"""
from __future__ import annotations

import logging
import time

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


class BotHelpClient:
    """Manages OAuth2 tokens and exposes high-level API methods."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                BOTHELP_OAUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )

        if resp.status_code != 200:
            logger.error("bothelp_oauth_failed status=%d body=%s", resp.status_code, resp.text[:300])
            raise BotHelpAPIError(resp.status_code, resp.text[:300])

        data = resp.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_MARGIN
        logger.info("bothelp_token_refreshed expires_in=%d", expires_in)
        return self._token

    async def trigger_bot_step(
        self,
        bothelp_subscriber_id: int,
        bot_referral: str,
        step_referral: str,
    ) -> None:
        """Start a bot at a specific step for a BotHelp subscriber."""
        token = await self._ensure_token()
        url = f"{BOTHELP_API_BASE}/v1/subscribers/{bothelp_subscriber_id}/bot"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={"botReferral": bot_referral, "stepReferral": step_referral},
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code not in (200, 201, 204):
            logger.error(
                "bothelp_trigger_step_failed sub_id=%d step=%s status=%d body=%s",
                bothelp_subscriber_id,
                step_referral,
                resp.status_code,
                resp.text[:300],
            )
            raise BotHelpAPIError(resp.status_code, resp.text[:300])

        logger.info(
            "bothelp_step_triggered sub_id=%d step=%s",
            bothelp_subscriber_id,
            step_referral,
        )
