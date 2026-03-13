"""
Lava.top API client for creating payment invoices.

Uses POST /api/v3/invoice to generate a payment URL that triggers webhooks
upon completion (unlike manual product links from the dashboard).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

LAVA_GATE_BASE = "https://gate.lava.top"


@dataclass(frozen=True)
class InvoiceResult:
    invoice_id: str
    payment_url: str
    status: str


class LavaAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Lava API error {status_code}: {detail}")


PROVIDER_FOR_METHOD: dict[str, str] = {
    "SBP": "PAY2ME",
    "PAYPAL": "PAYPAL",
    "STRIPE": "STRIPE",
}

CARD_PROVIDER_FOR_CURRENCY: dict[str, str] = {
    "RUB": "SMART_GLOCAL",
    "USD": "STRIPE",
    "EUR": "STRIPE",
}

PROVIDER_FOR_CURRENCY: dict[str, str] = {
    "USD": "STRIPE",
    "EUR": "STRIPE",
}


async def create_invoice(
    api_key: str,
    email: str,
    offer_id: str,
    currency: str = "RUB",
    payment_method: str | None = None,
) -> InvoiceResult:
    """
    Create a one-time payment invoice via Lava API.

    Returns an InvoiceResult with the invoice ID and payment URL.
    """
    url = f"{LAVA_GATE_BASE}/api/v3/invoice"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body: dict[str, str] = {
        "email": email,
        "offerId": offer_id,
        "currency": currency,
    }
    if payment_method:
        body["paymentMethod"] = payment_method
        if payment_method == "CARD":
            provider = CARD_PROVIDER_FOR_CURRENCY.get(currency)
        else:
            provider = PROVIDER_FOR_METHOD.get(payment_method)
        if provider:
            body["paymentProvider"] = provider
    elif currency in PROVIDER_FOR_CURRENCY:
        body["paymentProvider"] = PROVIDER_FOR_CURRENCY[currency]

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=body, headers=headers)

    if resp.status_code not in (200, 201):
        logger.error(
            "lava_api_error status=%d body=%s",
            resp.status_code,
            resp.text[:500],
        )
        raise LavaAPIError(resp.status_code, resp.text[:500])

    data = resp.json()
    invoice_id = data.get("id", "")
    payment_url = data.get("paymentUrl", "")
    status = data.get("status", "unknown")

    logger.info(
        "lava_invoice_created id=%s status=%s url_prefix=%s",
        invoice_id,
        status,
        payment_url[:60] if payment_url else "none",
    )
    return InvoiceResult(
        invoice_id=str(invoice_id),
        payment_url=payment_url,
        status=status,
    )
