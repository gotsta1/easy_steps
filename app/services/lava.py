"""
Lava.top webhook payload parsing utilities.

All functions are pure (no I/O) so they are easy to unit-test and to adapt
when the exact Lava API contract is confirmed.

Event types (from Lava docs):
  Webhook type "Результат платежа":
    - payment.success  — successful purchase of a digital product (or first subscription payment)
    - payment.failed   — failed purchase

  Webhook type "Регулярный платеж":
    - subscription.recurring.payment.success  — successful subscription renewal
    - subscription.recurring.payment.failed   — failed subscription renewal
    - subscription.cancelled                  — subscription cancelled

For club access we use DIGITAL PRODUCTS (not subscriptions), so the primary
event is ``payment.success``.  Subscription events are kept for robustness.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Event-type normalisation (exact strings from Lava docs)
# ─────────────────────────────────────────────────────────────────────────────

_PAYMENT_SUCCESS_TYPES: frozenset[str] = frozenset(
    {
        "payment.success",
        "subscription.recurring.payment.success",
    }
)

_PAYMENT_FAILED_TYPES: frozenset[str] = frozenset(
    {
        "payment.failed",
        "subscription.recurring.payment.failed",
    }
)

_CANCELED_TYPES: frozenset[str] = frozenset(
    {
        "subscription.cancelled",
    }
)


def classify_event(raw_event_type: str) -> str | None:
    """
    Map a raw Lava event type string to a normalised action.

    Returns one of: ``"payment_success"``, ``"payment_failed"``,
    ``"canceled"``, or ``None`` for unrecognised event types.
    """
    et = raw_event_type.lower().strip()
    if et in _PAYMENT_SUCCESS_TYPES:
        return "payment_success"
    if et in _PAYMENT_FAILED_TYPES:
        return "payment_failed"
    if et in _CANCELED_TYPES:
        return "canceled"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Field extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_event_type(payload: dict[str, Any]) -> str:
    """
    Extract the event type string from the webhook payload.

    TODO: confirm the exact field name from Lava docs.
    Candidates: ``type``, ``event``, ``event_type``, ``action``.
    """
    for field in ("type", "event", "event_type", "action"):
        if val := payload.get(field):
            return str(val)
    logger.warning("lava_event_type_not_found keys=%s", list(payload.keys()))
    return "unknown"


def extract_event_id(payload: dict[str, Any]) -> str:
    """
    Extract a stable unique identifier from the Lava payload for idempotency.

    TODO: confirm the actual field name.  Candidates: ``id``, ``event_id``,
    ``order_id``, ``invoice_id``, ``contract_id``.

    Falls back to a deterministic SHA-256 hash of the serialised payload so
    the same body always produces the same key even if no ID field exists.
    """
    for field in ("id", "event_id", "order_id", "invoice_id", "contract_id"):
        if val := payload.get(field):
            return str(val)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "hash_" + hashlib.sha256(raw.encode()).hexdigest()


def extract_offer_id(payload: dict[str, Any]) -> str | None:
    """
    Extract the Lava offer / product ID that identifies *which* product
    was purchased.

    TODO: confirm the exact field name.  Candidates: ``offer_id``,
    ``product_id``, ``contract.offer_id``, ``product.id``.
    """
    # Top-level fields
    for field in ("offer_id", "product_id"):
        if val := payload.get(field):
            return str(val)

    # Nested: contract.offer_id or product.id
    for parent, child_keys in [
        ("contract", ("offer_id", "product_id")),
        ("product", ("id", "offer_id")),
        ("offer", ("id",)),
    ]:
        sub = payload.get(parent)
        if isinstance(sub, dict):
            for key in child_keys:
                if val := sub.get(key):
                    return str(val)

    logger.warning("lava_offer_id_not_found keys=%s", list(payload.keys()))
    return None


def extract_telegram_user_id(payload: dict[str, Any]) -> int | None:
    """
    Extract the buyer's Telegram user ID from the payload.

    Tries the following locations in order (first non-None integer wins):
      1. payload["metadata"]["telegram_user_id"] (or tg_user_id / tg_id)
      2. Free-text fields: ``comment``, ``purpose``, ``description``
         (parse the first integer token of 5+ digits)
      3. payload["custom_fields"]["telegram_user_id"]
      4. payload["buyer"]["telegram_user_id"]

    TODO: Adjust once the real Lava payload structure is confirmed.
    """

    def _try_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # 1. metadata sub-object
    for key in ("telegram_user_id", "tg_user_id", "tg_id"):
        if val := (payload.get("metadata") or {}).get(key):
            if uid := _try_int(val):
                return uid

    # 2. free-text fields – look for a suspiciously large integer token
    for field in ("comment", "purpose", "description"):
        text = str(payload.get(field) or "")
        for token in text.split():
            token = token.strip(".,;:\"'()[]")
            if token.lstrip("-").isdigit() and len(token) >= 5:
                if uid := _try_int(token):
                    return uid

    # 3. custom_fields sub-object
    for key in ("telegram_user_id", "tg_user_id"):
        if val := (payload.get("custom_fields") or {}).get(key):
            if uid := _try_int(val):
                return uid

    # 4. buyer sub-object
    for key in ("telegram_user_id", "tg_user_id"):
        if val := (payload.get("buyer") or {}).get(key):
            if uid := _try_int(val):
                return uid

    logger.warning(
        "telegram_user_id_not_found payload_keys=%s", list(payload.keys())
    )
    return None
