from datetime import date, datetime, time

import pytest

from expense_tracker.parsing.fields import (
    canonical_field, extract_fields, parse_amount, parse_date, parse_datetime, parse_time,
)


@pytest.mark.parametrize("raw, expected", [
    ("Rp 1.250.000,00", 1_250_000),
    ("Rp1.250.000", 1_250_000),
    ("IDR 25.000", 25_000),
    ("Rp 25,000.00", 25_000),
    ("Rp 2.500", 2_500),
    ("Rp 500", 500),
    ("Rp. 54.990,00", 54_990),
    ("25000,00", 25_000),
    ("1,250,000", 1_250_000),
    ("-", None),
    ("", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("25 Sep 2026", date(2026, 9, 25)),
    ("25 September 2026", date(2026, 9, 25)),
    ("03 Agu 2026", date(2026, 8, 3)),
    ("1 Mei 2026 12:30", date(2026, 5, 1)),
    ("25/09/2026", date(2026, 9, 25)),
    ("2026-09-25", date(2026, 9, 25)),
    ("25-09-26", date(2026, 9, 25)),
    ("Kamis, 17 Des 2026", date(2026, 12, 17)),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_time_ignores_date():
    assert parse_time("2026-09-25 14:05:09 WIB") == time(14, 5, 9)
    assert parse_time("14.05 WIB") == time(14, 5)
    assert parse_time("25/09/2026") is None


def test_parse_datetime_combined():
    assert parse_datetime("25 Sep 2026, 14:05:09 WIB") == datetime(2026, 9, 25, 14, 5, 9)
    assert parse_datetime("25 Sep 2026", "07:01") == datetime(2026, 9, 25, 7, 1)


@pytest.mark.parametrize("label, field", [
    ("Nominal Transaksi", "amount"),
    ("Jumlah", "amount"),
    ("Biaya Admin", "fee"),
    ("Total Pembayaran", "total"),
    ("Nama Penerima", "counterparty"),
    ("Rekening Tujuan", "counterparty_account"),
    ("No. Referensi", "reference"),
    ("Sumber Dana", "source_account"),
    ("Tanggal Transaksi", "datetime"),
    ("Tanggal", "date"),
    ("Waktu", "time"),
    ("Nama Pengirim", "sender_name"),
    ("Terima kasih telah bertransaksi dengan kami dan semoga harimu menyenangkan", None),
])
def test_canonical_field(label, field):
    assert canonical_field(label) == field


def test_extract_from_html_table_and_text():
    html = """<table><tr><td>Penerima</td><td>:</td><td>KOPI KITA</td></tr>
              <tr><td>Nominal</td><td>Rp 25.000,00</td></tr></table>"""
    assert extract_fields(html, "") == {"counterparty": "KOPI KITA", "amount": "Rp 25.000,00"}

    text = "Penerima : KOPI KITA\nNominal\nRp 25.000,00\nTanggal: 25 Sep 2026"
    fields = extract_fields("", text)
    assert fields["counterparty"] == "KOPI KITA"
    assert fields["amount"] == "Rp 25.000,00"
    assert fields["date"] == "25 Sep 2026"
