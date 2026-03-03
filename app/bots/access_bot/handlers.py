from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.types import ChatJoinRequest

from app.core.config import get_settings
from app.core.time import utcnow
from app.db.models import User
from app.db.repo import EntitlementRepo, UserRepo
from app.db.session import AsyncSessionFactory
from app.services.entitlements import CLUB_PRODUCT_KEY, can_approve_join
from app.services.telegram_access import TelegramAccessService

logger = logging.getLogger(__name__)
router = Router(name="access_bot")


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """
    Evaluate a channel join request and approve or decline it immediately.

    Decision criteria (all must pass):
      - The request is for the configured TG_CHANNEL_ID.
      - The user has an active entitlement for product LAVA_PRODUCT_KEY_CLUB.
      - active_until is None OR now <= active_until.
      - allowed_to_join_until is None OR now <= allowed_to_join_until.
    """
    settings = get_settings()

    # Guard: only process requests for the configured channel.
    if event.chat.id != settings.TG_CHANNEL_ID:
        logger.info(
            "join_request_ignored chat_id=%d user_id=%d",
            event.chat.id,
            event.from_user.id,
        )
        return

    telegram_user_id = event.from_user.id

    async with AsyncSessionFactory() as db:
        user_repo = UserRepo(db)
        ent_repo = EntitlementRepo(db)

        user: User | None = await user_repo.get_by_telegram_id(telegram_user_id)
        ent = None
        if user:
            ent = await ent_repo.get_by_user_and_product(user.id, CLUB_PRODUCT_KEY)

    now = utcnow()
    approved, reason = can_approve_join(ent, now=now)

    tg_svc = TelegramAccessService(bot, settings.TG_CHANNEL_ID)

    if approved:
        await tg_svc.approve_join_request(telegram_user_id)
        logger.info(
            "join_approved telegram_id=%d product=%s",
            telegram_user_id,
            CLUB_PRODUCT_KEY,
        )
    else:
        await tg_svc.decline_join_request(telegram_user_id)
        logger.warning(
            "join_declined telegram_id=%d product=%s reason=%s",
            telegram_user_id,
            CLUB_PRODUCT_KEY,
            reason,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
