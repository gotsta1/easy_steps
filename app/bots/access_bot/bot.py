from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.core.config import get_settings

# Module-level singletons, initialised on first call.
_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    """Return the singleton Access Bot instance."""
    global _bot
    if _bot is None:
        settings = get_settings()
        session = None
        if settings.TELEGRAM_PROXY_URL:
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(settings.TELEGRAM_PROXY_URL)
            session = AiohttpSession(connector=connector)
        _bot = Bot(
            token=settings.ACCESS_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=session,
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    """Return the singleton Dispatcher with all handlers registered."""
    global _dp
    if _dp is None:
        from app.bots.access_bot.handlers import register_handlers  # lazy import

        _dp = Dispatcher()
        register_handlers(_dp)
    return _dp
