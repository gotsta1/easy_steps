from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import bothelp_webhook as route
from app.services.bothelp_api import BotHelpSubscriber, BotHelpAPIError
from app.core.config import Settings


@pytest.mark.parametrize("payload",[
    [],{}, {"user_id":-10,"bothelp_user_id":20}, {"user_id":True,"bothelp_user_id":20},
    {"user_id":10.5,"bothelp_user_id":20},
])
async def test_invalid_webhook_cannot_change_mappings(monkeypatch,payload):
    repo=AsyncMock();monkeypatch.setattr(route,"UserRepo",repo)
    r=await route.bothelp_webhook_handler(SimpleNamespace(json=AsyncMock(return_value=payload)),Settings.model_construct(),AsyncMock())
    assert r['status'] in {'invalid_ids','missing_fields','invalid_payload'}
    repo.assert_not_called()


@pytest.mark.parametrize("profile",[None,BotHelpSubscriber(20,999,"channel"),BotHelpSubscriber(20,10,"other")])
async def test_unverified_identity_cannot_overwrite_existing_mapping(monkeypatch,profile):
    repo=AsyncMock();monkeypatch.setattr(route,"UserRepo",repo)
    monkeypatch.setattr(route,"get_bothelp_client",lambda:SimpleNamespace(get_subscriber=AsyncMock(return_value=profile)))
    r=await route.bothelp_webhook_handler(SimpleNamespace(json=AsyncMock(return_value={'user_id':10,'bothelp_user_id':20})),Settings.model_construct(BOTHELP_CHANNEL_ID="channel"),AsyncMock())
    assert r['status']=='identity_mismatch'
    repo.assert_not_called()


async def test_verified_webhook_binds_and_uses_direct_sync(monkeypatch):
    profile=BotHelpSubscriber(20,10,"channel");user=SimpleNamespace()
    repo=SimpleNamespace(get_or_create=AsyncMock(return_value=(user,False)))
    monkeypatch.setattr(route,"UserRepo",lambda db:repo)
    client=SimpleNamespace(get_subscriber=AsyncMock(return_value=profile))
    monkeypatch.setattr(route,"get_bothelp_client",lambda:client)
    bind=AsyncMock();sync=AsyncMock()
    monkeypatch.setattr(route,"bind_verified_subscriber",bind)
    monkeypatch.setattr(route,"sync_telegram_user_status",sync)
    db=AsyncMock();settings=Settings.model_construct(BOTHELP_CHANNEL_ID="channel")
    r=await route.bothelp_webhook_handler(SimpleNamespace(json=AsyncMock(return_value={'user_id':10,'bothelp_user_id':20})),settings,db)
    assert r=={'status':'ok'}
    bind.assert_awaited_once_with(db,user,profile)
    db.commit.assert_awaited_once()
    sync.assert_awaited_once_with(db,settings,10,client=client)


async def test_provider_failure_does_not_modify_existing_mapping(monkeypatch):
    repo=AsyncMock();monkeypatch.setattr(route,"UserRepo",repo)
    monkeypatch.setattr(route,"get_bothelp_client",lambda:SimpleNamespace(get_subscriber=AsyncMock(side_effect=BotHelpAPIError(503,"unavailable"))))
    with pytest.raises(HTTPException) as e:
        await route.bothelp_webhook_handler(SimpleNamespace(json=AsyncMock(return_value={'user_id':10,'bothelp_user_id':20})),Settings.model_construct(BOTHELP_CHANNEL_ID="channel"),AsyncMock())
    assert e.value.status_code==503
    repo.assert_not_called()
