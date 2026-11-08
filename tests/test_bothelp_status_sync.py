from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace
import time
import pytest

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus, User
from app.services import bothelp_status_sync as sync
from app.services.bothelp_api import BotHelpAPIError, BotHelpSubscriber
from app.services.bothelp_status_sync import (
    is_bothelp_status_sync_configured,
    subscription_status_for_entitlement,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def make_club(
    status: EntitlementStatus = EntitlementStatus.active,
    active_until: datetime | None = None,
) -> Entitlement:
    return Entitlement(
        user_id=1,
        product_key="club",
        status=status,
        active_until=active_until,
    )


def test_subscription_status_has_three_states() -> None:
    assert subscription_status_for_entitlement(None, NOW) == "never_paid"
    assert subscription_status_for_entitlement(make_club(), NOW) == "active"
    assert (
        subscription_status_for_entitlement(
            make_club(active_until=NOW + timedelta(seconds=1)), NOW
        )
        == "active"
    )
    assert (
        subscription_status_for_entitlement(
            make_club(active_until=NOW - timedelta(seconds=1)), NOW
        )
        == "expired"
    )
    assert (
        subscription_status_for_entitlement(
            make_club(status=EntitlementStatus.canceled), NOW
        )
        == "expired"
    )


def test_status_sync_requires_complete_bothelp_configuration() -> None:
    configured = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_CHANNEL_ID="channel",
    )
    missing_step = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_CHANNEL_ID="",
    )

    assert is_bothelp_status_sync_configured(configured) is True
    assert is_bothelp_status_sync_configured(missing_step) is False


def make_user():
    return User(id=1,telegram_user_id=10,bothelp_subscriber_id=20,bothelp_subscription_status="expired",
                bothelp_status_sync_attempts=0,bothelp_mapping_verified_at=None,
                bothelp_status_next_retry_at=None)


def client():
    return SimpleNamespace(directory=None,directory_channel=None,
        get_subscriber=AsyncMock(return_value=BotHelpSubscriber(20,10,"channel")),
        replace_custom_field=AsyncMock())


def settings():
    return Settings.model_construct(BOTHELP_CHANNEL_ID="channel")


async def test_direct_field_update_replaces_flow_trigger(monkeypatch):
    monkeypatch.setattr(sync,"utcnow",lambda:NOW)
    user=make_user(); api=client(); db=AsyncMock()
    assert await sync._write_status(db,settings(),api,user,"active") is True
    api.replace_custom_field.assert_awaited_once_with(20,"club_subscription_status","active")
    assert user.bothelp_subscription_status=="active"
    assert user.bothelp_status_synced_at==NOW
    assert user.bothelp_mapping_verified_at==NOW
    assert user.bothelp_status_sync_attempts==0


async def test_mismatched_telegram_identity_never_receives_an_update(monkeypatch):
    monkeypatch.setattr(sync,"utcnow",lambda:NOW)
    user=make_user(); api=client()
    api.get_subscriber.return_value=BotHelpSubscriber(20,999,"channel")
    assert await sync._write_status(AsyncMock(),settings(),api,user,"active") is False
    api.replace_custom_field.assert_not_awaited()
    assert user.bothelp_subscription_status=="expired"
    assert user.bothelp_status_next_retry_at==NOW+timedelta(hours=1)


@pytest.mark.parametrize("code",[404,429,503,0])
async def test_failed_update_persists_retry_not_a_false_success(monkeypatch,code):
    monkeypatch.setattr(sync,"utcnow",lambda:NOW)
    user=make_user();api=client()
    api.replace_custom_field.side_effect=BotHelpAPIError(code,"test_failure")
    assert await sync._write_status(AsyncMock(),settings(),api,user,"active") is False
    assert user.bothelp_subscription_status=="expired"
    assert user.bothelp_status_synced_at is None
    assert user.bothelp_status_next_retry_at>NOW


async def test_retry_deadline_prevents_hammering(monkeypatch):
    monkeypatch.setattr(sync,"utcnow",lambda:NOW)
    user=make_user();api=client();user.bothelp_status_next_retry_at=NOW+timedelta(hours=1)
    assert await sync._write_status(AsyncMock(),settings(),api,user,"active") is False
    api.get_subscriber.assert_not_awaited();api.replace_custom_field.assert_not_awaited()


async def test_fresh_directory_avoids_one_lookup_per_user(monkeypatch):
    monkeypatch.setattr(sync,"utcnow",lambda:NOW)
    user=make_user();api=client()
    api.directory_channel="channel";api.directory_refreshed_at=time.monotonic()
    api.directory={20:BotHelpSubscriber(20,10,"channel")}
    assert await sync._write_status(AsyncMock(),settings(),api,user,"active") is True
    api.get_subscriber.assert_not_awaited()


async def test_candidate_query_includes_periodic_refresh_and_due_retries_only():
    from sqlalchemy.dialects import postgresql
    from unittest.mock import MagicMock
    db=AsyncMock();db.execute.return_value=MagicMock(all=lambda:[])
    await sync._load_sync_candidates(db,50,86400)
    sql=str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "bothelp_status_next_retry_at IS NULL" in sql
    assert "bothelp_status_synced_at IS NULL" in sql
    assert "bothelp_status_synced_at <" in sql
