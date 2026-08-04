"""Google Sheets integration for recording sales."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import gspread

logger = logging.getLogger(__name__)

_client: gspread.Client | None = None
_worksheets: dict[tuple[str, str], gspread.Worksheet] = {}
_prepared_sheets: set[int] = set()
_sheet_lock = threading.Lock()

INVOICE_ID_COLUMN = 8


def _get_client(credentials_path: str) -> gspread.Client:
    global _client
    if _client is None:
        _client = gspread.service_account(filename=credentials_path)
    return _client


def _get_worksheet(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str,
) -> gspread.Worksheet:
    key = (spreadsheet_id, sheet_name)
    sheet = _worksheets.get(key)
    if sheet is None:
        client = _get_client(credentials_path)
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        _worksheets[key] = sheet
    return sheet


def _prepare_worksheet(sheet: gspread.Worksheet) -> None:
    """Ensure the hidden Invoice ID column exists for deduplication."""
    sheet_key = id(sheet)
    if sheet_key in _prepared_sheets:
        return
    if sheet.col_count < INVOICE_ID_COLUMN:
        sheet.add_cols(INVOICE_ID_COLUMN - sheet.col_count)
    if sheet.acell("H1").value != "Invoice ID":
        sheet.update_acell("H1", "Invoice ID")
    sheet.hide_columns(7, 8)
    _prepared_sheets.add(sheet_key)


def append_sale(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    *,
    invoice_id: str,
    account: str,
    amount: float,
    user_name: str,
    date_time: datetime,
    cuid: str,
) -> bool:
    """Append a sale once, using the hidden Invoice ID column as the key.

    Returns ``True`` when a row was appended and ``False`` when it already
    existed. Exceptions are intentionally propagated so the durable delivery
    worker can record the failure and retry later.
    """
    with _sheet_lock:
        sheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
        _prepare_worksheet(sheet)

        if invoice_id in sheet.col_values(INVOICE_ID_COLUMN):
            logger.info("gsheet_sale_already_recorded invoice_id=%s", invoice_id)
            return False

        row = [
            account,
            amount,
            user_name,
            date_time.astimezone(timezone(timedelta(hours=3))).strftime(
                "%d.%m.%y %H:%M:%S"
            ),
            cuid,
            "",
            "",
            invoice_id,
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(
            "gsheet_sale_recorded invoice_id=%s account=%s amount=%s cuid=%s",
            invoice_id,
            account,
            amount,
            cuid,
        )
        return True
