from __future__ import annotations

from app.core.config import Settings
from app.services.bothelp_review_mailing import is_review_mailing_configured


def test_review_mailing_requires_complete_bothelp_configuration() -> None:
    configured = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING="step",
    )
    missing_step = Settings.model_construct(
        BOTHELP_CLIENT_ID="client",
        BOTHELP_CLIENT_SECRET="secret",
        BOTHELP_BOT_REFERRAL="bot",
        BOTHELP_STEP_REVIEW_MAILING="",
    )

    assert is_review_mailing_configured(configured) is True
    assert is_review_mailing_configured(missing_step) is False


def test_review_mailing_defaults_to_five_days_and_safe_batches() -> None:
    settings = Settings.model_construct()

    assert settings.BOTHELP_REVIEW_DELAY_HOURS == 120
    assert settings.BOTHELP_REVIEW_INTERVAL_SECONDS == 900
    assert settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS == 60
    assert settings.BOTHELP_REVIEW_BATCH_SIZE == 50
