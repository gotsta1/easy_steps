from dataclasses import replace
import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from ops.disk_monitor import Config, DiskUsage, MonitorError, load_state, run_monitor, send_message


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(token="test-token", chat_id=123, state_file=tmp_path / "disk.json")


def disk(percent: int) -> DiskUsage:
    return DiskUsage(total=100, used=percent, free=100 - percent)


def test_threshold_matches_df_with_reserved_blocks(config: Config) -> None:
    messages = []
    usage = DiskUsage(total=110, used=75, free=25)
    assert usage.percent == 75
    assert run_monitor(config, usage=usage, now=100000, sender=lambda *args: messages.append(args)) == "alert_sent"
    assert len(messages) == 1


def test_no_alert_below_threshold(config: Config) -> None:
    messages = []
    run_monitor(config, usage=disk(74), now=100000, sender=lambda *args: messages.append(args))
    assert messages == []
    assert not config.state_file.exists()


def test_reminders_survive_process_restarts_and_wait_24_hours(config: Config) -> None:
    messages = []
    sender = lambda *args: messages.append(args)
    run_monitor(config, usage=disk(75), now=100000, sender=sender)
    assert run_monitor(replace(config), usage=disk(85), now=100300, sender=sender) == "ok"
    assert run_monitor(config, usage=disk(90), now=186399, sender=sender) == "ok"
    assert run_monitor(config, usage=disk(90), now=186400, sender=sender) == "alert_sent"
    assert len(messages) == 2


def test_hysteresis_recovery_and_new_incident(config: Config) -> None:
    messages = []
    sender = lambda *args: messages.append(args)
    run_monitor(config, usage=disk(75), now=100000, sender=sender)
    assert run_monitor(config, usage=disk(74), now=100300, sender=sender) == "ok"
    assert run_monitor(config, usage=disk(75), now=100600, sender=sender) == "ok"
    assert run_monitor(config, usage=disk(70), now=100900, sender=sender) == "recovery_sent"
    assert run_monitor(config, usage=disk(69), now=101200, sender=sender) == "ok"
    assert run_monitor(config, usage=disk(75), now=101500, sender=sender) == "alert_sent"
    assert len(messages) == 3


def test_failed_alert_is_retried_without_marking_it_sent(config: Config) -> None:
    def fail(*args):
        raise MonitorError("Telegram HTTP 500")

    with pytest.raises(MonitorError):
        run_monitor(config, usage=disk(80), now=100000, sender=fail)
    assert not config.state_file.exists()
    assert run_monitor(config, usage=disk(80), now=100300, sender=lambda *args: 1) == "alert_sent"


def test_failed_recovery_keeps_incident_active(config: Config) -> None:
    run_monitor(config, usage=disk(80), now=100000, sender=lambda *args: 1)

    def fail(*args):
        raise MonitorError("Telegram HTTP 500")

    with pytest.raises(MonitorError):
        run_monitor(config, usage=disk(60), now=100300, sender=fail)
    assert load_state(config.state_file)["active"] is True
    assert run_monitor(config, usage=disk(60), now=100600, sender=lambda *args: 1) == "recovery_sent"


def test_test_message_does_not_change_real_alert_state(config: Config) -> None:
    run_monitor(config, usage=disk(80), now=100000, sender=lambda *args: 1)
    before = config.state_file.read_bytes()
    assert run_monitor(config, usage=disk(80), now=100300, test=True, sender=lambda *args: 1) == "test_sent"
    assert config.state_file.read_bytes() == before


def test_network_error_does_not_expose_bot_token(config: Config, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise URLError(f"https://api.telegram.org/bot{config.token}/sendMessage")

    monkeypatch.setattr("ops.disk_monitor.request.urlopen", fail)
    with pytest.raises(MonitorError) as exc:
        send_message(config, "test")
    assert config.token not in str(exc.value)


@pytest.mark.parametrize("response", [
    {"ok": False, "error_code": 403},
    {"ok": True, "result": {"chat": {"id": 999}, "message_id": 1}},
])
def test_telegram_must_confirm_delivery_to_the_requested_chat(
    config: Config, monkeypatch, response: dict,
) -> None:
    monkeypatch.setattr(
        "ops.disk_monitor.request.urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()),
    )
    with pytest.raises(MonitorError):
        send_message(config, "test")


def test_confirmed_telegram_message_id_is_returned(config: Config, monkeypatch) -> None:
    response = {"ok": True, "result": {"chat": {"id": config.chat_id}, "message_id": 42}}
    monkeypatch.setattr(
        "ops.disk_monitor.request.urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()),
    )
    assert send_message(config, "test") == 42


@pytest.mark.parametrize("recovery,threshold", [(75, 75), (76, 75), (-1, 75), (70, 101)])
def test_invalid_thresholds_are_rejected(config: Config, recovery: int, threshold: int) -> None:
    with pytest.raises(MonitorError):
        replace(config, recovery=recovery, threshold=threshold)
