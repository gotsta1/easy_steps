"""Google Sheets integration for recording sales."""

from __future__ import annotations

import logging
from datetime import datetime

import gspread

logger = logging.getLogger(__name__)

_client: gspread.Client | None = None


def _get_client(credentials_path: str) -> gspread.Client:
    global _client
    if _client is None:
        _client = gspread.service_account(filename=credentials_path)
    return _client


def append_sale(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    *,
    account: str,
    amount: float,
    user_name: str,
    date_time: datetime,
    cuid: str,
) -> None:
    """Append a sale row to the Google Sheet.

    Columns: Аккаунт покупателя | Сумма оплаты | Юзер | Дата и время | CUID
    """
    try:
        client = _get_client(credentials_path)
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        row = [
            account,
            amount,
            user_name,
            date_time.strftime("%d.%m.%y %H.%M.%S"),
            cuid,
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("gsheet_sale_recorded account=%s amount=%s cuid=%s", account, amount, cuid)
    except Exception:
        logger.exception("gsheet_append_failed")
