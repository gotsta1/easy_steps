"""
Unit tests for the entitlement decision function.

can_approve_join is a pure function — no DB, no network, no async.
Tests should run instantly with: pytest tests/test_entitlements.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Entitlement, EntitlementStatus
from app.services.entitlements import can_approve_join

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(hours=1)
PAST = NOW - timedelta(hours=1)


def make_entitlement(
    status: EntitlementStatus = EntitlementStatus.active,
    active_until: datetime | None = None,
    allowed_to_join_until: datetime | None = None,
) -> Entitlement:
    """Construct a bare Entitlement ORM object without touching the DB."""
    return Entitlement(
        status=status,
        active_until=active_until,
        allowed_to_join_until=allowed_to_join_until,
        product_key="club_monthly",
        user_id=0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# No entitlement
# ─────────────────────────────────────────────────────────────────────────────


def test_no_entitlement_is_declined():
    approved, reason = can_approve_join(None, now=NOW)
    assert not approved
    assert reason == "no_entitlement"


# ─────────────────────────────────────────────────────────────────────────────
# Non-active statuses
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_status",
    [EntitlementStatus.inactive, EntitlementStatus.past_due, EntitlementStatus.canceled],
)
def test_non_active_status_is_declined(bad_status: EntitlementStatus):
    ent = make_entitlement(status=bad_status)
    approved, reason = can_approve_join(ent, now=NOW)
    assert not approved
    assert bad_status.value in reason


# ─────────────────────────────────────────────────────────────────────────────
# Active status — no expiry fields set
# ─────────────────────────────────────────────────────────────────────────────


def test_active_no_expiry_is_approved():
    """Active entitlement with no date limits should always be approved."""
    ent = make_entitlement(status=EntitlementStatus.active)
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Subscription expiry (active_until)
# ─────────────────────────────────────────────────────────────────────────────


def test_active_until_in_future_is_approved():
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=FUTURE,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


def test_active_until_in_past_is_declined():
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=PAST,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert not approved
    assert reason == "subscription_expired"


def test_active_until_exactly_now_is_still_valid():
    """Boundary: active_until == now → still valid (we use strict >)."""
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=NOW,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Join window (allowed_to_join_until)
# ─────────────────────────────────────────────────────────────────────────────


def test_join_window_in_future_is_approved():
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=FUTURE,
        allowed_to_join_until=FUTURE,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


def test_join_window_expired_is_declined():
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=FUTURE,
        allowed_to_join_until=PAST,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert not approved
    assert reason == "join_window_expired"


def test_join_window_none_with_future_active_until_is_approved():
    """No join-window restriction → only subscription validity matters."""
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=FUTURE,
        allowed_to_join_until=None,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


def test_join_window_expired_overrides_valid_subscription():
    """Expired join window blocks even when subscription is valid."""
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=FUTURE,
        allowed_to_join_until=PAST,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert not approved
    assert reason == "join_window_expired"


# ─────────────────────────────────────────────────────────────────────────────
# Timezone handling
# ─────────────────────────────────────────────────────────────────────────────


def test_naive_datetime_is_treated_as_utc():
    """Naive datetimes stored in the DB should be treated as UTC."""
    naive_future = NOW.replace(tzinfo=None) + timedelta(hours=1)
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=naive_future,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert approved
    assert reason == "ok"


def test_naive_past_datetime_is_expired():
    naive_past = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    ent = make_entitlement(
        status=EntitlementStatus.active,
        active_until=naive_past,
    )
    approved, reason = can_approve_join(ent, now=NOW)
    assert not approved
    assert reason == "subscription_expired"


# ─────────────────────────────────────────────────────────────────────────────
# Default `now` uses real clock (smoke test — just checks it doesn't raise)
# ─────────────────────────────────────────────────────────────────────────────


def test_default_now_does_not_raise():
    ent = make_entitlement(status=EntitlementStatus.active)
    approved, reason = can_approve_join(ent)  # no explicit `now`
    assert isinstance(approved, bool)
    assert isinstance(reason, str)
