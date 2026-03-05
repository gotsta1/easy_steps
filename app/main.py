from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging

# Boot logging before anything else.
_settings = get_settings()
setup_logging(_settings.LOG_LEVEL)

logger = logging.getLogger(__name__)

# Optional Sentry integration.
if _settings.SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=_settings.SENTRY_DSN, environment=_settings.APP_ENV)
    logger.info("sentry_initialized dsn_prefix=%s", _settings.SENTRY_DSN[:20])


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = get_settings()

    # ── Startup ──────────────────────────────────────────────────────────────
    from app.bots.access_bot.bot import get_bot, get_dispatcher

    bot = get_bot()
    app.state.bot = bot

    # Pre-warm the dispatcher (registers handlers).
    get_dispatcher()

    await _set_telegram_webhook(settings, bot)

    if settings.KICK_ON_EXPIRE:
        kick_task = asyncio.create_task(_kick_loop(settings))
        app.state.kick_task = kick_task
        logger.info(
            "kick_job_started interval_s=%d grace_s=%d",
            settings.KICK_CRON_SECONDS,
            settings.KICK_GRACE_SECONDS,
        )

    logger.info("app_startup_complete env=%s", settings.APP_ENV)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    if settings.KICK_ON_EXPIRE and hasattr(app.state, "kick_task"):
        app.state.kick_task.cancel()
        try:
            await app.state.kick_task
        except asyncio.CancelledError:
            pass

    await bot.session.close()
    logger.info("app_shutdown_complete")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="EasySteps Backend",
        version="0.1.0",
        # Disable docs in production to avoid leaking API structure.
        docs_url="/docs" if settings.APP_ENV == "dev" else None,
        redoc_url="/redoc" if settings.APP_ENV == "dev" else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: restrict to known origins in production
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request, call_next):
        if request.url.path.startswith("/payments"):
            body = await request.body()
            logger.info(
                "incoming_request path=%s body=%s",
                request.url.path,
                body[:500],
            )
        response = await call_next(request)
        return response

    # ── Static routers ────────────────────────────────────────────────────────
    from app.api.routes.health import router as health_router
    from app.api.routes.admin import router as admin_router
    from app.api.routes.payments import router as payments_router

    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(payments_router)

    # ── Dynamic-path routes ───────────────────────────────────────────────────
    # These paths come from env vars and MUST match what is registered with
    # Telegram / Lava, so we register them programmatically.

    from app.api.routes.lava_webhook import lava_webhook_handler

    app.add_api_route(
        settings.LAVA_WEBHOOK_PATH,
        lava_webhook_handler,
        methods=["POST"],
        tags=["lava"],
        summary="Lava.top payment webhook",
    )

    from app.bots.access_bot.webhook import access_bot_webhook_handler

    app.add_api_route(
        settings.ACCESS_BOT_WEBHOOK_PATH,
        access_bot_webhook_handler,
        methods=["POST"],
        tags=["access-bot"],
        summary="Telegram Access Bot webhook",
    )

    from app.api.routes.bothelp_webhook import bothelp_webhook_handler

    app.add_api_route(
        settings.BOTHELP_WEBHOOK_PATH,
        bothelp_webhook_handler,
        methods=["POST"],
        tags=["bothelp"],
        summary="BotHelp subscriber mapping webhook",
    )

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _set_telegram_webhook(settings: Settings, bot) -> None:
    webhook_url = (
        settings.APP_PUBLIC_BASE_URL.rstrip("/") + settings.ACCESS_BOT_WEBHOOK_PATH
    )
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.ACCESS_BOT_SECRET_TOKEN,
        allowed_updates=["chat_join_request"],
        drop_pending_updates=True,
    )
    logger.info("telegram_webhook_registered url=%s", webhook_url)


async def _kick_loop(settings: Settings) -> None:
    """Background task: periodically send expiry notifications and kick expired members."""
    while True:
        await asyncio.sleep(settings.KICK_CRON_SECONDS)
        try:
            await _run_notify_job(settings)
        except Exception:
            logger.exception("notify_job_error")
        try:
            await _run_kick_job(settings)
        except Exception:
            logger.exception("kick_job_error")


async def _run_notify_job(settings: Settings) -> None:
    """Send expiry warnings via BotHelp API for subscriptions ending in 3/2/1 days."""
    from sqlalchemy import select

    from app.core.time import utcnow
    from app.db.models import User
    from app.db.repo import EntitlementRepo
    from app.db.session import AsyncSessionFactory
    from app.services.bothelp_api import BotHelpClient, BotHelpAPIError

    steps_map = settings.notify_steps_map
    if not steps_map or not settings.BOTHELP_CLIENT_ID:
        return  # notifications not configured

    bothelp = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    now = utcnow()
    total_sent = 0

    async with AsyncSessionFactory() as db:
        ent_repo = EntitlementRepo(db)

        # Process thresholds from largest to smallest (3, 2, 1).
        for days in sorted(steps_map.keys(), reverse=True):
            step_referral = steps_map[days]
            expiring = await ent_repo.get_expiring_soon(now, days)

            for ent in expiring:
                result = await db.execute(select(User).where(User.id == ent.user_id))
                user: User | None = result.scalar_one_or_none()
                if not user or not user.bothelp_subscriber_id:
                    continue
                try:
                    await bothelp.trigger_bot_step(
                        bothelp_subscriber_id=user.bothelp_subscriber_id,
                        bot_referral=settings.BOTHELP_BOT_REFERRAL,
                        step_referral=step_referral,
                    )
                    ent.expiry_notified_days = days
                    total_sent += 1
                except BotHelpAPIError:
                    logger.warning(
                        "notify_failed tg_id=%d bothelp_id=%d days=%d",
                        user.telegram_user_id,
                        user.bothelp_subscriber_id,
                        days,
                    )

        await db.commit()

    if total_sent:
        logger.info("notify_job_complete sent=%d", total_sent)


async def _run_kick_job(settings: Settings) -> None:
    from sqlalchemy import select

    from app.bots.access_bot.bot import get_bot
    from app.core.time import utcnow
    from app.db.models import EntitlementStatus, User
    from app.db.repo import EntitlementRepo
    from app.db.session import AsyncSessionFactory
    from app.services.telegram_access import TelegramAccessService

    now = utcnow()
    grace_cutoff = now - timedelta(seconds=settings.KICK_GRACE_SECONDS)

    async with AsyncSessionFactory() as db:
        ent_repo = EntitlementRepo(db)
        expired = await ent_repo.get_expired_active(grace_cutoff)

        if not expired:
            return

        bot = get_bot()
        tg_svc = TelegramAccessService(bot, settings.TG_CHANNEL_ID)

        for ent in expired:
            result = await db.execute(select(User).where(User.id == ent.user_id))
            user: User | None = result.scalar_one_or_none()
            if not user:
                continue
            await tg_svc.kick_and_unban(user.telegram_user_id)
            ent.status = EntitlementStatus.inactive

        await db.commit()

    logger.info("kick_job_complete kicked=%d", len(expired))


# ─────────────────────────────────────────────────────────────────────────────
# ASGI entry-point
# ─────────────────────────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=_settings.APP_HOST,
        port=_settings.APP_PORT,
        reload=_settings.APP_ENV == "dev",
    )
