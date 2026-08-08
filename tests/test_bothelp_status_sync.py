from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
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
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_SUBSCRIPTION_SYNC="step",
    )
    missing_step = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_SUBSCRIPTION_SYNC="",
    )

    assert is_bothelp_status_sync_configured(configured) is True
    assert is_bothelp_status_sync_configured(missing_step) is False
