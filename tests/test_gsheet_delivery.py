from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.db.models import PendingInvoice
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
        ref="tanya",
        gsheet_attempts=0,
    )


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        GSHEET_CREDENTIALS_PATH="credentials.json",
        GSHEET_SPREADSHEET_ID="spreadsheet",
        GSHEET_SHEET_NAME="Sales",
    )


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
