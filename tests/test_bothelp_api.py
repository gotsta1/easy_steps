import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import bothelp_api as api


def make_client(handler):
    def transport(request):
        if request.url.host == "oauth.bothelp.io":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        return handler(request)
    return api.BotHelpClient("test", "test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))


def subscriber(uid=10, sid=20, channel="channel"):
    return {"id": sid, "userId": str(uid), "channelId": channel, "channelType": "telegram"}


async def test_lookup_requires_exact_subscriber_id():
    client = make_client(lambda r: httpx.Response(200, json={"data": [subscriber(sid=21)]}))
    try:
        assert await client.get_subscriber(20) is None
    finally:
        await client.aclose()


async def test_field_replacement_uses_json_patch_and_confirms_success():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True})
    client = make_client(handler)
    try:
        await client.replace_custom_field(20, "club_subscription_status", "active")
        assert seen[0].method == "PATCH"
        assert seen[0].url.path == "/v1/subscribers/20/customFields"
        assert json.loads(seen[0].content) == [{"op": "replace", "path": "/club_subscription_status", "value": "active"}]
    finally:
        await client.aclose()


@pytest.mark.parametrize("body", [{"success": False}, {}, []])
async def test_http_200_without_positive_result_is_not_success(body):
    client = make_client(lambda r: httpx.Response(200, json=body))
    try:
        with pytest.raises(api.BotHelpAPIError):
            await client.replace_custom_field(20, "club_subscription_status", "active")
    finally:
        await client.aclose()


async def test_message_trigger_is_never_automatically_retried():
    seen=[]
    client=make_client(lambda r: (seen.append(r) or httpx.Response(503)))
    try:
        with pytest.raises(api.BotHelpAPIError):
            await client.trigger_bot_step(20,"bot","message")
        assert len(seen)==1
    finally:
        await client.aclose()


async def test_safe_replacement_retries_429_and_honors_retry_after(monkeypatch):
    calls=[]
    sleep=AsyncMock()
    monkeypatch.setattr(api.asyncio,"sleep",sleep)
    def handler(r):
        calls.append(r)
        return httpx.Response(429,headers={"Retry-After":"3"}) if len(calls)==1 else httpx.Response(200,json={"success":True})
    client=make_client(handler)
    try:
        await client.replace_custom_field(20,"club_subscription_status","active")
        assert len(calls)==2
        assert any(c.args==(3,) for c in sleep.await_args_list)
    finally:
        await client.aclose()


async def test_directory_paginates_and_filters_exact_channel():
    def handler(request):
        after=int(request.url.params['after'])
        if after==0:
            return httpx.Response(200,json={"data":[subscriber(),subscriber(sid=21,channel="other")],"paging":{"next":"after=21","cursor":{"after":21}}})
        assert after==21
        return httpx.Response(200,json={"data":[subscriber(uid=11,sid=22)],"paging":{}})
    client=make_client(handler)
    try:
        profiles=await client.fetch_directory("channel")
        assert set(profiles)=={20,22}
        assert profiles[20].telegram_user_id==10
    finally:
        await client.aclose()


async def test_directory_rejects_non_advancing_cursor():
    client=make_client(lambda r:httpx.Response(200,json={"data":[subscriber()],"paging":{"next":"x","cursor":{"after":0}}}))
    try:
        with pytest.raises(api.BotHelpAPIError,match="non_advancing"):
            await client.fetch_directory("channel")
    finally:
        await client.aclose()


async def test_directory_failure_does_not_return_partial_results(monkeypatch):
    monkeypatch.setattr(api.asyncio,"sleep",AsyncMock())
    calls=[]
    def handler(r):
        calls.append(r)
        if len(calls)==1: return httpx.Response(200,json={"data":[subscriber()],"paging":{"next":"x","cursor":{"after":20}}})
        return httpx.Response(503)
    client=make_client(handler)
    try:
        with pytest.raises(api.BotHelpAPIError): await client.fetch_directory("channel")
        assert client.directory is None
    finally:
        await client.aclose()
