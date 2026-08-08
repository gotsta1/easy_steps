from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
from app.services.bothelp_review_mailing import (
    ENROLLED,
    STOPPED,
    desired_review_mailing_state,
    is_review_mailing_configured,
)


def test_review_mailing_requires_complete_bothelp_configuration() -> None:
    configured = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING="step",
        BOTHELP_STEP_REVIEW_MAILING_STOP="stop-step",
    )
    missing_step = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING="",
        BOTHELP_STEP_REVIEW_MAILING_STOP="stop-step",
    )
    missing_stop_step = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING="step",
        BOTHELP_STEP_REVIEW_MAILING_STOP="",
    )

    assert is_review_mailing_configured(configured) is True
    assert is_review_mailing_configured(missing_step) is False
    assert is_review_mailing_configured(missing_stop_step) is False


def test_review_mailing_defaults_to_five_days_and_safe_batches() -> None:
    settings = Settings.model_construct()

    assert settings.BOTHELP_REVIEW_DELAY_HOURS == 120
    assert settings.BOTHELP_REVIEW_INTERVAL_SECONDS == 900
    assert settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS == 60
    assert settings.BOTHELP_REVIEW_BATCH_SIZE == 50


def test_active_club_must_be_removed_from_review_mailing() -> None:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    entitlement = Entitlement(
        user_id=1,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=now + timedelta(days=15),
    )

    assert desired_review_mailing_state(entitlement, now, 120) == STOPPED


def test_lifetime_club_must_be_removed_from_review_mailing() -> None:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    entitlement = Entitlement(
        user_id=1,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=None,
    )

    assert desired_review_mailing_state(entitlement, now, 120) == STOPPED


def test_expired_club_is_enrolled_only_after_five_days() -> None:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    recently_expired = Entitlement(
        user_id=1,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=now - timedelta(hours=119),
    )
    eligible = Entitlement(
        user_id=2,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=now - timedelta(hours=120),
    )

    assert desired_review_mailing_state(recently_expired, now, 120) is None
    assert desired_review_mailing_state(eligible, now, 120) == ENROLLED
