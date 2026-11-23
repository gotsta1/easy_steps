"""
Lava.top API client for creating payment invoices.

Uses POST /api/v3/invoice to generate a payment URL that triggers webhooks
upon completion (unlike manual product links from the dashboard).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlparse

import httpx

logger = logging.getLogger(__name__)

LAVA_GATE_BASE = "https://gate.lava.top"


@dataclass(frozen=True)
class InvoiceResult:
    invoice_id: str
    payment_url: str
    status: str
    amount: float | None = None
    currency: str | None = None


class LavaAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Lava API error {status_code}: {detail}")


def money_amount(value) -> float:
    """Validate vendor amounts without replacing missing or invalid data with zero."""
    if value is None or isinstance(value, bool):
        raise ValueError("invalid_money_amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_money_amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid_money_amount")
    result = float(amount)
    if not Decimal(str(result)).is_finite():
        raise ValueError("invalid_money_amount")
    return result


def paid_amount_rub(payload: dict) -> float | None:
    """Only a RUB payment supplies an actual RUB amount; never infer an FX rate."""
    if str(payload.get("currency", "")).upper() != "RUB":
        return None
    try:
        return money_amount(payload.get("amount"))
    except ValueError:
        return None


async def get_offer_price_rub(api_key: str, offer_id: str, *, http_client=None) -> float:
    """Read the exact offer's current RUB price, including hidden products.

    Deliberately uncached: a price edit applies to the next invoice, while the
    caller persists each quote so existing purchases are never revalued.
    """
    client = http_client or httpx.AsyncClient(timeout=15)
    params = {"feedVisibility": "ALL", "showAllSubscriptionPeriods": "true"}
    seen = set()
    try:
        for _ in range(100):
            cursor = params.get("beforeCreatedAt")
            if cursor in seen:
                raise LavaAPIError(502, "non_advancing_product_cursor")
            seen.add(cursor)
            try:
                response = await client.get(
                    LAVA_GATE_BASE + "/api/v2/products",
                    headers={"X-Api-Key": api_key, "Accept": "application/json"},
                    params=params,
                )
            except httpx.HTTPError as exc:
                raise LavaAPIError(502, "product_catalog_unavailable") from exc
            if response.status_code != 200:
                raise LavaAPIError(response.status_code, "product_catalog_unavailable")
            try:
                data = response.json()
            except ValueError as exc:
                raise LavaAPIError(502, "invalid_product_catalog") from exc
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise LavaAPIError(502, "invalid_product_catalog")
            matching = []
            for item in data["items"]:
                # The live API also returns direct product objects, unlike the
                # type/data wrapper described by Swagger.
                product = item.get("data", item) if isinstance(item, dict) else None
                if not isinstance(product, dict):
                    continue
                matching.extend(offer for offer in product.get("offers") or []
                                if isinstance(offer, dict) and offer.get("id") == offer_id)
            if matching:
                if len(matching) != 1:
                    raise LavaAPIError(502, "ambiguous_offer_price")
                prices = [price for price in matching[0].get("prices") or []
                          if isinstance(price, dict) and price.get("currency") == "RUB"]
                if len(prices) != 1:
                    raise LavaAPIError(502, "missing_or_ambiguous_rub_price")
                try:
                    return money_amount(prices[0].get("amount"))
                except ValueError as exc:
                    raise LavaAPIError(502, "invalid_rub_offer_price") from exc
            next_page = data.get("nextPage")
            if not next_page:
                raise LavaAPIError(404, "offer_not_found_in_catalog")
            if not isinstance(next_page, str):
                raise LavaAPIError(502, "invalid_product_cursor")
            parsed = urlparse(next_page)
            if (parsed.netloc and parsed.netloc != "gate.lava.top") or parsed.path != "/api/v2/products":
                raise LavaAPIError(502, "invalid_product_cursor")
            # Never follow a provider-supplied URL with our API key. Extract only its cursor.
            cursor = dict(parse_qsl(parsed.query)).get("beforeCreatedAt")
            if not cursor:
                raise LavaAPIError(502, "invalid_product_cursor")
            params["beforeCreatedAt"] = cursor
        raise LavaAPIError(502, "product_catalog_page_limit")
    finally:
        if http_client is None:
            await client.aclose()


PROVIDER_FOR_METHOD: dict[str, str] = {
    "SBP": "PAY2ME",
    "PAYPAL": "PAYPAL",
    "APPLE_PAY": "UNLIMIT",
}

CARD_PROVIDER_FOR_CURRENCY: dict[str, str] = {
    "RUB": "SMART_GLOCAL",
    "USD": "UNLIMIT",
    "EUR": "UNLIMIT",
}

PROVIDER_FOR_CURRENCY: dict[str, str] = {
    "USD": "UNLIMIT",
    "EUR": "UNLIMIT",
}


async def create_invoice(
    api_key: str,
    email: str,
    offer_id: str,
    currency: str = "RUB",
    payment_method: str | None = None,
    promo_code: str | None = None,
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

    if promo_code:
        body["promoCode"] = promo_code

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
    amount_total = data.get("amountTotal", {})
    amount = amount_total.get("amount")
    currency_out = amount_total.get("currency")

    logger.info(
        "lava_invoice_created id=%s status=%s amount=%s %s url_prefix=%s",
        invoice_id,
        status,
        amount,
        currency_out,
        payment_url[:60] if payment_url else "none",
    )
    return InvoiceResult(
        invoice_id=str(invoice_id),
        payment_url=payment_url,
        status=status,
        amount=amount,
        currency=currency_out,
    )
