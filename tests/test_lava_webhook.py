from __future__ import annotations

from datetime import datetime, timezone

from types import SimpleNamespace

from app.api.routes.lava_webhook import (
    _extract_payment_time,
    _mark_retention_offer_redeemed,
)


def test_extract_payment_time_uses_lava_timestamp() -> None:
    result = _extract_payment_time({"timestamp": "2026-07-31T12:21:32.585825Z"})

    assert result == datetime(2026, 7, 31, 12, 21, 32, 585825, tzinfo=timezone.utc)


def test_retention_payment_marks_offer_redeemed() -> None:
    entitlement = SimpleNamespace(retention_offers=0)
    pending = SimpleNamespace(plan="retention_1m")

    assert _mark_retention_offer_redeemed(entitlement, pending) is True
    assert entitlement.retention_offers == 1


def test_regular_payment_does_not_mark_offer_redeemed() -> None:
    entitlement = SimpleNamespace(retention_offers=0)
    pending = SimpleNamespace(plan="1m")

    assert _mark_retention_offer_redeemed(entitlement, pending) is False
    assert entitlement.retention_offers == 0
