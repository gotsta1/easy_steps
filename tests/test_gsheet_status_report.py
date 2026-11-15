from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import Entitlement, EntitlementStatus
from app.services import google_sheets_status as sheet_api
from app.services import gsheet_delivery, gsheet_status_report as report

NOW = datetime(2026, 11, 15, 4, tzinfo=timezone.utc)  # 07:00 MSK


def settings(**overrides):
    values = dict(GSHEET_CREDENTIALS_PATH="credentials.json", GSHEET_SPREADSHEET_ID="book",
                  GSHEET_SHEET_NAME="Sales", GSHEET_REF="ref_ref", GSHEET_STATUS_SHEET_ID=123,
                  GSHEET_STATUS_SHEET_NAME="Пользователи и статусы", GSHEET_STATUS_UPDATE_HOUR_MSK=6,
                  GSHEET_RETRY_INTERVAL_SECONDS=300)
    return SimpleNamespace(**{**values, **overrides})


def entitlement(**overrides):
    return Entitlement(user_id=1, product_key="club", **{
        "status": EntitlementStatus.active, "active_until": NOW + timedelta(days=3), **overrides,
    })


@pytest.mark.parametrize(("expiry", "status", "active", "days"), [
    (NOW + timedelta(seconds=1), EntitlementStatus.active, True, 1),
    (NOW + timedelta(days=2, seconds=1), EntitlementStatus.active, True, 3),
    (NOW, EntitlementStatus.active, False, 0),
    (NOW - timedelta(days=1), EntitlementStatus.active, False, 0),
    (NOW + timedelta(days=3), EntitlementStatus.inactive, False, 0),
    (None, EntitlementStatus.active, True, None),
    (None, EntitlementStatus.canceled, False, 0),
    (None, EntitlementStatus.past_due, False, 0),
])
def test_row_matches_access_rules(expiry, status, active, days):
    row = report.make_status_row(123, entitlement(active_until=expiry, status=status), NOW)
    assert (row.telegram_user_id, row.active, row.days) == (123, active, days)


def test_naive_expiry_is_utc():
    row = report.make_status_row(123, entitlement(active_until=NOW.replace(tzinfo=None)), NOW)
    assert row.expires_at == NOW
    assert row.active is False


@pytest.mark.parametrize(("last", "now", "due"), [
    (None, NOW.replace(hour=1), True),  # Initial publish even before 06:00.
    (NOW, NOW + timedelta(hours=22, minutes=59), False),
    (NOW, NOW + timedelta(hours=23), True),  # Next 06:00 MSK, not 24h since last run.
    (NOW.replace(hour=2), NOW.replace(hour=3), True),
    (NOW.replace(hour=3), NOW.replace(hour=3), False),
    (NOW - timedelta(days=3), NOW, True),  # Catch up after downtime.
])
def test_daily_schedule(last, now, due):
    assert report.report_is_due(last, now, 6) is due


def test_report_can_run_without_referral_sales_and_cannot_target_sales_tab():
    assert report.is_status_report_configured(settings(GSHEET_REF=""))
    assert not report.is_status_report_configured(settings(GSHEET_STATUS_SHEET_ID=None))
    assert not report.is_status_report_configured(settings(GSHEET_STATUS_SHEET_NAME="Sales"))
    assert not report.is_status_report_configured(settings(GSHEET_CREDENTIALS_PATH=""))


def test_schedule_config_is_validated():
    base = dict(APP_PUBLIC_BASE_URL="https://example.com", DATABASE_URL="postgresql://localhost/db",
                ACCESS_BOT_TOKEN="test", ACCESS_BOT_SECRET_TOKEN="test", TG_CHANNEL_ID=-1001,
                LAVA_WEBHOOK_LOGIN="test", LAVA_WEBHOOK_PASSWORD="test", LAVA_API_KEY="test", ADMIN_TOKEN="test")
    for hour in (-1, 24):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **base, GSHEET_STATUS_UPDATE_HOUR_MSK=hour)
    assert Settings(_env_file=None, **base, GSHEET_STATUS_SHEET_ID=None).GSHEET_STATUS_SHEET_ID is None
    assert Settings(_env_file=None, **base, GSHEET_STATUS_SHEET_ID="").GSHEET_STATUS_SHEET_ID is None


async def test_load_only_club_all_referrals_and_sort_active_expiring_first(monkeypatch):
    db = AsyncMock()
    db.__aenter__.return_value = db
    result = MagicMock()
    result.all.return_value = [(9, entitlement(status=EntitlementStatus.inactive)),
                              (3, entitlement(active_until=None)), (2, entitlement())]
    db.execute.return_value = result
    monkeypatch.setattr(report, "AsyncSessionFactory", lambda: db)
    rows = await report.load_status_rows(NOW)
    assert [r.telegram_user_id for r in rows] == [2, 3, 9]
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(),
                                                 compile_kwargs={"literal_binds": True}))
    assert "entitlements.product_key = 'club'" in sql
    assert "pending_invoices" not in sql and "bothelp_subscriber_id IS NOT NULL" not in sql
    db.commit.assert_not_awaited()


async def test_duplicate_club_rows_abort_without_publishing(monkeypatch):
    db = AsyncMock()
    db.__aenter__.return_value = db
    result = MagicMock()
    result.all.return_value = [(1, entitlement()), (1, entitlement())]
    db.execute.return_value = result
    monkeypatch.setattr(report, "AsyncSessionFactory", lambda: db)
    with pytest.raises(ValueError, match="duplicate_club"):
        await report.load_status_rows(NOW)


def patch_job(monkeypatch, timestamp=None, write=None):
    read = MagicMock(return_value=timestamp)
    write = write or MagicMock()
    load = AsyncMock(return_value=[report.make_status_row(123, entitlement(), NOW)])
    monkeypatch.setattr(report, "utcnow", lambda: NOW)
    monkeypatch.setattr(report, "read_report_timestamp", read)
    monkeypatch.setattr(report, "replace_status_report", write)
    monkeypatch.setattr(report, "load_status_rows", load)
    return read, write, load


async def test_initial_publish_once_then_daily(monkeypatch):
    read, write, load = patch_job(monkeypatch)
    job = report.StatusReportJob(settings())
    assert await job.run_if_due()
    assert not await job.run_if_due()
    assert job.last_success == NOW
    read.assert_called_once()
    write.assert_called_once()
    load.assert_awaited_once()


async def test_restart_uses_persisted_sheet_timestamp(monkeypatch):
    _, write, load = patch_job(monkeypatch, timestamp=NOW)
    assert not await report.StatusReportJob(settings()).run_if_due()
    write.assert_not_called()
    load.assert_not_awaited()


async def test_google_failure_retries_without_marking_success_or_logging_response(monkeypatch, caplog):
    _, write, _ = patch_job(monkeypatch, write=MagicMock(side_effect=RuntimeError("PRIVATE_PROVIDER_BODY")))
    job = report.StatusReportJob(settings())
    assert not await job.run_if_due()
    assert job.last_success is None
    assert "PRIVATE_PROVIDER_BODY" not in caplog.text
    write.side_effect = None
    assert await job.run_if_due()
    assert write.call_count == 2


async def test_failed_marker_read_is_retried_before_querying_database(monkeypatch):
    read, write, load = patch_job(monkeypatch)
    read.side_effect = RuntimeError("Google unavailable")
    job = report.StatusReportJob(settings())
    assert not await job.run_if_due()
    assert not job.loaded
    write.assert_not_called()
    load.assert_not_awaited()
    read.side_effect = None
    assert await job.run_if_due()


async def test_database_failure_does_not_replace_old_sheet(monkeypatch):
    _, write, load = patch_job(monkeypatch, timestamp=NOW - timedelta(days=2))
    load.side_effect = RuntimeError("DB unavailable")
    job = report.StatusReportJob(settings())
    assert not await job.run_if_due()
    assert job.last_success == NOW - timedelta(days=2)
    write.assert_not_called()


async def test_sales_failure_does_not_block_report(monkeypatch):
    sales = AsyncMock(side_effect=RuntimeError("sales failure"))
    job = SimpleNamespace(run_if_due=AsyncMock())
    monkeypatch.setattr(gsheet_delivery, "run_delivery_batch", sales)
    monkeypatch.setattr(gsheet_delivery, "StatusReportJob", lambda _: job)
    monkeypatch.setattr(gsheet_delivery.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await gsheet_delivery.delivery_loop(settings())
    job.run_if_due.assert_awaited_once()


async def test_report_runs_with_sales_disabled(monkeypatch):
    sales = AsyncMock()
    job = SimpleNamespace(run_if_due=AsyncMock())
    monkeypatch.setattr(gsheet_delivery, "run_delivery_batch", sales)
    monkeypatch.setattr(gsheet_delivery, "StatusReportJob", lambda _: job)
    monkeypatch.setattr(gsheet_delivery.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await gsheet_delivery.delivery_loop(settings(GSHEET_REF=""))
    sales.assert_not_awaited()
    job.run_if_due.assert_awaited_once()


def test_report_summary_and_dates():
    rows = [report.make_status_row(1, entitlement(active_until=NOW + timedelta(days=7)), NOW),
            report.make_status_row(2, entitlement(active_until=None), NOW),
            report.make_status_row(3, entitlement(status=EntitlementStatus.inactive), NOW),
            report.make_status_row(4, entitlement(active_until=NOW + timedelta(days=30)), NOW)]
    values = sheet_api.report_values(rows, NOW)
    assert values[2] == ["Всего пользователей", 4, "Активных", 3]
    assert values[3] == ["Бессрочных", 1, "Истёкших", 1]
    assert values[4] == ["Истекает за 7 дней", 1, "Истекает за 30 дней", 2]
    assert values[8][2:] == ["Бессрочно", "Бессрочно"]
    assert values[7][3].hour == 7
    assert isinstance(sheet_api._cell(values[7][3])["userEnteredValue"]["numberValue"], float)
    assert sheet_api._cell("123")["userEnteredValue"] == {"stringValue": "123"}


@pytest.mark.parametrize("num_users", [0, 2, 1500])
def test_atomic_replacement_clears_stale_rows_and_scopes_every_request(num_users):
    values = sheet_api.report_values([report.StatusRow(i, True, None, None) for i in range(num_users)], NOW)
    criteria = {"criteria": {"1": {"hiddenValues": ["Истёк"]}}}
    requests = sheet_api.build_report_requests(123, 1000, values, criteria, 26)
    update = next(req["updateCells"] for req in requests if "updateCells" in req)
    assert update["range"]["endRowIndex"] == max(1000, 7 + num_users, 8)
    assert len(update["rows"]) == 7 + num_users
    assert "userEnteredValue" in update["fields"]
    assert next(r["setBasicFilter"]["filter"] for r in requests if "setBasicFilter" in r)["criteria"] == criteria["criteria"]
    assert "addSheet" not in str(requests) and "appendCells" not in str(requests)
    for request in requests:
        body = next(iter(request.values()))
        target = body.get("range") or body.get("properties") or body.get("filter")["range"]
        assert target["sheetId"] == 123


def test_replace_is_one_batch_never_clear_then_write(monkeypatch):
    book = MagicMock()
    book.fetch_sheet_metadata.return_value = {"sheets": [{"properties": {"sheetId": 123}}]}
    sheet = SimpleNamespace(id=123, row_count=1000, col_count=26)

    @contextmanager
    def fake_sheet(_):
        yield book, sheet

    monkeypatch.setattr(sheet_api, "_report_sheet", fake_sheet)
    sheet_api.replace_status_report(settings(), [], NOW)
    book.batch_update.assert_called_once()


@pytest.mark.parametrize(("header", "expected"), [
    ([], None), ([[""]], None),
    ([[sheet_api.TITLE], ["Обновлено (МСК)", "15.11.2026 07:00:00"]], NOW),
])
def test_timestamp_read(header, expected, monkeypatch):
    sheet = MagicMock()
    sheet.get.return_value = header

    @contextmanager
    def fake_sheet(_):
        yield MagicMock(), sheet

    monkeypatch.setattr(sheet_api, "_report_sheet", fake_sheet)
    assert sheet_api.read_report_timestamp(settings()) == expected


def test_wrong_gid_cannot_overwrite_payment_sheet(monkeypatch):
    client = MagicMock()
    client.open_by_key.return_value.get_worksheet_by_id.return_value.title = "Sales"
    monkeypatch.setattr(sheet_api.gspread, "service_account", lambda **_: client)
    with pytest.raises(ValueError, match="worksheet_mismatch"):
        sheet_api.replace_status_report(settings(), [], NOW)
    client.open_by_key.return_value.batch_update.assert_not_called()
    client.http_client.session.close.assert_called_once()


def test_unknown_header_or_invalid_timestamp_is_not_treated_as_empty(monkeypatch):
    sheet = MagicMock()

    @contextmanager
    def fake_sheet(_):
        yield MagicMock(), sheet

    monkeypatch.setattr(sheet_api, "_report_sheet", fake_sheet)
    for header in ([["Other report"]], [[sheet_api.TITLE], ["Updated", "broken date"]]):
        sheet.get.return_value = header
        with pytest.raises(ValueError):
            sheet_api.read_report_timestamp(settings())
