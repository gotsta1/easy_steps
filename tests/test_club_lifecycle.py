from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
from app.services.bothelp_club_lifecycle import (
    _trigger_retention_message,
    is_club_lifecycle_configured,
    random_retention_send_at,
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
    assert settings.BOTHELP_RETENTION_REPEAT_MIN_DAYS == 2
    assert settings.BOTHELP_RETENTION_REPEAT_MAX_DAYS == 4
    assert settings.BOTHELP_RETENTION_SEND_START_HOUR_MSK == 6
    assert settings.BOTHELP_RETENTION_SEND_END_HOUR_MSK == 24
    assert settings.BOTHELP_REVIEW_INTERVAL_SECONDS == 900
    assert settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS == 60
    assert settings.BOTHELP_REVIEW_BATCH_SIZE == 50


def test_retention_message_is_due_exactly_seven_days_after_kick() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    assert retention_message_is_due(make_entitlement(now), now, 168) is True
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=167), now, 168
        )
        is False
    )


def test_retention_message_uses_persisted_random_schedule_until_activation() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    scheduled_later = make_entitlement(now, kicked_hours_ago=240, sent=True)
    scheduled_later.retention_message_sent_at = now - timedelta(days=3)
    scheduled_later.retention_next_message_at = now + timedelta(seconds=1)
    assert retention_message_is_due(scheduled_later, now, 168) is False

    scheduled_now = make_entitlement(now, kicked_hours_ago=240, sent=True)
    scheduled_now.retention_message_sent_at = now - timedelta(days=3)
    scheduled_now.retention_next_message_at = now
    assert retention_message_is_due(scheduled_now, now, 168) is True

    redeemed = make_entitlement(now, kicked_hours_ago=240, sent=True, redeemed=1)
    redeemed.retention_message_sent_at = now - timedelta(days=3)
    redeemed.retention_next_message_at = now
    assert retention_message_is_due(redeemed, now, 168) is True

    active = make_entitlement(now, status=EntitlementStatus.active)
    active.active_until = now + timedelta(days=30)
    assert retention_message_is_due(active, now, 168) is False
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=None), now, 168
        )
        is False
    )


def test_historical_expiry_enters_retention_without_fake_kick_date() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=9)

    assert retention_message_is_due(entitlement, now, 168, 86400) is True
    assert entitlement.kicked_at is None


def test_historical_expiry_waits_seven_days_after_kick_grace() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=7, hours=12)

    assert retention_message_is_due(entitlement, now, 168, 86400) is False


def test_random_retention_schedule_is_two_to_four_days_and_moscow_daytime() -> None:
    base = datetime(2026, 9, 15, 20, 30, tzinfo=timezone.utc)
    base_moscow = base.astimezone(ZoneInfo("Europe/Moscow"))

    for seed in range(30):
        scheduled = random_retention_send_at(
            base,
            min_days=2,
            max_days=4,
            start_hour_msk=6,
            end_hour_msk=24,
            rng=random.Random(seed),
        ).astimezone(ZoneInfo("Europe/Moscow"))
        assert (scheduled.date() - base_moscow.date()).days in {2, 3, 4}
        assert 6 <= scheduled.hour < 24


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
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=2,
        BOTHELP_RETENTION_REPEAT_MAX_DAYS=4,
        BOTHELP_RETENTION_SEND_START_HOUR_MSK=6,
        BOTHELP_RETENTION_SEND_END_HOUR_MSK=24,
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
    assert entitlement.retention_next_message_at is not None


async def test_retention_step_is_retried_after_bothelp_failure() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=169)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=2,
        BOTHELP_RETENTION_REPEAT_MAX_DAYS=4,
        BOTHELP_RETENTION_SEND_START_HOUR_MSK=6,
        BOTHELP_RETENTION_SEND_END_HOUR_MSK=24,
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
    assert entitlement.retention_next_message_at is None


async def test_existing_retention_user_gets_future_schedule_without_immediate_send() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=480, sent=True)
    entitlement.retention_message_sent_at = now - timedelta(days=10)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=2,
        BOTHELP_RETENTION_REPEAT_MAX_DAYS=4,
        BOTHELP_RETENTION_SEND_START_HOUR_MSK=6,
        BOTHELP_RETENTION_SEND_END_HOUR_MSK=24,
        KICK_GRACE_SECONDS=0,
    )

    class FakeDB:
        async def flush(self) -> None:
            pass

    class UnexpectedClient:
        async def trigger_bot_step(self, **_kwargs) -> None:
            raise AssertionError("bootstrap must schedule rather than send")

    assert (
        await _trigger_retention_message(
            FakeDB(), settings, UnexpectedClient(), entitlement, user
        )
        is False
    )
    assert entitlement.retention_next_message_at is not None
    assert entitlement.retention_next_message_at > now


async def test_redeemed_user_receives_regular_retention_step() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=169, redeemed=1)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=168,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=2,
        BOTHELP_RETENTION_REPEAT_MAX_DAYS=4,
        BOTHELP_RETENTION_SEND_START_HOUR_MSK=6,
        BOTHELP_RETENTION_SEND_END_HOUR_MSK=24,
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
