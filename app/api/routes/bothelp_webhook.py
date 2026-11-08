"""
BotHelp webhook handler.

Receives webhook calls from BotHelp bot steps and saves the
bothelp_subscriber_id mapping for each Telegram user.

BotHelp payload fields we use:
  - user_id:         Telegram user ID (messenger ID)
  - bothelp_user_id: BotHelp internal subscriber ID
"""
from __future__ import annotations

import logging

from fastapi import Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.repo import UserRepo
from app.db.session import get_db
from app.services.bothelp_status_sync import sync_telegram_user_status
from app.services.bothelp_api import get_bothelp_client, BotHelpAPIError
from app.services.bothelp_mapping import bind_verified_subscriber, matches_identity, MappingConflict

logger = logging.getLogger(__name__)


async def bothelp_webhook_handler(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        payload = await request.json()
    except ValueError:
        return {"status": "invalid_payload"}
    if not isinstance(payload, dict):
        return {"status": "invalid_payload"}
    logger.info("bothelp_webhook_received keys=%s", list(payload.keys()))

    telegram_user_id_raw = payload.get("user_id")
    bothelp_user_id_raw = payload.get("bothelp_user_id")

    if not telegram_user_id_raw or not bothelp_user_id_raw:
        logger.warning(
            "bothelp_webhook_missing_fields user_id=%s bothelp_user_id=%s",
            telegram_user_id_raw,
            bothelp_user_id_raw,
        )
        return {"status": "missing_fields"}

    try:
        if not all(str(v).isdigit() and int(v)>0 for v in (telegram_user_id_raw, bothelp_user_id_raw)):
            raise ValueError("IDs must be positive integers")
        telegram_user_id = int(telegram_user_id_raw)
        bothelp_subscriber_id = int(bothelp_user_id_raw)
    except (ValueError, TypeError):
        logger.warning(
            "bothelp_webhook_invalid_ids user_id=%s bothelp_user_id=%s",
            telegram_user_id_raw,
            bothelp_user_id_raw,
        )
        return {"status": "invalid_ids"}

    if not settings.BOTHELP_CHANNEL_ID:
        raise HTTPException(503, detail="BotHelp channel is not configured")
    client = get_bothelp_client()
    try:
        profile = await client.get_subscriber(bothelp_subscriber_id)
    except BotHelpAPIError as exc:
        raise HTTPException(503, detail="BotHelp identity verification unavailable") from exc
    if not matches_identity(profile, telegram_user_id, settings.BOTHELP_CHANNEL_ID):
        return {"status": "identity_mismatch"}
    user_repo = UserRepo(db)
    user, created = await user_repo.get_or_create(telegram_user_id)
    try:
        await bind_verified_subscriber(db, user, profile)
    except MappingConflict as exc:
        raise HTTPException(409, detail="BotHelp identity binding conflict") from exc
    await db.commit()

    await sync_telegram_user_status(db, settings, telegram_user_id, client=client)

    logger.info(
        "bothelp_subscriber_saved tg_id=%d bothelp_id=%d created=%s",
        telegram_user_id,
        bothelp_subscriber_id,
        created,
    )
    return {"status": "ok"}
