import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, text

from expense_tracker import parties, pipeline
from expense_tracker.config import Settings
from expense_tracker.db import init_db, make_engine, session_scope
from expense_tracker.models import Counterparty, Direction, PartyType, Transaction, TxnType
from expense_tracker.parsing.base import ParsedTransaction
from expense_tracker.sources.eml_folder import EmlFolderSource

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Jakarta")
SETTINGS = Settings(_env_file=None, own_names="PENGGUNA DEMO")


def qris(name: str, acquirer: str = "GoPay", pan: str = "9360001") -> ParsedTransaction:
    return ParsedTransaction(TxnType.QRIS, Direction.OUT, 10_000, datetime(2026, 9, 25), counterparty=name,
                             counterparty_id=pan, acquirer=acquirer)


@pytest.mark.parametrize("name, acquirer, expected", [
    ("Erin Josephine, Edukasi", "GoPay", PartyType.PERSON),       # real friend QR layout
    ("Budi Santoso, Jasa", "DANA", PartyType.PERSON),
    ("kalijudan print", "Bank Mandiri", PartyType.MERCHANT),       # real store
    ("Bakso Pak Kumis, Makanan", "GoPay", PartyType.MERCHANT),     # business word beats the name pattern
    ("Toko Madura Berkah", "GoPay", PartyType.MERCHANT),
    ("Sinar Jaya", "GoPay", PartyType.MERCHANT),                   # no "Name, Category": default store
    ("Erin Josephine, Edukasi", "Bank Mandiri", PartyType.MERCHANT),  # person pattern needs an e-wallet QR
])
def test_guess_party_type(name, acquirer, expected):
    assert parties.guess_party_type(qris(name, acquirer))[0] == expected


def test_transfers_are_people_and_keys():
    t = ParsedTransaction(TxnType.TRANSFER_OUT, Direction.OUT, 50_000, datetime(2026, 9, 25),
                          counterparty="Budi", counterparty_account="BCA - ****1234")
    assert parties.guess_party_type(t)[0] == PartyType.PERSON
    assert parties.counterparty_key(t) == "acct:BCA - ****1234"
    assert parties.counterparty_key(qris("X", pan="936123")) == "pan:936123"
    topup = ParsedTransaction(TxnType.TOPUP, Direction.OUT, 50_000, datetime(2026, 9, 25), counterparty="GOPAY")
    assert parties.counterparty_key(topup) is None


@pytest.fixture
def synced(tmp_path):
    for f in FIXTURES.glob("livin_*.eml"):
        shutil.copy(f, tmp_path / f.name)
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with session_scope(engine) as s:
        pipeline.sync(s, EmlFolderSource(tmp_path, TZ), SETTINGS)
    return engine


def _txn(session, name_part: str) -> Transaction:
    return session.query(Transaction).filter(Transaction.counterparty.contains(name_part)).one()


def test_friend_qr_goes_to_transfer_ke_orang(synced):
    with session_scope(synced) as s:
        friend = _txn(s, "Teman Contoh")
        assert friend.category == "Transfer ke Orang"
        assert friend.counterparty_key.startswith("pan:") and friend.acquirer == "GoPay"
        assert _txn(s, "fotokopi").category == "Pendidikan"
        assert s.query(Counterparty).count() == 2  # top-ups don't create counterparties


def test_tag_applies_to_history(synced):
    with session_scope(synced) as s:
        [store] = parties.find(s, "fotokopi")
        parties.tag(store, PartyType.PERSON)
        s.flush()
        assert pipeline.reclassify_party(s, SETTINGS, store.key) == 1
        assert _txn(s, "fotokopi").category == "Transfer ke Orang"
        assert _txn(s, "fotokopi").category_source == "user"

        [friend] = parties.find(s, "teman contoh")
        parties.tag(friend, category="Makan & Minum")
        s.flush()
        pipeline.reclassify_party(s, SETTINGS, friend.key)
        assert _txn(s, "Teman Contoh").category == "Makan & Minum"

    # Tags survive a full re-parse.
    with session_scope(synced) as s:
        pipeline.reparse(s, SETTINGS, only_unparsed=False)
        assert _txn(s, "fotokopi").category == "Transfer ke Orang"
        assert _txn(s, "Teman Contoh").category == "Makan & Minum"


def test_old_database_gets_new_columns(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as conn:  # transactions table as it was before counterparties existed
        conn.execute(text("CREATE TABLE transactions (id INTEGER PRIMARY KEY, raw_email_id INTEGER, bank VARCHAR,"
                          " txn_type VARCHAR, direction VARCHAR, kind VARCHAR, amount INTEGER, fee INTEGER,"
                          " counterparty VARCHAR, counterparty_account VARCHAR, reference VARCHAR,"
                          " occurred_at DATETIME, category VARCHAR, category_source VARCHAR, note TEXT)"))
    init_db(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("transactions")}
    assert {"counterparty_key", "counterparty_id", "acquirer"} <= columns
    assert inspect(engine).has_table("counterparties")
