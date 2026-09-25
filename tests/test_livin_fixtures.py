"""Real Livin' by Mandiri email layouts (anonymized with scripts/anonymize_eml.py)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from expense_tracker.categorize import classify
from expense_tracker.config import Settings
from expense_tracker.models import Direction, Kind, TxnType
from expense_tracker.parsing import parse_message
from expense_tracker.sources.base import message_from_bytes

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = Settings(_env_file=None, own_names="PENGGUNA DEMO")


def load(name: str):
    return parse_message(message_from_bytes((FIXTURES / name).read_bytes(), "eml", ZoneInfo("Asia/Jakarta")))


@pytest.mark.parametrize("name, amount, fee, counterparty, account, when", [
    # QRIS titled just "Pembayaran Berhasil!"; merchant is in a card block, not a table row.
    ("livin_qris_gopay_acquirer.eml", 10_000, 0, "Teman Contoh, Edukasi", "", datetime(2026, 9, 25, 17, 30, 9)),
    ("livin_qris_mandiri_acquirer.eml", 15_000, 0, "fotokopi contoh print", "", datetime(2026, 9, 25, 10, 55, 44)),
])
def test_qris(name, amount, fee, counterparty, account, when):
    p = load(name)
    assert p.txn_type == TxnType.QRIS
    assert p.direction == Direction.OUT
    assert (p.amount, p.fee, p.counterparty, p.counterparty_account, p.occurred_at) == (
        amount, fee, counterparty, account, when)
    assert p.reference and p.reference.isdigit()
    # "Merchant PAN" must not be mistaken for the merchant name.
    assert not p.counterparty.isdigit()


@pytest.mark.parametrize("name, amount, when", [
    ("livin_topup_gopay.eml", 50_000, datetime(2026, 9, 25, 17, 28, 8)),
    ("livin_topup_gopay_2.eml", 10_001, datetime(2026, 9, 25, 17, 32, 17)),
])
def test_topup(name, amount, when):
    p = load(name)
    assert (p.txn_type, p.amount, p.fee, p.counterparty, p.counterparty_account, p.occurred_at) == (
        TxnType.TOPUP, amount, 1_200, "GOPAY", "****0000", when)
    c = classify(p, SETTINGS)
    assert (c.kind, c.category) == (Kind.EXPENSE, "Top-up E-wallet")


def test_categories():
    assert classify(load("livin_qris_mandiri_acquirer.eml"), SETTINGS).category == "Pendidikan"
