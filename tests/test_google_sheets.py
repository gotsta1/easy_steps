from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import google_sheets


class FakeWorksheet:
    def __init__(self, invoice_ids: list[str] | None = None) -> None:
        self.col_count = 7
        self.invoice_ids = ["Invoice ID", *(invoice_ids or [])]
        self.appended_rows: list[list] = []
        self.hidden_ranges: list[tuple[int, int]] = []
        self.header = ""

    def add_cols(self, count: int) -> None:
        self.col_count += count

    def acell(self, _label: str) -> SimpleNamespace:
        return SimpleNamespace(value=self.header)

    def update_acell(self, _label: str, value: str) -> None:
        self.header = value

    def hide_columns(self, start: int, end: int) -> None:
        self.hidden_ranges.append((start, end))

    def col_values(self, column: int) -> list[str]:
        assert column == 8
        return self.invoice_ids

    def append_row(self, row: list, value_input_option: str) -> None:
        assert value_input_option == "USER_ENTERED"
        self.appended_rows.append(row)
        self.invoice_ids.append(row[7])


def _reset_module_state() -> None:
    google_sheets._prepared_sheets.clear()
    google_sheets._worksheets.clear()


def test_append_sale_adds_hidden_invoice_column_and_payment_row(monkeypatch) -> None:
    _reset_module_state()
    sheet = FakeWorksheet()
    monkeypatch.setattr(google_sheets, "_get_worksheet", lambda *_args: sheet)

    appended = google_sheets.append_sale(
        "credentials.json",
        "spreadsheet",
        "Sales",
        invoice_id="invoice-1",
        account="1463889608",
        amount=329,
        user_name="Олеся",
        date_time=datetime(2026, 7, 31, 12, 21, 32, tzinfo=timezone.utc),
        cuid="cmoi.aj9",
    )

    assert appended is True
    assert sheet.col_count == 8
    assert sheet.header == "Invoice ID"
    assert sheet.hidden_ranges == [(7, 8)]
    assert sheet.appended_rows == [[
        "1463889608",
        329,
        "Олеся",
        "31.07.26 15:21:32",
        "cmoi.aj9",
        "",
        "",
        "invoice-1",
    ]]


def test_append_sale_does_not_duplicate_existing_invoice(monkeypatch) -> None:
    _reset_module_state()
    sheet = FakeWorksheet(["invoice-1"])
    monkeypatch.setattr(google_sheets, "_get_worksheet", lambda *_args: sheet)

    appended = google_sheets.append_sale(
        "credentials.json",
        "spreadsheet",
        "Sales",
        invoice_id="invoice-1",
        account="1463889608",
        amount=329,
        user_name="Олеся",
        date_time=datetime(2026, 7, 31, 12, 21, 32, tzinfo=timezone.utc),
        cuid="cmoi.aj9",
    )

    assert appended is False
    assert sheet.appended_rows == []
