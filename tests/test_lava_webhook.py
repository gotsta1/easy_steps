from __future__ import annotations

from datetime import datetime, timezone

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


@pytest.mark.parametrize(
    "referral,expected_export",
    [("ref_ref", True), ("tanya", False), (None, False)],
)
async def test_successful_webhook_exports_only_configured_referral(monkeypatch, referral, expected_export):
    from app.api.routes import lava_webhook as route
    from app.services import gsheet_delivery

    pending = SimpleNamespace(telegram_user_id=123, offer_id="offer-menu", plan="menu", ref=referral)
    events = AsyncMock()
    events.exists.return_value = False
    invoices = AsyncMock()
    invoices.get_by_contract_id.return_value = pending
    monkeypatch.setattr(route, "LavaEventRepo", lambda db: events)
    monkeypatch.setattr(route, "PendingInvoiceRepo", lambda db: invoices)
    monkeypatch.setattr(route, "verify_lava_basic_auth", AsyncMock(return_value=True))
    monkeypatch.setattr(route.lava_svc, "extract_event_type", lambda p: "payment.success")
    monkeypatch.setattr(route.lava_svc, "extract_event_id", lambda p: "event-test")
    monkeypatch.setattr(route.lava_svc, "classify_event", lambda t: "payment_success")
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(gsheet_delivery, "deliver_invoice", delivery)
    settings = SimpleNamespace(
        LAVA_WEBHOOK_LOGIN="test", LAVA_WEBHOOK_PASSWORD="test", lava_product_map={},
        LAVA_OFFER_MENU="offer-menu", GSHEET_REF="ref_ref", GSHEET_CREDENTIALS_PATH="test",
        GSHEET_SPREADSHEET_ID="test", GSHEET_SHEET_NAME="Sales",
    )
    request = SimpleNamespace(json=AsyncMock(return_value={"contractId": "invoice-test"}))
    result = await route.lava_webhook_handler(request, settings, AsyncMock(), AsyncMock())

    assert result == {"status": "ok"}
    invoices.mark_paid.assert_awaited_once()
    assert delivery.await_count == int(expected_export)
