"""Host disk alerts; intentionally independent of Docker and application services."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
from typing import Callable
from urllib import error, request


class MonitorError(Exception):
    """A diagnostic that is safe to log without Telegram credentials."""


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: int
    path: str = "/"
    threshold: int = 75
    recovery: int = 70
    reminder_seconds: int = 86400
    state_file: Path = Path("/var/lib/easysteps-monitor/disk.json")

    def __post_init__(self) -> None:
        if not self.token or not self.chat_id:
            raise MonitorError("Telegram token and chat ID are required")
        if not 0 <= self.recovery < self.threshold <= 100:
            raise MonitorError("Require 0 <= recovery < threshold <= 100")
        if self.reminder_seconds < 300:
            raise MonitorError("Reminder interval must be at least 300 seconds")

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            token=os.environ.get("DISK_MONITOR_TELEGRAM_BOT_TOKEN", ""),
            chat_id=int(os.environ.get("DISK_MONITOR_TELEGRAM_CHAT_ID", "0")),
            path=os.environ.get("DISK_MONITOR_PATH", "/"),
            threshold=int(os.environ.get("DISK_MONITOR_USED_PERCENT", "75")),
            recovery=int(os.environ.get("DISK_MONITOR_RECOVERY_PERCENT", "70")),
            reminder_seconds=int(os.environ.get("DISK_MONITOR_REMINDER_SECONDS", "86400")),
            state_file=Path(os.environ.get(
                "DISK_MONITOR_STATE_FILE", "/var/lib/easysteps-monitor/disk.json"
            )),
        )


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int

    @property
    def percent(self) -> int:
        # Match df: reserved filesystem blocks are not available to applications.
        return math.ceil(100 * self.used / (self.used + self.free))

    def describe(self, path: str) -> str:
        gib = 1024 ** 3
        return (
            f"Сервер: {socket.gethostname()}\n"
            f"Диск: {path}\n"
            f"Занято: {self.percent}%\n"
            f"Использовано: {self.used / gib:.1f} ГиБ\n"
            f"Свободно: {self.free / gib:.1f} ГиБ"
        )


def send_message(config: Config, message: str) -> int:
    body = json.dumps({"chat_id": config.chat_id, "text": message}).encode("utf-8")
    req = request.Request(
        f"https://api.telegram.org/bot{config.token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        raise MonitorError(f"Telegram HTTP {exc.code}") from None
    except (error.URLError, TimeoutError, OSError):
        raise MonitorError("Telegram network request failed") from None
    except ValueError:
        raise MonitorError("Telegram returned invalid JSON") from None

    if not isinstance(payload, dict):
        raise MonitorError("Telegram returned an invalid response")
    result = payload.get("result", {})
    if (
        payload.get("ok") is not True
        or not isinstance(result, dict)
        or not isinstance(result.get("chat"), dict)
        or result.get("chat", {}).get("id") != config.chat_id
        or not isinstance(result.get("message_id"), int)
    ):
        raise MonitorError("Telegram did not confirm message delivery")
    return result["message_id"]


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {"active": False, "last_alert_at": 0}
    if (
        not isinstance(state, dict)
        or type(state.get("active")) is not bool
        or not isinstance(state.get("last_alert_at"), (int, float))
        or not math.isfinite(state["last_alert_at"])
        or state["last_alert_at"] < 0
    ):
        raise MonitorError("Invalid disk monitor state")
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".disk-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_monitor(
    config: Config,
    *,
    now: float | None = None,
    usage: DiskUsage | None = None,
    sender: Callable[[Config, str], int] = send_message,
    test: bool = False,
) -> str:
    now = time.time() if now is None else now
    if usage is None:
        disk = shutil.disk_usage(config.path)
        usage = DiskUsage(disk.total, disk.used, disk.free)

    if test:
        sender(config, (
            "Тест мониторинга диска: уведомления подключены.\n\n"
            f"{usage.describe(config.path)}\n\n"
            f"Предупреждение при загрузке от {config.threshold}%.\n"
            "Проверка каждые 5 минут."
        ))
        print(f"disk_monitor action=test_sent used_percent={usage.percent}", flush=True)
        return "test_sent"

    state = load_state(config.state_file)
    action = "ok"
    if usage.percent >= config.threshold:
        if not state["active"] or now - state["last_alert_at"] >= config.reminder_seconds:
            sender(config, (
                "Предупреждение: заканчивается место на диске.\n\n"
                f"{usage.describe(config.path)}\n\n"
                f"Порог: {config.threshold}%. Нужна проверка свободного места."
            ))
            state = {"active": True, "last_alert_at": now}
            action = "alert_sent"
    elif state["active"] and usage.percent <= config.recovery:
        sender(config, (
            "Место на диске восстановилось.\n\n"
            f"{usage.describe(config.path)}\n\n"
            f"Загрузка снизилась до {config.recovery}% или ниже."
        ))
        state = {"active": False, "last_alert_at": 0}
        action = "recovery_sent"

    if action != "ok":
        save_state(config.state_file, state)
    print(f"disk_monitor action={action} used_percent={usage.percent}", flush=True)
    return action


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Send a test without changing state")
    args = parser.parse_args()
    try:
        run_monitor(Config.from_env(), test=args.test)
    except MonitorError as exc:
        print(f"disk_monitor_error {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # HTTP exception strings may embed the bot token in the request URL.
        print(f"disk_monitor_error type={type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
