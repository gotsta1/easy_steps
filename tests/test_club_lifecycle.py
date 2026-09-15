from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
from app.services.bothelp_club_lifecycle import (
    _trigger_retention_message,
    is_club_lifecycle_configured,
    retention_offer_is_unused,
    retention_message_is_due,
)


def make_entitlement(
    now: datetime,
    *,
    status: EntitlementStatus = EntitlementStatus.inactive,
    kicked_hours_ago: int | None = 168,
    sent: bool = False,
    redeemed: int = 0,
) -> Entitlement:
    return Entitlement(
        user_id=1,
        product_key="club",
        status=status,
        active_until=now - timedelta(days=1),
        kicked_at=(
            now - timedelta(hours=kicked_hours_ago)
            if kicked_hours_ago is not None
            else None
        ),
        retention_message_sent_at=now if sent else None,
        retention_offers=redeemed,
    )


def test_lifecycle_requires_credentials_and_at_least_one_step() -> None:
    configured = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING_STOP="stop-step",
        BOTHELP_STEP_RETENTION_OFFER="",
        BOTHELP_STEP_RETENTION_USED="",
    )
    retention_only = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING_STOP="",
        BOTHELP_STEP_RETENTION_OFFER="retention-step",
        BOTHELP_STEP_RETENTION_USED="used-step",
    )
    missing_steps = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING_STOP="",
        BOTHELP_STEP_RETENTION_OFFER="",
        BOTHELP_STEP_RETENTION_USED="",
    )
    incomplete_retention = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING_STOP="",
        BOTHELP_STEP_RETENTION_OFFER="retention-step",
        BOTHELP_STEP_RETENTION_USED="",
    )

    assert is_club_lifecycle_configured(configured) is True
    assert is_club_lifecycle_configured(retention_only) is True
    assert is_club_lifecycle_configured(missing_steps) is False
    assert is_club_lifecycle_configured(incomplete_retention) is False


def test_retention_defaults_to_seven_days_and_safe_batches() -> None:
    settings = Settings.model_construct()

    assert settings.BOTHELP_RETENTION_DELAY_HOURS == 168
    assert settings.BOTHELP_RETENTION_REPEAT_HOURS == 72
    assert settings.BOTHELP_REVIEW_INTERVAL_SECONDS == 900
    assert settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS == 60
    assert settings.BOTHELP_REVIEW_BATCH_SIZE == 50


def test_retention_message_is_due_exactly_seven_days_after_kick() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    assert retention_message_is_due(make_entitlement(now), now, 168, 72) is True
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=167), now, 168, 72
        )
        is False
    )


def test_retention_message_repeats_every_three_days_until_activation() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    sent_recently = make_entitlement(now, kicked_hours_ago=240, sent=True)
    sent_recently.retention_message_sent_at = now - timedelta(hours=71)
    assert retention_message_is_due(sent_recently, now, 168, 72) is False

    sent_three_days_ago = make_entitlement(now, kicked_hours_ago=240, sent=True)
    sent_three_days_ago.retention_message_sent_at = now - timedelta(hours=72)
    assert retention_message_is_due(sent_three_days_ago, now, 168, 72) is True

    redeemed = make_entitlement(now, kicked_hours_ago=240, sent=True, redeemed=1)
    redeemed.retention_message_sent_at = now - timedelta(hours=72)
    assert retention_message_is_due(redeemed, now, 168, 72) is True

    active = make_entitlement(now, status=EntitlementStatus.active)
    active.active_until = now + timedelta(days=30)
    assert retention_message_is_due(active, now, 168, 72) is False
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=None), now, 168, 72
        )
        is False
    )


def test_historical_expiry_enters_retention_without_fake_kick_date() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=9)

    assert retention_message_is_due(entitlement, now, 168, 72, 86400) is True
    assert entitlement.kicked_at is None


def test_historical_expiry_waits_seven_days_after_kick_grace() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=7, hours=12)

    assert retention_message_is_due(entitlement, now, 168, 72, 86400) is False


def test_retention_invoice_only_checks_unused_discount() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    assert retention_offer_is_unused(None) is False
    assert retention_offer_is_unused(make_entitlement(now)) is True
    assert retention_offer_is_unused(make_entitlement(now, sent=True)) is True
    assert (
        retention_offer_is_unused(make_entitlement(now, sent=True, redeemed=1))
        is False
    )

    active = make_entitlement(now, status=EntitlementStatus.active, sent=True)
    active.active_until = now + timedelta(days=30)
    assert retention_offer_is_unused(active) is True


async def test_retention_step_marks_offer_sent_only_after_success() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=169)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_HOURS=72,
    )

    class FakeDB:
        async def flush(self) -> None:
            pass

    class FakeClient:
        async def trigger_bot_step(self, **kwargs) -> None:
            assert kwargs == {
                "bothelp_subscriber_id": 456,
                "bot_referral": "bot",
                "step_referral": "1789393024060",
            }

    assert (
        await _trigger_retention_message(
            FakeDB(), settings, FakeClient(), entitlement, user
        )
        is True
    )
    assert entitlement.retention_message_sent_at is not None


async def test_retention_step_is_retried_after_bothelp_failure() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=169)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_HOURS=72,
    )

    class FakeDB:
        async def flush(self) -> None:
            pass

    class FailingClient:
        async def trigger_bot_step(self, **_kwargs) -> None:
            raise httpx.ConnectError("BotHelp unavailable")

    assert (
        await _trigger_retention_message(
            FakeDB(), settings, FailingClient(), entitlement, user
        )
        is False
    )
    assert entitlement.retention_message_sent_at is None


async def test_redeemed_user_receives_regular_retention_step() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=169, redeemed=1)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_HOURS=72,
    )

    class FakeDB:
        async def flush(self) -> None:
            pass

    class FakeClient:
        async def trigger_bot_step(self, **kwargs) -> None:
            assert kwargs["step_referral"] == "1789471963809"

    assert (
        await _trigger_retention_message(
            FakeDB(), settings, FakeClient(), entitlement, user
        )
        is True
    )
