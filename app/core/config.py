from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_ENV: Literal["dev", "prod"] = "dev"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    # Public-facing HTTPS base URL (no trailing slash).
    # Used to build the Telegram webhook URL: APP_PUBLIC_BASE_URL + ACCESS_BOT_WEBHOOK_PATH
    APP_PUBLIC_BASE_URL: str

    # ── Database ─────────────────────────────────────────────────────────────
    # Railway injects postgres:// — normalised automatically to postgresql+asyncpg://
    DATABASE_URL: str

    # ── Access Bot ───────────────────────────────────────────────────────────
    ACCESS_BOT_TOKEN: str
    ACCESS_BOT_WEBHOOK_PATH: str = "/tg/access/webhook"
    # Passed to Telegram setWebhook as secret_token; validated on every update.
    ACCESS_BOT_SECRET_TOKEN: str

    # ── Telegram channel ─────────────────────────────────────────────────────
    TG_CHANNEL_ID: int  # numeric, e.g. -1001234567890
    TG_MENU_CHANNEL_ID: int = 0  # menu channel ID; 0 disables menu flows

    # ── Kick on expire ───────────────────────────────────────────────────────
    KICK_ON_EXPIRE: bool = False
    KICK_GRACE_SECONDS: int = 0  # extra grace after active_until before kicking
    KICK_CRON_SECONDS: int = 3600  # how often the kick job runs

    # ── BotHelp API (expiry notifications) ────────────────────────────────────
    BOTHELP_CLIENT_ID: str = ""
    BOTHELP_CLIENT_SECRET: str = ""
    BOTHELP_BOT_REFERRAL: str = ""           # referral бота в BotHelp
    BOTHELP_STEP_NOTIFY_3D: str = ""         # step referral: "осталось 3 дня"
    BOTHELP_STEP_NOTIFY_2D: str = ""         # step referral: "осталось 2 дня"
    BOTHELP_STEP_NOTIFY_1D: str = ""         # step referral: "остался 1 день"
    BOTHELP_STEP_NOTIFY_3H: str = ""         # step referral: "осталось 3 часа" (trial week)
    BOTHELP_STEP_NOTIFY_EXPIRED_10H: str = ""  # step referral: "истекло 10 часов назад"
    BOTHELP_STEP_NOTIFY_EXPIRED_1W: str = ""   # step referral: "прошла 1 неделя"
    BOTHELP_STEP_NOTIFY_EXPIRED_30D: str = ""  # step referral: "прошло 30 дней"
    BOTHELP_WEBHOOK_PATH: str = "/bothelp/webhook"

    # ── Lava.top ─────────────────────────────────────────────────────────────
    LAVA_WEBHOOK_PATH: str = "/lava/webhook"
    # Basic Auth credentials for Lava webhook verification.
    # You set these in the Lava dashboard when creating the webhook.
    LAVA_WEBHOOK_LOGIN: str
    LAVA_WEBHOOK_PASSWORD: str
    # API key from Lava developer portal — used to create invoices.
    LAVA_API_KEY: str
    # Domain for generated buyer emails (Lava requires an email field).
    LAVA_BUYER_EMAIL_DOMAIN: str = "easysteps.app"

    # Lava offer IDs → mapped to club access durations.
    # Each env var holds the offer/product ID from your Lava dashboard.
    # All four grant access to the same channel — only the duration differs.
    LAVA_OFFER_CLUB_1W: str = ""   # 1-week club product
    LAVA_OFFER_CLUB_1M: str = ""   # 1-month club product
    LAVA_OFFER_CLUB_3M: str = ""   # 3-month club product
    LAVA_OFFER_CLUB_6M: str = ""   # 6-month club product
    LAVA_OFFER_CLUB_12M: str = ""  # 12-month club product
    LAVA_OFFER_MENU: str = ""      # one-time menu product (lifetime access)

    @property
    def lava_product_map(self) -> dict[str, int]:
        """
        Map Lava offer ID → duration in days.

        Only includes offers with a non-empty ID, so you can deploy
        with just a subset of products configured.
        """
        mapping: dict[str, int] = {}
        for offer_id, days in [
            (self.LAVA_OFFER_CLUB_1W, 7),
            (self.LAVA_OFFER_CLUB_1M, 30),
            (self.LAVA_OFFER_CLUB_3M, 90),
            (self.LAVA_OFFER_CLUB_6M, 180),
            (self.LAVA_OFFER_CLUB_12M, 365),
        ]:
            if offer_id:
                mapping[offer_id] = days
        return mapping

    @property
    def notify_steps_map(self) -> dict[int, str]:
        """Map days-before-expiry → BotHelp step referral. Only configured steps."""
        mapping: dict[int, str] = {}
        for days, step in [
            (3, self.BOTHELP_STEP_NOTIFY_3D),
            (2, self.BOTHELP_STEP_NOTIFY_2D),
            (1, self.BOTHELP_STEP_NOTIFY_1D),
        ]:
            if step:
                mapping[days] = step
        return mapping

    @property
    def notify_hours_map(self) -> dict[int, str]:
        """Map hours-before-expiry → BotHelp step referral. Only configured steps."""
        mapping: dict[int, str] = {}
        for hours, step in [
            (3, self.BOTHELP_STEP_NOTIFY_3H),
        ]:
            if step:
                mapping[hours] = step
        return mapping

    @property
    def notify_post_expiry_hours_map(self) -> dict[int, str]:
        """Map hours-after-expiry → BotHelp step referral. Only configured steps."""
        mapping: dict[int, str] = {}
        for hours, step in [
            (10, self.BOTHELP_STEP_NOTIFY_EXPIRED_10H),
            (168, self.BOTHELP_STEP_NOTIFY_EXPIRED_1W),
            (720, self.BOTHELP_STEP_NOTIFY_EXPIRED_30D),
        ]:
            if step:
                mapping[hours] = step
        return mapping

    # ── Google Sheets ────────────────────────────────────────────────────────
    GSHEET_CREDENTIALS_PATH: str = ""  # path to service account JSON
    GSHEET_SPREADSHEET_ID: str = ""    # spreadsheet ID from URL
    GSHEET_SHEET_NAME: str = "Лист1"   # worksheet name

    # ── Admin ────────────────────────────────────────────────────────────────
    ADMIN_TOKEN: str

    # ── Observability ────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str | None = None

    # ─────────────────────────────────────────────────────────────────────────

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalise_db_url(cls, v: str) -> str:
        """Rewrite postgres(ql):// → postgresql+asyncpg:// for asyncpg driver."""
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
