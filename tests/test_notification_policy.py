from app.main import (
    _should_send_expiry_notification,
    _should_send_post_expiry_notification,
)
from app.core.config import Settings


def test_settings_expose_only_selected_notification_thresholds() -> None:
    settings = Settings.model_construct(
        BOTHELP_STEP_NOTIFY_3D="step-3d",
        BOTHELP_STEP_NOTIFY_2D="step-2d",
        BOTHELP_STEP_NOTIFY_EXPIRED_10H="step-expired-10h",
        BOTHELP_STEP_NOTIFY_EXPIRED_3D="step-expired-3d",
    )

    assert settings.notify_steps_map == {3: "step-3d", 2: "step-2d"}
    assert settings.notify_post_expiry_hours_map == {
        10: "step-expired-10h",
        72: "step-expired-3d",
    }


def test_only_selected_pre_expiry_notifications_are_enabled() -> None:
    for duration_days in (7, 30, 90, 180, 365, None):
        assert _should_send_expiry_notification(duration_days, 3) is True
        assert _should_send_expiry_notification(duration_days, 2) is True
        assert _should_send_expiry_notification(duration_days, 1) is False


def test_only_selected_post_expiry_notifications_are_enabled() -> None:
    for duration_days in (7, 30, 90, 180, 365, None):
        assert _should_send_post_expiry_notification(duration_days, 10) is True
        assert _should_send_post_expiry_notification(duration_days, 72) is True
        assert _should_send_post_expiry_notification(duration_days, 168) is False
