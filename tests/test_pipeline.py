from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from expense_tracker import pipeline, summary
from expense_tracker.categorize import classify
from expense_tracker.config import Settings
from expense_tracker.db import init_db, make_engine, session_scope
from expense_tracker.models import Direction, Kind, RawEmail, Transaction, TxnType
from expense_tracker.parsing import NotATransaction, ParseFailure, parse_message
from expense_tracker.sources.base import RawMessage
from expense_tracker.sources.eml_folder import EmlFolderSource
from expense_tracker.synthetic import generate

TZ = ZoneInfo("Asia/Jakarta")
SETTINGS = Settings(_env_file=None, own_names="KEVIN", daily_budget=50_000)


def msg(subject: str, body: str) -> RawMessage:
    return RawMessage("id-" + subject, "test", datetime(2026, 9, 25, 12, 0), "bank", subject, "", body)


def test_qris_payment():
    p = parse_message(msg("Pembayaran QRIS Berhasil",
                          "Penerima : KOPI KENANGAN\nNominal Transaksi : Rp 25.000,00\n"
                          "Tanggal : 25 Sep 2026\nWaktu : 08:15:00 WIB\nNo. Referensi : 123"))
    assert (p.txn_type, p.direction, p.amount, p.counterparty) == (TxnType.QRIS, Direction.OUT, 25_000, "KOPI KENANGAN")
    assert p.occurred_at == datetime(2026, 9, 25, 8, 15)
    assert p.reference == "123"
    assert classify(p, SETTINGS).category == "Makan & Minum"


def test_transfer_in_and_own_transfer():
    p = parse_message(msg("Dana Masuk", "Nama Pengirim : BUDI\nNominal : Rp 50.000"))
    assert (p.txn_type, p.direction, p.counterparty) == (TxnType.TRANSFER_IN, Direction.IN, "BUDI")
    assert classify(p, SETTINGS).kind == Kind.INCOME

    own = parse_message(msg("Transfer Berhasil", "Nama Penerima : KEVIN ARIO\nNominal : Rp 100.000"))
    assert classify(own, SETTINGS).kind == Kind.INTERNAL


def test_topup_counts_as_spending_by_default():
    p = parse_message(msg("Top Up Berhasil", "Penerima : GOPAY - 0812\nNominal : Rp 100.000\nBiaya Admin : Rp 1.000"))
    assert (p.txn_type, p.fee) == (TxnType.TOPUP, 1_000)
    assert classify(p, SETTINGS).kind == Kind.EXPENSE
    # Once e-wallet spending is tracked separately, top-ups become internal to avoid double counting.
    not_counting = Settings(_env_file=None, count_topups_as_spending=False)
    assert classify(p, not_counting).kind == Kind.INTERNAL


def test_footer_words_do_not_break_detection():
    body = ("Penerima : INDOMARET\nNominal : Rp 30.000\n"
            "Jika transaksi tidak berhasil, hubungi kami. Jangan bagikan kode OTP. Transfer aman.")
    p = parse_message(msg("Pembayaran QRIS Berhasil", body))
    assert p.txn_type == TxnType.QRIS and p.amount == 30_000


def test_seed_rule_word_boundaries():
    p = parse_message(msg("Pembayaran Berhasil", "Merchant : TOKOPEDIA\nJumlah : Rp 80.000"))
    assert classify(p, SETTINGS).category == "Belanja Online"


def test_skip_and_fail():
    with pytest.raises(NotATransaction):
        parse_message(msg("Transaksi Gagal", "Nominal : Rp 10.000"))
    with pytest.raises(NotATransaction):
        parse_message(msg("Promo", "Dapatkan promo cashback!"))
    with pytest.raises(ParseFailure):
        parse_message(msg("Pembayaran Berhasil", "Penerima : X"))


@pytest.fixture
def engine():
    e = make_engine("sqlite:///:memory:")
    init_db(e)
    return e


def test_synthetic_end_to_end(tmp_path, engine):
    n = generate(tmp_path, days=45, seed=7, end=date(2026, 9, 25), tz=TZ)
    source = EmlFolderSource(tmp_path, TZ)
    with session_scope(engine) as s:
        stats = pipeline.sync(s, source, SETTINGS)
    assert stats.new == n
    assert stats.failed == 0
    assert stats.parsed + stats.skipped == n

    # Re-running is a no-op (dedup).
    with session_scope(engine) as s:
        again = pipeline.sync(s, source, SETTINGS, since=datetime(1970, 1, 1))
        assert again.new == 0
        m = summary.month_summary(s, 2026, 9, today=date(2026, 9, 25))
        d = summary.day_summary(s, date(2026, 9, 25), SETTINGS.daily_budget)

    assert m.spent > 0 and m.income >= 2_500_000
    assert sum(m.per_day.values()) == m.spent
    assert sum(v for _, v in m.by_category) == m.spent
    assert m.daily_average == m.spent // 25
    assert d.budget_left == 50_000 - d.spent


def test_user_category_survives_reparse(tmp_path, engine):
    generate(tmp_path, days=5, seed=1, end=date(2026, 9, 25), tz=TZ)
    with session_scope(engine) as s:
        pipeline.sync(s, EmlFolderSource(tmp_path, TZ), SETTINGS)
        txn = s.query(Transaction).first()
        txn.category, txn.category_source = "Custom", "user"
        tid = txn.id
    with session_scope(engine) as s:
        pipeline.reparse(s, SETTINGS, only_unparsed=False)
        assert s.get(Transaction, tid).category == "Custom"
        assert s.query(RawEmail).count() >= s.query(Transaction).count()
