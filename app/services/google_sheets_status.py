"""Atomic replacement of the dedicated club-status report, never payment rows."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import gspread

from app.core.config import Settings

if TYPE_CHECKING:
    from app.services.gsheet_status_report import StatusRow

MSK = timezone(timedelta(hours=3))
TITLE = "Пользователи и статусы"
TIMESTAMP_FORMAT = "%d.%m.%Y %H:%M:%S"
HEADER_ROW = 6  # Zero-based; the first user is on spreadsheet row 8.


@contextmanager
def _report_sheet(settings: Settings):
    client = gspread.service_account(filename=settings.GSHEET_CREDENTIALS_PATH)
    client.set_timeout((10, 60))
    try:
        book = client.open_by_key(settings.GSHEET_SPREADSHEET_ID)
        sheet = book.get_worksheet_by_id(settings.GSHEET_STATUS_SHEET_ID)
        # A misconfigured gid must not overwrite the payment worksheet.
        if (sheet.title != settings.GSHEET_STATUS_SHEET_NAME
                or sheet.title == settings.GSHEET_SHEET_NAME):
            raise ValueError("status_report_worksheet_mismatch")
        yield book, sheet
    finally:
        client.http_client.session.close()


def read_report_timestamp(settings: Settings) -> datetime | None:
    with _report_sheet(settings) as (_, sheet):
        rows = sheet.get("A1:B2")
    if not rows or not any(cell for row in rows for cell in row):
        return None
    if rows[0] != [TITLE] or len(rows) < 2 or len(rows[1]) < 2:
        raise ValueError("status_report_unrecognized_header")
    return datetime.strptime(rows[1][1], TIMESTAMP_FORMAT).replace(tzinfo=MSK)


def report_values(rows: list[StatusRow], now: datetime) -> list[list]:
    return [
        [TITLE],
        ["Обновлено (МСК)", now.astimezone(MSK)],
        ["Всего пользователей", len(rows), "Активных", sum(row.active for row in rows)],
        ["Бессрочных", sum(row.active and row.days is None for row in rows),
         "Истёкших", sum(not row.active for row in rows)],
        ["Истекает за 7 дней", sum(row.active and row.days is not None and row.days <= 7 for row in rows),
         "Истекает за 30 дней", sum(row.active and row.days is not None and row.days <= 30 for row in rows)],
        ["Источник: БД подписок. Статус доступа, не присутствие в Telegram-канале."],
        ["Telegram ID", "Статус клуба", "Осталось дней", "Доступ до (МСК)"],
        *[[str(row.telegram_user_id), "Активен" if row.active else "Истёк",
           "Бессрочно" if row.days is None else row.days,
           row.expires_at.astimezone(MSK)
           if row.expires_at else ("Бессрочно" if row.active else "")]
          for row in rows],
    ]


def _cell(value) -> dict:
    if isinstance(value, datetime):
        # Sheets serials are local wall-clock dates; the workbook timezone is not changed.
        serial = (value.astimezone(MSK).replace(tzinfo=None)
                  - datetime(1899, 12, 30)).total_seconds() / 86400
        return {"userEnteredValue": {"numberValue": serial}, "userEnteredFormat": {
            "numberFormat": {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm:ss"},
        }}
    return {"userEnteredValue": {"numberValue" if isinstance(value, int) else "stringValue": value}}


def build_report_requests(sheet_id: int, old_row_count: int, values: list[list],
                          basic_filter: dict | None = None, old_col_count: int = 4) -> list[dict]:
    row_count = max(old_row_count, len(values), HEADER_ROW + 2)
    all_cells = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": row_count,
                 "startColumnIndex": 0, "endColumnIndex": 4}
    header = {**all_cells, "startRowIndex": HEADER_ROW, "endRowIndex": HEADER_ROW + 1}
    data_filter = {**(basic_filter or {}), "range": {
        **header, "endRowIndex": max(len(values), HEADER_ROW + 2),
    }}
    dark = {"red": 0.08, "green": 0.24, "blue": 0.26}
    white = {"red": 1, "green": 1, "blue": 1}
    requests = [
        {"updateSheetProperties": {"properties": {
            "sheetId": sheet_id, "gridProperties": {
                "rowCount": row_count, "frozenRowCount": HEADER_ROW + 1,
                "columnCount": max(old_col_count, 4),
            }}, "fields": "gridProperties.rowCount,gridProperties.frozenRowCount,gridProperties.columnCount"}},
        {"repeatCell": {"range": all_cells, "cell": {"userEnteredFormat": {
            "backgroundColor": white, "textFormat": {"fontSize": 11},
            "verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
        }}, "fields": "userEnteredFormat"}},
        # Unfilled cells in this range are cleared by the SAME atomic request.
        {"updateCells": {"range": all_cells,
                         "fields": "userEnteredValue,userEnteredFormat.numberFormat",
                         "rows": [{"values": [_cell(v) for v in row]} for row in values]}},
        {"repeatCell": {"range": header, "cell": {"userEnteredFormat": {
            "backgroundColor": dark, "textFormat": {"bold": True, "foregroundColor": white},
        }}, "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": {**all_cells, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 16}}},
                        "fields": "userEnteredFormat.textFormat"}},
        {"setBasicFilter": {"filter": data_filter}},
    ]
    for row in (0, 5):
        merge_range = {**all_cells, "startRowIndex": row, "endRowIndex": row + 1}
        requests.extend([
            {"unmergeCells": {"range": merge_range}},
            {"mergeCells": {"range": merge_range, "mergeType": "MERGE_ALL"}},
        ])
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0,
                  "endIndex": max(len(values), HEADER_ROW + 2)},
        "properties": {"pixelSize": 30}, "fields": "pixelSize",
    }})
    # Wide columns keep summary labels, IDs and Moscow timestamps legible.
    for index, width in enumerate((250, 210, 235, 235)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": index, "endIndex": index + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize",
        }})
    return requests


def replace_status_report(settings: Settings, rows: list[StatusRow], now: datetime) -> None:
    values = report_values(rows, now)
    with _report_sheet(settings) as (book, sheet):
        metadata = book.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),basicFilter)"})
        basic_filter = next(item.get("basicFilter") for item in metadata["sheets"]
                            if item["properties"]["sheetId"] == sheet.id)
        book.batch_update({"requests": build_report_requests(
            sheet.id, sheet.row_count, values, basic_filter, sheet.col_count,
        )})
