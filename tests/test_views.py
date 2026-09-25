"""Report cards v2: names per category, drill-down buttons, asking about new stores, chart image."""

import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from expense_tracker import pipeline
from expense_tracker.bot import actions
from expense_tracker.config import Settings
from expense_tracker.db import init_db, make_engine, session_scope
from expense_tracker.models import Counterparty, Kind, PartyType, RawEmail, Transaction, TxnType
from expense_tracker.parsing.base import ParsedTransaction
from expense_tracker.sources.eml_folder import EmlFolderSource
from expense_tracker.summary import range_summary, rupiah_short

TZ = ZoneInfo("Asia/Jakarta")
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 26)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, own_names="PENGGUNA DEMO", monthly_budget=2_500_000, period_start_day=25, **kw)


@pytest.fixture
def engine(tmp_path):
    for f in FIXTURES.glob("livin_*.eml"):
        shutil.copy(f, tmp_path / f.name)
    e = make_engine("sqlite:///:memory:")
    init_db(e)
    with session_scope(e) as db:
        pipeline.sync(db, EmlFolderSource(tmp_path, TZ), settings())
    return e


def add_unknown(db, tmp_path, name="Kedai Misterius", pan="936000111", hhmm="14:02:00"):
    """A real Livin' QRIS email (fixture layout) for a store no rule knows, synced through the pipeline."""
    from email import message_from_bytes, policy
    from email.message import EmailMessage
    from email.utils import make_msgid

    src = message_from_bytes((FIXTURES / "livin_qris_mandiri_acquirer.eml").read_bytes(), policy=policy.default)
    html = src.get_body(("html",)).get_content()
    html = html.replace("fotokopi contoh print", name).replace("4935950453394500220", pan)
    html = html.replace("25 Sep 2026", "26 Sep 2026").replace("10:55:44", hhmm)
    msg = EmailMessage()
    for header in ("From", "To", "Subject"):
        msg[header] = str(src[header])
    msg["Date"] = "Sat, 26 Sep 2026 14:05:00 +0700"
    msg["Message-ID"] = make_msgid(domain="test.example")
    msg.set_content(html, subtype="html")
    folder = tmp_path / f"new_{pan}"
    folder.mkdir()
    (folder / "x.eml").write_bytes(bytes(msg))
    pipeline.sync(db, EmlFolderSource(folder, TZ), settings(), since=datetime(1970, 1, 1))
    return db.query(Transaction).filter_by(counterparty=name).one()


def test_rupiah_short():
    assert [rupiah_short(v) for v in (500, 43_000, 62_401, 999_999, 1_200_000)] == \
        ["Rp500", "Rp43rb", "Rp62,4rb", "Rp1jt", "Rp1,2jt"]


def test_range_summary_names_and_top_merchants(engine):
    with session_scope(engine) as db:
        s = range_summary(db, date(2026, 9, 25), date(2026, 10, 25), TODAY)
    by_cat = {b.category: b for b in s.categories}
    assert [i.name for i in by_cat["Pendidikan"].items] == ["fotokopi contoh print"]
    assert by_cat["Top-up E-wallet"].items[0].count == 2
    # Top-ups and friends are not "stores you visit most".
    assert [i.name for i in s.top_merchants] == ["fotokopi contoh print"]
    assert s.spent == sum(b.total for b in s.categories) == sum(s.per_day.values())


def test_period_card_shows_names_budget_and_buttons(engine):
    with session_scope(engine) as db:
        reply = actions.report(db, settings(), "p", TODAY)
    assert "25 Sep s.d. 24 Okt 2026" in reply.text
    assert "fotokopi contoh print" in reply.text and "Teman Contoh, Edukasi" in reply.text
    assert "Rp2.500.000" in reply.text and "Sisa hari" in reply.text
    tabs, *cats, last = reply.buttons
    assert [label for label, _ in tabs] == ["Hari ini", "7 hari", "• Periode"]
    assert any(label.startswith("Pendidikan") for row in cats for label, _ in row)
    assert last == [("Grafik", "f:p")]


def test_drill_down_and_back(engine):
    s = settings()
    with session_scope(engine) as db:
        report = actions.report(db, s, "w", TODAY)
        data = next(d for row in report.buttons for label, d in row if label.startswith("Pendidikan"))
        detail = actions.handle_callback(db, s, data, TODAY)
        assert "fotokopi" in detail.text and "1 transaksi" in detail.text
        edit, back = detail.buttons[0][0], detail.buttons[-1][0]
        assert edit[1].endswith(":w") and back == ("Kembali", "v:w")

        # Change category from the list, "just this once", then back to the report.
        picker = actions.handle_callback(db, s, edit[1], TODAY)
        idx = actions.CATEGORIES.index("Belanja Harian")
        ask = actions.handle_callback(db, s, next(d for row in picker.buttons for l, d in row if d.startswith(f"s:") and d.split(":")[2] == str(idx)), TODAY)
        once = actions.handle_callback(db, s, ask.buttons[0][1][1], TODAY)
        assert "Belanja Harian" in once.text and once.buttons == [[("Kembali ke laporan", "v:w")]]
        again = actions.handle_callback(db, s, "v:w", TODAY)
        assert "Belanja Harian" in again.text


def test_new_store_is_asked_and_remembered_by_qr(engine, tmp_path):
    s = settings()
    with session_scope(engine) as db:
        t = add_unknown(db, tmp_path)
        assert actions.needs_category(t)
        alert = actions.alert_reply(db, s, t)
        assert "Toko baru" in alert.text
        labels = [label for row in alert.buttons for label, _ in row]
        assert "Makan" in labels and "Ke teman" in labels

        idx = actions.CATEGORIES.index("Makan & Minum")
        done = actions.handle_callback(db, s, f"q:{t.id}:{idx}", TODAY)
        assert "Diingat untuk seterusnya" in done.text and done.buttons == []
        party = db.query(Counterparty).filter_by(key=t.counterparty_key).one()
        assert (party.category, party.party_type, party.party_source) == ("Makan & Minum", PartyType.MERCHANT, "user")

    # Next payment to the same QR (even under a different display name) is filed without asking.
    with session_scope(engine) as db:
        later = ParsedTransaction(TxnType.QRIS, "out", 20_000, datetime(2026, 9, 27, 9, 0),
                                  counterparty="KEDAI MISTERIUS 2", counterparty_id="936000111", acquirer="Bank Mandiri")
        from expense_tracker import parties
        from expense_tracker.categorize import classify

        cls = classify(later, s, [], parties.get_or_create(db, later))
        assert cls.category == "Makan & Minum"


def test_new_store_answered_as_friend(engine, tmp_path):
    s = settings()
    with session_scope(engine) as db:
        t = add_unknown(db, tmp_path, name="Budi Kurniawan", pan="936000222")
        idx = actions.CATEGORIES.index("Transfer ke Orang")
        done = actions.handle_callback(db, s, f"q:{t.id}:{idx}", TODAY)
        assert "teman" in done.text
        party = db.query(Counterparty).filter_by(key=t.counterparty_key).one()
        assert party.party_type == PartyType.PERSON and party.category is None
        assert t.category == "Transfer ke Orang"


def test_chart_png(engine):
    with session_scope(engine) as db:
        reply = actions.handle_callback(db, settings(), "f:p", TODAY)
        day_chart = actions.handle_callback(db, settings(), "f:d", TODAY)
    assert reply.photo[:8] == b"\x89PNG\r\n\x1a\n" and len(reply.photo) > 10_000
    assert "Rp2.500.000" in reply.text
    assert "7 hari" in day_chart.text  # a single day is shown as the last 7 days


def test_bad_view_callbacks(engine):
    with session_scope(engine) as db:
        assert actions.handle_callback(db, settings(), "v:zz", TODAY) is None
        # Unknown category hash falls back to the report.
        assert "Per kategori" in actions.handle_callback(db, settings(), "g:p:deadbeef", TODAY).text
