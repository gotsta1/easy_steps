"""
Telegram webhook endpoint for the Access Bot.

This module exposes ``access_bot_webhook_handler`` — a bare async function
registered in main.py at the path configured by ACCESS_BOT_WEBHOOK_PATH.
Using a plain function (not an APIRouter) allows main.py to set the path
dynamically from settings, which must match the URL passed to setWebhook.
"""
from __future__ import annotations

import logging

from aiogram.types import Update
from fastapi import Depends, Header, HTTPException, Request, status

from app.bots.access_bot.bot import get_bot, get_dispatcher
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def access_bot_webhook_handler(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    POST {ACCESS_BOT_WEBHOOK_PATH}

    Validates the Telegram secret token, then feeds the update into aiogram.
    Only ``chat_join_request`` updates are expected (configured in setWebhook).
    """
    if x_telegram_bot_api_secret_token != settings.ACCESS_BOT_SECRET_TOKEN:
        logger.warning(
            "invalid_telegram_secret_token remote=%s", request.client
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )

    data = await request.json()
    update = Update.model_validate(data)

    bot = get_bot()
    dp = get_dispatcher()
    await dp.feed_update(bot=bot, update=update)

    return {"ok": True}
