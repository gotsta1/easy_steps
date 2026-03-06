"""
Payment redirect endpoint.

GET /pay/{invoice_id} → 302 redirect to Lava payment URL.

BotHelp URL buttons require an explicit domain, and URL-encode path variables.
Since Lava payment URLs contain slashes and query params, we use a simple
redirect: BotHelp links to /pay/{invoice_id} (a clean UUID), and we resolve
the full Lava URL from pending_invoices and redirect.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Depends

from app.db.models import PendingInvoice
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pay"])


@router.get("/pay/{invoice_id}")
async def pay_redirect(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    result = await db.execute(
        select(PendingInvoice).where(PendingInvoice.lava_invoice_id == invoice_id)
    )
    invoice = result.scalar_one_or_none()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    logger.info("pay_redirect invoice=%s tg=%d", invoice_id, invoice.telegram_user_id)
    return RedirectResponse(url=invoice.payment_url, status_code=302)
