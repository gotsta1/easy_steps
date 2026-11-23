from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import PendingInvoice
from app.db.repo import PendingInvoiceRepo
from app.services import gsheet_delivery


def make_invoice() -> PendingInvoice:
    return PendingInvoice(
        lava_invoice_id="invoice-1",
        telegram_user_id=1463889608,
        offer_id="offer-1w",
        plan="1w",
        payment_url="https://example.com/pay",
        paid=True,
        paid_at=datetime(2026, 7, 31, 12, 21, 32, tzinfo=timezone.utc),
        amount_rub=329,
        first_name="Олеся",
        cuid="cmoi.aj9",
        ref="ref_ref",
        gsheet_attempts=0,
    )


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        GSHEET_CREDENTIALS_PATH="credentials.json",
        GSHEET_SPREADSHEET_ID="spreadsheet",
        GSHEET_SHEET_NAME="Sales",
        GSHEET_REF="ref_ref",
        GSHEET_RETRY_BATCH_SIZE=50,
    )


def test_sheets_referral_defaults_to_current_source():
    assert Settings.model_construct().GSHEET_REF == "ref_ref"


@pytest.mark.parametrize("referral", ["ref_ref", "tanya"])
async def test_delivery_query_filters_paid_undelivered_matching_referral(referral):
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    await PendingInvoiceRepo(db).get_next_gsheet_pending(referral)
    query = db.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert f"pending_invoices.ref = '{referral}'" in sql
    assert "pending_invoices.paid IS true" in sql
    assert "pending_invoices.gsheet_recorded_at IS NULL" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


async def test_worker_passes_configured_referral(monkeypatch):
    db = AsyncMock()
    db.__aenter__.return_value = db
    monkeypatch.setattr(gsheet_delivery, "AsyncSessionFactory", lambda: db)
    query = AsyncMock(return_value=None)
    monkeypatch.setattr(PendingInvoiceRepo, "get_next_gsheet_pending", query)

    assert await gsheet_delivery.run_delivery_batch(settings()) == 0
    query.assert_awaited_once_with("ref_ref")


def test_blank_referral_disables_sheets_delivery():
    configured = settings()
    assert gsheet_delivery.is_gsheet_configured(configured) is True
    configured.GSHEET_REF = ""
    assert gsheet_delivery.is_gsheet_configured(configured) is False


async def test_deliver_invoice_marks_success(monkeypatch) -> None:
    invoice = make_invoice()
    captured: dict = {}

    class FakeRepo:
        def __init__(self, _db) -> None:
            pass

        async def mark_gsheet_recorded(self, inv: PendingInvoice) -> None:
            inv.gsheet_recorded_at = datetime.now(timezone.utc)
            inv.gsheet_attempts += 1
            inv.gsheet_last_error = None

        async def mark_gsheet_failed(self, *_args) -> None:
            raise AssertionError("failure must not be recorded")

    async def fake_to_thread(func, **kwargs):
        captured.update(kwargs)
        return func

    monkeypatch.setattr(gsheet_delivery, "PendingInvoiceRepo", FakeRepo)
    monkeypatch.setattr(gsheet_delivery.asyncio, "to_thread", fake_to_thread)

    succeeded = await gsheet_delivery.deliver_invoice(object(), settings(), invoice)

    assert succeeded is True
    assert invoice.gsheet_recorded_at is not None
    assert invoice.gsheet_attempts == 1
    assert captured["invoice_id"] == "invoice-1"
    assert captured["amount"] == 329
    assert captured["date_time"] == invoice.paid_at


async def test_deliver_invoice_records_failure(monkeypatch) -> None:
    invoice = make_invoice()

    class FakeRepo:
        def __init__(self, _db) -> None:
            pass

        async def mark_gsheet_recorded(self, *_args) -> None:
            raise AssertionError("success must not be recorded")

        async def mark_gsheet_failed(
            self,
            inv: PendingInvoice,
            error: str,
        ) -> None:
            inv.gsheet_attempts += 1
            inv.gsheet_last_error = error

    async def failing_to_thread(*_args, **_kwargs):
        raise RuntimeError("Google unavailable")

    monkeypatch.setattr(gsheet_delivery, "PendingInvoiceRepo", FakeRepo)
    monkeypatch.setattr(gsheet_delivery.asyncio, "to_thread", failing_to_thread)

    succeeded = await gsheet_delivery.deliver_invoice(object(), settings(), invoice)

    assert succeeded is False
    assert invoice.gsheet_attempts == 1
    assert invoice.gsheet_last_error == "Google unavailable"


@pytest.mark.parametrize("payload,expected_amount", [
    ({"currency": "RUB", "amount": "1390"}, 1390),
    ({"currency": "USD", "amount": "16.96"}, None),
    (None, None), ({"currency": "RUB", "amount": "bad"}, None),
])
async def test_legacy_missing_amount_uses_only_actual_rub_event_not_today_price(
    monkeypatch, payload, expected_amount,
):
    invoice = make_invoice()
    invoice.amount_rub = None
    invoice.plan = "1m"
    repo = SimpleNamespace(mark_gsheet_recorded=AsyncMock(), mark_gsheet_failed=AsyncMock())
    events = SimpleNamespace(latest_success_payload=AsyncMock(return_value=payload))
    write = AsyncMock()
    monkeypatch.setattr(gsheet_delivery, "PendingInvoiceRepo", lambda _: repo)
    monkeypatch.setattr(gsheet_delivery, "LavaEventRepo", lambda _: events)
    monkeypatch.setattr(gsheet_delivery.asyncio, "to_thread", write)
    succeeded = await gsheet_delivery.deliver_invoice(object(), settings(), invoice)
    assert succeeded is (expected_amount is not None)
    if expected_amount is None:
        write.assert_not_awaited()
        repo.mark_gsheet_failed.assert_awaited_once()
        assert "missing_rub_price_snapshot" in repo.mark_gsheet_failed.await_args.args[1]
    else:
        assert invoice.amount_rub == expected_amount
        assert write.await_args.kwargs["amount"] == expected_amount


async def test_frozen_foreign_price_is_exported_without_current_catalog_lookup(monkeypatch):
    invoice = make_invoice()
    invoice.plan = "1m"
    invoice.amount_rub = 1390
    repo = SimpleNamespace(mark_gsheet_recorded=AsyncMock())
    write = AsyncMock()
    def no_event_lookup(_):
        raise AssertionError("A frozen price must not be recalculated")
    monkeypatch.setattr(gsheet_delivery, "PendingInvoiceRepo", lambda _: repo)
    monkeypatch.setattr(gsheet_delivery, "LavaEventRepo", no_event_lookup)
    monkeypatch.setattr(gsheet_delivery.asyncio, "to_thread", write)
    assert await gsheet_delivery.deliver_invoice(object(), settings(), invoice)
    assert write.await_args.kwargs["amount"] == 1390


async def test_legacy_price_lookup_is_invoice_scoped_and_success_only():
    from app.db.repo import LavaEventRepo
    db = AsyncMock()
    await LavaEventRepo(db).latest_success_payload("invoice-123")
    sql = str(db.scalar.await_args.args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "contractId" in sql and "invoice-123" in sql
    assert "payment.success" in sql and "payment.failed" not in sql
