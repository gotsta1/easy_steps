"""Durable delivery of paid Tanya invoices to Google Sheets."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import PendingInvoice
from app.db.repo import PendingInvoiceRepo
from app.db.session import AsyncSessionFactory
from app.services.google_sheets import append_sale

logger = logging.getLogger(__name__)

PLAN_PRICE_RUB: dict[str, float] = {
    "1w": 329,
    "1m": 1290,
    "3m": 3490,
    "6m": 6490,
}


def is_gsheet_configured(settings: Settings) -> bool:
    return bool(
        settings.GSHEET_CREDENTIALS_PATH
        and settings.GSHEET_SPREADSHEET_ID
        and settings.GSHEET_SHEET_NAME
    )


async def deliver_invoice(
    db: AsyncSession,
    settings: Settings,
    invoice: PendingInvoice,
) -> bool:
    """Attempt one delivery and persist its result in the current transaction."""
    repo = PendingInvoiceRepo(db)
    amount = (
        invoice.amount_rub
        if invoice.amount_rub is not None
        else PLAN_PRICE_RUB.get(invoice.plan, 0)
    )
    payment_time = invoice.paid_at or invoice.created_at

    try:
        await asyncio.to_thread(
            append_sale,
            credentials_path=settings.GSHEET_CREDENTIALS_PATH,
            spreadsheet_id=settings.GSHEET_SPREADSHEET_ID,
            sheet_name=settings.GSHEET_SHEET_NAME,
            invoice_id=invoice.lava_invoice_id,
            account=str(invoice.telegram_user_id),
            amount=amount,
            user_name=invoice.first_name or "",
            date_time=payment_time,
            cuid=invoice.cuid or "",
        )
    except Exception as exc:
        await repo.mark_gsheet_failed(invoice, str(exc))
        logger.exception(
            "gsheet_delivery_failed invoice_id=%s attempt=%d",
            invoice.lava_invoice_id,
            invoice.gsheet_attempts,
        )
        return False

    await repo.mark_gsheet_recorded(invoice)
    return True


async def run_delivery_batch(settings: Settings) -> int:
    """Deliver up to the configured batch size, committing each invoice."""
    delivered = 0
    for _ in range(settings.GSHEET_RETRY_BATCH_SIZE):
        async with AsyncSessionFactory() as db:
            repo = PendingInvoiceRepo(db)
            invoice = await repo.get_next_gsheet_pending()
            if invoice is None:
                return delivered

            succeeded = await deliver_invoice(db, settings, invoice)
            await db.commit()
            if not succeeded:
                # A shared Sheets outage would make every row fail. Stop this
                # cycle and retry after the configured delay instead.
                return delivered
            delivered += 1
    return delivered


async def delivery_loop(settings: Settings) -> None:
    """Process pending rows immediately on startup, then periodically."""
    while True:
        try:
            delivered = await run_delivery_batch(settings)
            if delivered:
                logger.info("gsheet_delivery_batch_complete delivered=%d", delivered)
        except Exception:
            logger.exception("gsheet_delivery_job_error")
        await asyncio.sleep(settings.GSHEET_RETRY_INTERVAL_SECONDS)
