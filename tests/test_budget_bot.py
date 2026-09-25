import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from expense_tracker import pipeline
from expense_tracker.bot import actions
from expense_tracker.budget import budget_status
from expense_tracker.config import Settings
from expense_tracker.db import init_db, make_engine, session_scope
from expense_tracker.models import (CategoryRule, Counterparty, Direction, Kind, PartyType, RawEmail,
                                    Transaction, TxnType)
from expense_tracker.sources.eml_folder import EmlFolderSource

TZ = ZoneInfo("Asia/Jakarta")
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 25)  # 30-day month -> 6 days left including today
NOW = datetime(2026, 9, 25, 20, 0)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, own_names="PENGGUNA DEMO", **kw)


@pytest.fixture
def engine():
    e = make_engine("sqlite:///:memory:")
    init_db(e)
    return e


_n = 0


def add(session, when: datetime, amount: int, txn_type=TxnType.QRIS, kind=Kind.EXPENSE, fee=0,
        counterparty="WARUNG", direction=Direction.OUT, key=None, category="Makan & Minum") -> Transaction:
    global _n
    _n += 1
    raw = RawEmail(message_id=f"m{_n}", source="test", received_at=when)
    t = Transaction(raw_email=raw, txn_type=txn_type, direction=direction, kind=kind, amount=amount, fee=fee,
                    counterparty=counterparty, occurred_at=when, category=category, counterparty_key=key)
    session.add_all([raw, t])
    session.flush()
    return t


# ------------------------------------------------------------------ budget

def test_monthly_allowance_and_topups(engine):
    s = settings(monthly_budget=1_500_000)
    with session_scope(engine) as db:
        add(db, datetime(2026, 9, 3, 12), 900_000)                       # before today
        add(db, datetime(2026, 9, 25, 9), 40_000)                        # today
        add(db, datetime(2026, 9, 25, 10), 100_000, TxnType.TOPUP, fee=1_200, counterparty="GOPAY")
        add(db, datetime(2026, 9, 25, 11), 50_000, TxnType.TRANSFER_IN, Kind.INCOME, direction=Direction.IN)
        st = budget_status(db, s, TODAY)

    assert st.mode == "monthly" and st.days_left == 6
    assert st.allowance == (1_500_000 - 900_000) // 6 == 100_000
    assert st.spent_today == 40_000              # top-up not in today's number
    assert st.topups_today == 101_200            # ...but tracked
    assert st.month_spent == 900_000 + 40_000 + 101_200
    assert st.left_today == 60_000
    # Tomorrow's allowance absorbs today's spending, top-up included.
    assert st.tomorrow_allowance == (1_500_000 - st.month_spent) // 5


def test_overspending_shrinks_tomorrow_and_fixed_mode(engine):
    with session_scope(engine) as db:
        add(db, datetime(2026, 9, 25, 9), 150_000)
        monthly = budget_status(db, settings(monthly_budget=600_000), TODAY)
        fixed = budget_status(db, settings(daily_budget=50_000), TODAY)
        off = budget_status(db, settings(), TODAY)
    assert monthly.allowance == 100_000 and monthly.left_today == -50_000
    assert monthly.tomorrow_allowance == (600_000 - 150_000) // 5 == 90_000
    assert (fixed.mode, fixed.allowance, fixed.left_today) == ("daily", 50_000, -100_000)
    assert off.enabled is False


def test_budget_warnings_fire_once(engine):
    s = settings(daily_budget=100_000)
    with session_scope(engine) as db:
        add(db, datetime(2026, 9, 25, 9), 85_000)
        first = actions.budget_warnings(db, s, TODAY)
        again = actions.budget_warnings(db, s, TODAY)
        add(db, datetime(2026, 9, 25, 12), 30_000)
        over = actions.budget_warnings(db, s, TODAY)
        over_again = actions.budget_warnings(db, s, TODAY)
    assert len(first) == 1 and "tinggal" in first[0].text
    assert again == []
    assert len(over) == 1 and "lewat" in over[0].text
    assert over_again == []


# ------------------------------------------------------------------ alerts

def test_pending_alerts_skip_old_and_internal(engine):
    s = settings(monthly_budget=1_500_000)
    with session_scope(engine) as db:
        old = add(db, datetime(2026, 9, 1, 9), 20_000)                   # backfill: silent
        internal = add(db, datetime(2026, 9, 25, 9), 500_000, TxnType.TRANSFER_OUT, Kind.INTERNAL)
        new = add(db, datetime(2026, 9, 25, 19), 25_000, counterparty="KOPI <b>X</b>")
        alerts = actions.pending_alerts(db, s, NOW)
        assert [txn_id for txn_id, _ in alerts] == [new.id]
        assert old.notified_at is not None and internal.notified_at is not None
        text = alerts[0][1].text
        assert "-Rp25.000" in text and "KOPI &lt;b&gt;X&lt;/b&gt;" in text and "Sisa hari ini" in text
        actions.mark_notified(db, new.id, NOW)
        assert actions.pending_alerts(db, s, NOW) == []


def test_topup_alert_mentions_month(engine):
    with session_scope(engine) as db:
        add(db, datetime(2026, 9, 25, 17), 50_000, TxnType.TOPUP, fee=1_200, counterparty="GOPAY")
        [(_, reply)] = actions.pending_alerts(db, settings(monthly_budget=1_500_000), NOW)
    assert "Top-up GOPAY" in reply.text and "bukan ke hari ini" in reply.text and "Jatah besok" in reply.text


# ------------------------------------------------------------------ buttons, on real fixture emails

@pytest.fixture
def synced(tmp_path, engine):
    for f in FIXTURES.glob("livin_*.eml"):
        shutil.copy(f, tmp_path / f.name)
    with session_scope(engine) as db:
        pipeline.sync(db, EmlFolderSource(tmp_path, TZ), settings())
    return engine


def _txn(db, part):
    return db.query(Transaction).filter(Transaction.counterparty.contains(part)).one()


def test_guess_buttons_and_tag_teman(synced):
    s = settings()
    with session_scope(synced) as db:
        store = _txn(db, "fotokopi")
        reply = actions.alert_reply(db, s, store)
        assert ("Ini teman", f"p:{store.id}:t") in reply.buttons[1]
        assert "Tebakan: toko" in reply.text

        reply = actions.handle_callback(db, s, f"p:{store.id}:t")
        assert "Transfer ke Orang" in reply.text
        assert len(reply.buttons) == 1  # confirmed: no more teman/toko buttons
        party = db.query(Counterparty).filter_by(key=store.counterparty_key).one()
        assert (party.party_type, party.party_source) == (PartyType.PERSON, "user")


def test_change_category_always_remembers_per_qr(synced):
    s = settings()
    with session_scope(synced) as db:
        friend = _txn(db, "Teman Contoh")
        menu = actions.handle_callback(db, s, f"c:{friend.id}")
        labels = [label for row in menu.buttons for label, _ in row]
        assert "Makan & Minum" in labels and labels[-1] == "Batal"

        idx = actions.CATEGORIES.index("Makan & Minum")
        ask = actions.handle_callback(db, s, f"s:{friend.id}:{idx}")
        assert friend.category == "Makan & Minum" and friend.category_source == "user"
        assert [label for label, _ in ask.buttons[0]] == ["Ya, selalu", "Sekali ini aja"]

        done = actions.handle_callback(db, s, f"a:{friend.id}")
        assert "selalu Makan & Minum" in done.text and done.buttons == []
        party = db.query(Counterparty).filter_by(key=friend.counterparty_key).one()
        assert party.category == "Makan & Minum"


def test_always_without_party_creates_rule(synced):
    s = settings()
    with session_scope(synced) as db:
        topup = db.query(Transaction).filter_by(txn_type=TxnType.TOPUP).first()
        idx = actions.CATEGORIES.index("Transportasi")
        actions.handle_callback(db, s, f"s:{topup.id}:{idx}")
        actions.handle_callback(db, s, f"a:{topup.id}")
        rule = db.query(CategoryRule).one()
        assert (rule.pattern, rule.category) == ("GOPAY", "Transportasi")
        assert all(t.category == "Transportasi" for t in db.query(Transaction).filter_by(txn_type=TxnType.TOPUP))


def test_bad_callback_data(synced):
    with session_scope(synced) as db:
        assert actions.handle_callback(db, settings(), "zzz") is None
        assert "tidak ada" in actions.handle_callback(db, settings(), "c:99999").text


def test_reports_render(synced):
    s = settings(monthly_budget=1_500_000)
    with session_scope(synced) as db:
        day = actions.today_report(db, s, TODAY).text
        month = actions.month_report(db, s, TODAY).text
        budget = actions.budget_report(db, s, TODAY).text
    assert "Jumat, 25 Sep" in day and "Jatah besok" in day
    assert "September 2026" in month and "Budget" in month and "fotokopi contoh print" in month
    assert "Sisa 6 hari" in budget


def test_telegram_app_builds_without_network(tmp_path):
    from expense_tracker.bot.app import build_app

    s = settings(telegram_token="123456:TEST", telegram_chat_id=42, db_url=f"sqlite:///{tmp_path}/bot.db",
                 bot_source="eml", bot_eml_path=str(tmp_path))
    app = build_app(s)
    commands = {c for h in app.handlers[0] for c in getattr(h, "commands", [])}
    assert {"start", "hariini", "bulanini", "budget", "sync", "help"} <= commands
    assert len(app.job_queue.jobs()) == 2


def test_sync_job_sends_alerts_through_telegram_layer(tmp_path):
    """Runs the real job code with a fake bot: sync -> alerts -> budget warning, then a button press."""
    import asyncio
    from types import SimpleNamespace

    from expense_tracker.bot import app as bot_app

    for f in FIXTURES.glob("livin_*.eml"):
        shutil.copy(f, tmp_path / f.name)
    s = settings(telegram_token="123456:TEST", telegram_chat_id=42, db_url=f"sqlite:///{tmp_path}/bot.db",
                 bot_source="eml", bot_eml_path=str(tmp_path), daily_budget=20_000,
                 alert_max_age_hours=24 * 365 * 10)  # fixtures are from 2026-09-25; alert them all

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    application = bot_app.build_app(s)
    context = SimpleNamespace(bot=FakeBot(), bot_data=application.bot_data)
    stats = asyncio.run(bot_app.sync_and_alert(context))

    assert stats.new == 4
    alerts = [text for _, text, _ in sent if text.startswith("<b>-")]
    assert len(alerts) == 4 and all(chat == 42 for chat, _, _ in sent)
    first_markup = next(markup for _, text, markup in sent if "fotokopi" in text)
    assert first_markup.inline_keyboard[0][0].text == "Ganti kategori"

    # Second run: nothing new, no duplicate alerts.
    sent.clear()
    asyncio.run(bot_app.sync_and_alert(context))
    assert sent == []

    # Press "Ini teman" on the print shop alert.
    data = first_markup.inline_keyboard[1][0].callback_data
    edits = []

    class FakeQuery:
        message = SimpleNamespace(chat=SimpleNamespace(id=42))
        callback_data = data

        async def answer(self):
            pass

        async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
            edits.append(text)

    query = FakeQuery()
    query.data = data
    asyncio.run(bot_app.on_button(SimpleNamespace(callback_query=query), context))
    assert edits and "Transfer ke Orang" in edits[0]


def test_command_menu_matches_handlers():
    import asyncio

    from expense_tracker.bot import app as bot_app
    from expense_tracker.bot import texts

    names = [name for name, _ in texts.COMMANDS]
    assert names == ["hariini", "minggu", "bulanini", "grafik", "budget", "sync", "help"]
    assert all(1 <= len(desc) <= 256 for _, desc in texts.COMMANDS)

    sent = []

    class FakeBot:
        async def set_my_commands(self, commands):
            sent.extend(commands)

    class FakeApp:
        bot = FakeBot()

    asyncio.run(bot_app.set_commands(FakeApp()))
    assert [c.command for c in sent] == names
