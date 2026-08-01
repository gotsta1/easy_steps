from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.routes.subscriptions import build_subscription_status
from app.db.models import Entitlement, EntitlementStatus

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def make_entitlement(
    product_key: str,
    status: EntitlementStatus = EntitlementStatus.active,
    active_until: datetime | None = None,
) -> Entitlement:
    return Entitlement(
        user_id=1,
        product_key=product_key,
        status=status,
        active_until=active_until,
    )


def test_no_purchases() -> None:
    response = build_subscription_status(None, None, now=NOW)

    assert response.club == "0"
    assert response.menu == "False"


def test_club_days_are_rounded_up() -> None:
    club = make_entitlement(
        "club",
        active_until=NOW + timedelta(days=2, hours=1),
    )

    response = build_subscription_status(club, None, now=NOW)

    assert response.club == "3"
    assert response.menu == "False"


def test_club_with_less_than_one_day_returns_one() -> None:
    club = make_entitlement("club", active_until=NOW + timedelta(minutes=1))

    response = build_subscription_status(club, None, now=NOW)

    assert response.club == "1"


def test_active_club_without_expiry_returns_lifetime() -> None:
    club = make_entitlement("club")

    response = build_subscription_status(club, None, now=NOW)

    assert response.club == "Бессрочно"


def test_expired_or_inactive_club_returns_zero() -> None:
    expired = make_entitlement("club", active_until=NOW - timedelta(seconds=1))
    inactive = make_entitlement(
        "club",
        status=EntitlementStatus.inactive,
        active_until=NOW + timedelta(days=10),
    )

    assert build_subscription_status(expired, None, now=NOW).club == "0"
    assert build_subscription_status(inactive, None, now=NOW).club == "0"


def test_active_lifetime_menu_returns_true() -> None:
    menu = make_entitlement("menu")

    response = build_subscription_status(None, menu, now=NOW)

    assert response.menu == "True"


def test_inactive_menu_returns_false() -> None:
    menu = make_entitlement("menu", status=EntitlementStatus.inactive)

    response = build_subscription_status(None, menu, now=NOW)

    assert response.menu == "False"
