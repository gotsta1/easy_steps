"""
Lava.top payment webhook handler.

This module exposes ``lava_webhook_handler`` — a bare async function (not
attached to a router) that is registered in main.py at the path configured
by ``LAVA_WEBHOOK_PATH``.

Flow for digital product purchases:
  1. Verify HMAC-SHA256 signature.
  2. Store event in lava_events for idempotency.
  3. Classify event type.
  4. Identify the Telegram user from the payload.
  5. Resolve Lava offer ID → duration in days.
  6. Extend (stack) the user's club entitlement.
"""
from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_entitlement_service
from app.core.config import Settings, get_settings
from app.core.security import verify_lava_signature
from app.db.repo import LavaEventRepo
from app.db.session import get_db
from app.services import lava as lava_svc
from app.services.entitlements import CLUB_PRODUCT_KEY, EntitlementService

logger = logging.getLogger(__name__)


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
    # ── 1. Signature verification ────────────────────────────────────────────
    if not await verify_lava_signature(request, settings.LAVA_SECRET):
        logger.error("lava_invalid_signature remote=%s", request.client)
        return {"status": "signature_invalid"}

    payload: dict = await request.json()
    logger.info("lava_webhook_received keys=%s", list(payload.keys()))

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
    telegram_user_id = lava_svc.extract_telegram_user_id(payload)
    if telegram_user_id is None:
        logger.warning(
            "lava_unmatched_user event_id=%s event_type=%s",
            event_id,
            raw_event_type,
        )
        return {"status": "unmatched_user"}

    # ── 5. Apply business action ─────────────────────────────────────────────

    if action == "payment_success":
        # Resolve Lava offer ID → duration in days.
        offer_id = lava_svc.extract_offer_id(payload)
        product_map = settings.lava_product_map
        duration_days = product_map.get(offer_id) if offer_id else None

        if duration_days is None:
            logger.warning(
                "lava_unknown_offer offer_id=%s event_id=%s known_offers=%s",
                offer_id,
                event_id,
                list(product_map.keys()),
            )
            return {"status": "unknown_offer"}

        await ent_service.apply_payment_success(
            telegram_user_id, duration_days, CLUB_PRODUCT_KEY
        )

    elif action == "payment_failed":
        await ent_service.apply_payment_failed(telegram_user_id, CLUB_PRODUCT_KEY)

    elif action == "canceled":
        # Subscription cancellation — not expected for digital products, but
        # handled defensively in case the shop has both product types.
        await ent_service.apply_canceled(telegram_user_id, CLUB_PRODUCT_KEY)

    return {"status": "ok"}
