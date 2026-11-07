from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
from app.services import bothelp_club_lifecycle as lifecycle
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
    kicked_hours_ago: int | None = 288,
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
        retention_next_message_index=0,
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
        BOTHELP_STEP_RETENTION_OFFER_2="retention-step-2",
        BOTHELP_STEP_RETENTION_OFFER_3="retention-step-3",
        BOTHELP_STEP_RETENTION_USED="used-step",
        BOTHELP_STEP_RETENTION_USED_2="used-step-2",
        BOTHELP_STEP_RETENTION_USED_3="used-step-3",
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


def test_retention_defaults_to_twelve_days_and_safe_batches() -> None:
    settings = Settings.model_construct()

    assert settings.BOTHELP_RETENTION_DELAY_HOURS == 288
    assert settings.BOTHELP_RETENTION_REPEAT_MIN_DAYS == 3
    assert settings.BOTHELP_RETENTION_REPEAT_MAX_DAYS == 4
    assert settings.BOTHELP_RETENTION_SEND_START_HOUR_MSK == 6
    assert settings.BOTHELP_RETENTION_SEND_END_HOUR_MSK == 24
    assert settings.BOTHELP_REVIEW_INTERVAL_SECONDS == 900
    assert settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS == 60
    assert settings.BOTHELP_REVIEW_BATCH_SIZE == 50


def test_retention_message_is_due_exactly_twelve_days_after_kick() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    assert retention_message_is_due(make_entitlement(now), now, 288) is True
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=287), now, 288
        )
        is False
    )
    almost_due = make_entitlement(now)
    almost_due.kicked_at += timedelta(microseconds=1)
    assert retention_message_is_due(almost_due, now, 288) is False


@pytest.mark.parametrize("elapsed_days", [7, 10, 11])
def test_retention_is_not_due_under_the_old_seven_day_cutoff(elapsed_days) -> None:
    now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=elapsed_days * 24)

    assert retention_message_is_due(entitlement, now, 288) is False


def test_retention_message_uses_persisted_random_schedule_until_activation() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)

    scheduled_later = make_entitlement(now, kicked_hours_ago=480, sent=True)
    scheduled_later.retention_message_sent_at = now - timedelta(days=3)
    scheduled_later.retention_next_message_at = now + timedelta(seconds=1)
    assert retention_message_is_due(scheduled_later, now, 288) is False

    scheduled_now = make_entitlement(now, kicked_hours_ago=480, sent=True)
    scheduled_now.retention_message_sent_at = now - timedelta(days=3)
    scheduled_now.retention_next_message_at = now
    assert retention_message_is_due(scheduled_now, now, 288) is True

    redeemed = make_entitlement(now, kicked_hours_ago=480, sent=True, redeemed=1)
    redeemed.retention_message_sent_at = now - timedelta(days=3)
    redeemed.retention_next_message_at = now
    assert retention_message_is_due(redeemed, now, 288) is True

    active = make_entitlement(now, status=EntitlementStatus.active)
    active.active_until = now + timedelta(days=30)
    assert retention_message_is_due(active, now, 288) is False
    assert (
        retention_message_is_due(
            make_entitlement(now, kicked_hours_ago=None), now, 288
        )
        is False
    )


def test_historical_expiry_enters_retention_without_fake_kick_date() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=14)

    assert retention_message_is_due(entitlement, now, 288, 86400) is True
    assert entitlement.kicked_at is None


def test_historical_expiry_waits_twelve_days_after_kick_grace() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=None)
    entitlement.active_until = now - timedelta(days=12, hours=12)

    assert retention_message_is_due(entitlement, now, 288, 86400) is False


def test_random_retention_schedule_is_three_to_four_days_and_moscow_daytime() -> None:
    base = datetime(2026, 9, 15, 20, 30, tzinfo=timezone.utc)
    base_moscow = base.astimezone(ZoneInfo("Europe/Moscow"))

    observed_days = set()
    for seed in range(30):
        scheduled = random_retention_send_at(
            base,
            min_days=3,
            max_days=4,
            start_hour_msk=6,
            end_hour_msk=24,
            rng=random.Random(seed),
        ).astimezone(ZoneInfo("Europe/Moscow"))
        elapsed_days = (scheduled.date() - base_moscow.date()).days
        observed_days.add(elapsed_days)
        assert elapsed_days in {3, 4}
        assert 6 <= scheduled.hour < 24
    assert observed_days == {3, 4}


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
    entitlement = make_entitlement(now, kicked_hours_ago=289)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=288,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=3,
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
    assert entitlement.retention_next_message_index == 1


async def test_retention_step_is_retried_after_bothelp_failure() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=289)
    entitlement.retention_next_message_index = 1
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=288,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=3,
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
    assert entitlement.retention_next_message_index == 1


async def test_existing_retention_user_gets_future_schedule_without_immediate_send() -> None:
    now = datetime.now(timezone.utc)
    entitlement = make_entitlement(now, kicked_hours_ago=480, sent=True)
    entitlement.retention_message_sent_at = now - timedelta(days=10)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=288,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=3,
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
    entitlement = make_entitlement(now, kicked_hours_ago=289, redeemed=1)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="1789393024060",
        BOTHELP_STEP_RETENTION_USED="1789471963809",
        BOTHELP_RETENTION_DELAY_HOURS=288,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=3,
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


async def test_retention_messages_rotate_per_branch_and_restart_after_new_kick(
    monkeypatch,
) -> None:
    now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(lifecycle, "utcnow", lambda: now)
    entitlement = make_entitlement(now, kicked_hours_ago=480)
    user = type("User", (), {"telegram_user_id": 123, "bothelp_subscriber_id": 456})()
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="offer-1",
        BOTHELP_STEP_RETENTION_OFFER_2="offer-2",
        BOTHELP_STEP_RETENTION_OFFER_3="offer-3",
        BOTHELP_STEP_RETENTION_USED="used-1",
        BOTHELP_STEP_RETENTION_USED_2="used-2",
        BOTHELP_STEP_RETENTION_USED_3="used-3",
        BOTHELP_RETENTION_DELAY_HOURS=288,
        BOTHELP_RETENTION_REPEAT_MIN_DAYS=3,
        BOTHELP_RETENTION_REPEAT_MAX_DAYS=4,
        BOTHELP_RETENTION_SEND_START_HOUR_MSK=6,
        BOTHELP_RETENTION_SEND_END_HOUR_MSK=24,
    )

    class FakeDB:
        async def flush(self) -> None:
            pass

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def trigger_bot_step(self, **kwargs) -> None:
            self.sent.append(kwargs["step_referral"])

    client = FakeClient()
    for expected_next_index in (1, 2, 0, 1):
        if entitlement.retention_message_sent_at is not None:
            entitlement.retention_message_sent_at = now - timedelta(days=3)
        entitlement.retention_next_message_at = now - timedelta(seconds=1)
        assert await _trigger_retention_message(FakeDB(), settings, client, entitlement, user)
        assert entitlement.retention_next_message_index == expected_next_index

    assert client.sent == ["offer-1", "offer-2", "offer-3", "offer-1"]

    # A redeemed offer reactivates the club; the next expired cycle starts at step one.
    from app.main import _mark_entitlement_kicked

    entitlement.status = EntitlementStatus.active
    entitlement.retention_offers = 1
    entitlement.active_until = now - timedelta(days=20)
    entitlement.retention_message_sent_at = now - timedelta(days=20)
    _mark_entitlement_kicked(entitlement, now - timedelta(days=13))
    assert entitlement.retention_next_message_index == 0
    assert await _trigger_retention_message(FakeDB(), settings, client, entitlement, user)
    assert client.sent[-1] == "used-1"


@pytest.mark.parametrize("redeemed", [0, 1])
async def test_old_two_day_repeat_is_rescheduled_without_sending(monkeypatch, redeemed):
    now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(lifecycle, "utcnow", lambda: now)
    entitlement = make_entitlement(now, kicked_hours_ago=480, redeemed=redeemed)
    last_sent_at = now - timedelta(days=2)
    entitlement.retention_message_sent_at = last_sent_at
    entitlement.retention_next_message_at = now
    entitlement.retention_next_message_index = 2
    db = SimpleNamespace(flush=AsyncMock())
    client = SimpleNamespace(trigger_bot_step=AsyncMock())
    user = SimpleNamespace(telegram_user_id=123, bothelp_subscriber_id=456)

    sent = await _trigger_retention_message(db, Settings.model_construct(), client, entitlement, user)

    assert sent is False
    client.trigger_bot_step.assert_not_awaited()
    db.flush.assert_awaited_once()
    assert entitlement.retention_message_sent_at == last_sent_at
    assert entitlement.retention_next_message_index == 2
    next_at = entitlement.retention_next_message_at.astimezone(lifecycle.MOSCOW_TZ)
    assert (next_at.date() - last_sent_at.astimezone(lifecycle.MOSCOW_TZ).date()).days in {3, 4}
    assert 6 <= next_at.hour < 24
    assert entitlement.retention_next_message_at > now


@pytest.mark.parametrize("hour_utc, expected_send", [(2, False), (3, True), (12, True)])
async def test_old_overdue_schedule_respects_new_minimum_moscow_time(
    monkeypatch, hour_utc, expected_send,
):
    now = datetime(2026, 11, 7, hour_utc, tzinfo=timezone.utc)
    monkeypatch.setattr(lifecycle, "utcnow", lambda: now)
    entitlement = make_entitlement(now, kicked_hours_ago=480)
    entitlement.retention_message_sent_at = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    entitlement.retention_next_message_at = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    db = SimpleNamespace(flush=AsyncMock())
    client = SimpleNamespace(trigger_bot_step=AsyncMock())
    user = SimpleNamespace(telegram_user_id=123, bothelp_subscriber_id=456)
    settings = Settings.model_construct(
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_RETENTION_OFFER="offer-1",
    )

    sent = await _trigger_retention_message(db, settings, client, entitlement, user)

    assert sent is expected_send
    assert client.trigger_bot_step.await_count == int(expected_send)
