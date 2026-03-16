"""
Lava.top payment webhook handler.

This module exposes ``lava_webhook_handler`` — a bare async function (not
attached to a router) that is registered in main.py at the path configured
by ``LAVA_WEBHOOK_PATH``.

Flow for digital product purchases:
  1. Verify Basic Auth credentials.
  2. Store event in lava_events for idempotency.
  3. Classify event type.
  4. Identify the Telegram user (via pending_invoices mapping or payload).
  5. Resolve Lava offer ID → product + duration policy.
  6. Activate/extend the corresponding entitlement.
"""
from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_entitlement_service
from app.core.config import Settings, get_settings
from app.core.security import verify_lava_basic_auth
from app.db.repo import LavaEventRepo, PendingInvoiceRepo
from app.db.session import get_db
from app.services import lava as lava_svc
from app.services.entitlements import (
    CLUB_PRODUCT_KEY,
    MENU_PRODUCT_KEY,
    EntitlementService,
)

logger = logging.getLogger(__name__)

# Fixed RUB prices per plan (used for Google Sheets recording)
PLAN_PRICE_RUB: dict[str, float] = {
    "1w": 329,
    "1m": 1290,
    "3m": 3490,
    "6m": 6490,
}


def _extract_contract_id(payload: dict) -> str | None:
    """Extract contractId from the webhook payload (Lava v3 field)."""
    for field in ("contractId", "contract_id", "id"):
        if val := payload.get(field):
            return str(val)
    return None


def _resolve_offer(settings: Settings, offer_id: str | None) -> tuple[str, int | None] | None:
    """
    Resolve offer ID to (product_key, duration_days).

    duration_days=None means lifetime entitlement.
    """
    if not offer_id:
        return None

    club_duration = settings.lava_product_map.get(offer_id)
    if club_duration is not None:
        return CLUB_PRODUCT_KEY, club_duration

    if settings.LAVA_OFFER_MENU and offer_id == settings.LAVA_OFFER_MENU:
        return MENU_PRODUCT_KEY, None

    return None


async def lava_webhook_handler(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
    ent_service: EntitlementService = Depends(get_entitlement_service),
) -> dict:
    """
    POST {LAVA_WEBHOOK_PATH}

    We return 200 on all outcomes (including unmatched users) so that Lava
    does not endlessly retry.  Failures are logged for manual investigation.
    """
    # ── 1. Basic Auth verification ────────────────────────────────────────────
    if not await verify_lava_basic_auth(
        request, settings.LAVA_WEBHOOK_LOGIN, settings.LAVA_WEBHOOK_PASSWORD
    ):
        logger.error("lava_auth_failed remote=%s", request.client)
        return {"status": "auth_failed"}

    payload: dict = await request.json()
    logger.info("lava_webhook_received keys=%s payload=%s", list(payload.keys()), payload)

    raw_event_type = lava_svc.extract_event_type(payload)
    event_id = lava_svc.extract_event_id(payload)

    # ── 2. Idempotency ───────────────────────────────────────────────────────
    event_repo = LavaEventRepo(db)
    if await event_repo.exists(event_id):
        logger.info("lava_duplicate_event event_id=%s", event_id)
        return {"status": "duplicate"}

    await event_repo.create(event_id, raw_event_type, payload)

    # ── 3. Classify event ────────────────────────────────────────────────────
    action = lava_svc.classify_event(raw_event_type)
    if action is None:
        logger.info(
            "lava_unhandled_event_type raw=%s event_id=%s",
            raw_event_type,
            event_id,
        )
        return {"status": "unhandled_event_type"}

    # ── 4. Identify user ─────────────────────────────────────────────────────
    # Primary: look up via pending_invoices table (contractId from payload).
    telegram_user_id: int | None = None
    offer_id: str | None = None
    pending_repo = PendingInvoiceRepo(db)

    contract_id = _extract_contract_id(payload)
    if contract_id:
        pending = await pending_repo.get_by_contract_id(contract_id)
        if pending:
            telegram_user_id = pending.telegram_user_id
            offer_id = pending.offer_id
            if action == "payment_success":
                await pending_repo.mark_paid(contract_id)
            logger.info(
                "user_resolved_from_pending contract=%s telegram_id=%d action=%s",
                contract_id,
                telegram_user_id,
                action,
            )

    # Fallback: try extracting from payload fields (backwards compat).
    if telegram_user_id is None:
        telegram_user_id = lava_svc.extract_telegram_user_id(payload)

    if telegram_user_id is None:
        logger.warning(
            "lava_unmatched_user event_id=%s event_type=%s contract_id=%s",
            event_id,
            raw_event_type,
            contract_id,
        )
        return {"status": "unmatched_user"}

    # ── 5. Apply business action ─────────────────────────────────────────────

    if offer_id is None:
        offer_id = lava_svc.extract_offer_id(payload)
    offer_details = _resolve_offer(settings, offer_id)

    if action == "payment_success":
        if offer_details is None:
            logger.warning(
                "lava_unknown_offer offer_id=%s event_id=%s known_offers=%s",
                offer_id,
                event_id,
                list(settings.lava_product_map.keys()) + [settings.LAVA_OFFER_MENU],
            )
            return {"status": "unknown_offer"}

        product_key, duration_days = offer_details
        if duration_days is None:
            await ent_service.apply_lifetime_success(telegram_user_id, product_key)
        else:
            await ent_service.apply_payment_success(
                telegram_user_id, duration_days, product_key
            )

        # Record sale to Google Sheets (only for ref="tanya")
        pending_ref = pending.ref if pending else None
        if (
            settings.GSHEET_CREDENTIALS_PATH
            and settings.GSHEET_SPREADSHEET_ID
            and pending_ref == "tanya"
        ):
            from app.services.google_sheets import append_sale

            plan = pending.plan if pending else None
            amount = PLAN_PRICE_RUB.get(plan, payload.get("amount", 0))
            timestamp_str = payload.get("timestamp", "")
            from datetime import datetime as dt

            try:
                sale_dt = dt.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                from app.core.time import utcnow
                sale_dt = utcnow()

            first_name = pending.first_name or "" if pending else ""
            cuid = pending.cuid or "" if pending else ""

            try:
                append_sale(
                    credentials_path=settings.GSHEET_CREDENTIALS_PATH,
                    spreadsheet_id=settings.GSHEET_SPREADSHEET_ID,
                    sheet_name=settings.GSHEET_SHEET_NAME,
                    account=str(telegram_user_id),
                    amount=amount,
                    user_name=first_name,
                    date_time=sale_dt,
                    cuid=cuid,
                )
            except Exception:
                logger.exception("gsheet_record_failed")

    elif action == "payment_failed":
        product_key = offer_details[0] if offer_details is not None else CLUB_PRODUCT_KEY
        await ent_service.apply_payment_failed(telegram_user_id, product_key)

    elif action == "canceled":
        product_key = offer_details[0] if offer_details is not None else CLUB_PRODUCT_KEY
        await ent_service.apply_canceled(telegram_user_id, product_key)

    return {"status": "ok"}
