"""Command line interface. Run `tracker --help`."""

from datetime import date, datetime

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from . import parties, pipeline, summary
from .budget import budget_status
from .categorize import load_user_rules
from .config import get_settings
from .db import init_db, make_engine, session_scope
from .bot.texts import period_label
from .period import period_for, period_starting_in
from .models import CategoryRule, Counterparty, Kind, ParseStatus, PartyType, RawEmail, Transaction

app = typer.Typer(help="Automatic expense tracker (Mandiri emails).", no_args_is_help=True)
summary_app = typer.Typer(help="Daily and monthly totals.", no_args_is_help=True)
rule_app = typer.Typer(help="Your own category rules.", no_args_is_help=True)
app.add_typer(summary_app, name="summary")
app.add_typer(rule_app, name="rule")
console = Console()
rp = summary.rupiah


def _engine(db: str | None = None):
    engine = make_engine(db)
    init_db(engine)
    return engine


def _today() -> date:
    return datetime.now(get_settings().tz).date()


DbOption = typer.Option(None, "--db", help="Override database URL, e.g. sqlite:///data/demo.db")


@app.command()
def init(db: str = DbOption):
    """Create the database tables."""
    _engine(db)
    console.print("[green]Database ready.[/]")


@app.command()
def auth():
    """Log in to Gmail once (opens a browser) and save the token."""
    from .sources.gmail import get_credentials

    s = get_settings()
    get_credentials(s.gmail_credentials, s.gmail_token)
    console.print(f"[green]Gmail connected.[/] Token saved to {s.gmail_token}")


@app.command()
def sync(
    source: str = typer.Option("gmail", help="gmail or eml"),
    path: str = typer.Option("samples", help="Folder of .eml files (for --source eml)"),
    since: str = typer.Option(None, help="Only emails from this date, YYYY-MM-DD"),
    full: bool = typer.Option(False, help="Ignore the last sync point and fetch everything"),
    db: str = DbOption,
):
    """Fetch new bank emails and turn them into transactions."""
    s = get_settings()
    if source == "gmail":
        from .sources.gmail import GmailSource

        src = GmailSource(s.gmail_credentials, s.gmail_token, s.gmail_query, s.tz)
    elif source == "eml":
        from .sources.eml_folder import EmlFolderSource

        src = EmlFolderSource(path, s.tz)
    else:
        raise typer.BadParameter("source must be gmail or eml")

    since_dt = datetime.fromisoformat(since) if since else (datetime(1970, 1, 1) if full else None)
    with session_scope(_engine(db)) as session:
        with console.status("Syncing..."):
            stats = pipeline.sync(session, src, s, since_dt)
    console.print(
        f"Fetched {stats.fetched}, new {stats.new}: "
        f"[green]{stats.parsed} parsed[/], {stats.skipped} skipped, [red]{stats.failed} failed[/]"
    )
    for note in stats.failures[:10]:
        console.print(f"  [red]x[/] {note}")
    if stats.failed:
        console.print("See them with `tracker emails --status failed`.")


@app.command()
def reparse(all_emails: bool = typer.Option(False, "--all", help="Re-parse every email, not just failed ones"),
            db: str = DbOption):
    """Run the parser again over stored emails (after improving it)."""
    with session_scope(_engine(db)) as session:
        stats = pipeline.reparse(session, get_settings(), only_unparsed=not all_emails)
    console.print(f"Re-parsed {stats.fetched}: [green]{stats.parsed} ok[/], {stats.skipped} skipped, "
                  f"[red]{stats.failed} failed[/]")


@app.command()
def emails(status: str = typer.Option(None, help="parsed | skipped | failed"), limit: int = 20, db: str = DbOption):
    """List stored emails and how they were parsed (for debugging the parser)."""
    with session_scope(_engine(db)) as session:
        query = select(RawEmail).order_by(RawEmail.received_at.desc()).limit(limit)
        if status:
            query = query.where(RawEmail.parse_status == ParseStatus(status))
        table = Table("id", "received", "status", "subject", "note")
        for raw in session.scalars(query):
            table.add_row(str(raw.id), raw.received_at.strftime("%Y-%m-%d %H:%M"), raw.parse_status,
                          raw.subject[:50], (raw.parse_error or "")[:60])
        console.print(table)
        console.print(pipeline.counts_by_status(session))


def _print_day(day: date, db: str | None) -> None:
    s = get_settings()
    with session_scope(_engine(db)) as session:
        d = summary.day_summary(session, day)
        status = budget_status(session, s, day)
        console.rule(f"{day:%A, %d %B %Y}")
        console.print(f"Pengeluaran: [bold]{rp(d.spent)}[/]" + (f"   Pemasukan: {rp(d.income)}" if d.income else ""))
        if status.enabled:
            left = status.left_today
            color, label = ("green", "Sisa budget") if left >= 0 else ("red", "Lewat budget")
            console.print(f"{label}: [{color}]{rp(abs(left))}[/] (jatah {rp(status.allowance)}"
                          + (f", top-up {rp(status.topups_today)} dihitung ke bulan" if status.topups_today else "")
                          + ")")
            if status.mode == "monthly" and status.days_left > 1:
                console.print(f"Jatah besok: {rp(status.tomorrow_allowance)}")
        table = Table("jam", "tipe", "ke/dari", "kategori", "jumlah", "")
        for t in d.transactions:
            mark = "" if t.kind == Kind.EXPENSE else t.kind
            sign = "+" if t.kind == Kind.INCOME else ""
            table.add_row(t.occurred_at.strftime("%H:%M"), t.txn_type, t.counterparty[:28], t.category,
                          sign + rp(t.total_out if t.kind == Kind.EXPENSE else t.amount), mark)
        if d.transactions:
            console.print(table)
        else:
            console.print("Belum ada transaksi.")


@summary_app.command("today")
def summary_today(db: str = DbOption):
    """Today's spending."""
    _print_day(_today(), db)


@summary_app.command("day")
def summary_day(day: str = typer.Argument(..., help="YYYY-MM-DD"), db: str = DbOption):
    """Spending on a given day."""
    _print_day(date.fromisoformat(day), db)


@summary_app.command("month")
def summary_month(month: str = typer.Argument(None, help="YYYY-MM (the period starting that month), default now"),
                  db: str = DbOption):
    """Totals for this budget period (calendar month, or from TRACKER_PERIOD_START_DAY), by category and day."""
    today = _today()
    start_day = get_settings().period_start_day
    if month:
        year, mon = (int(x) for x in month.split("-"))
        period = period_starting_in(year, mon, start_day)
    else:
        period = period_for(today, start_day)
    with session_scope(_engine(db)) as session:
        m = summary.period_summary(session, period, today)
    console.rule(period_label(period))
    console.print(f"Pengeluaran: [bold]{rp(m.spent)}[/]   Pemasukan: {rp(m.income)}   "
                  f"Rata-rata/hari: {rp(m.daily_average)}   ({m.transaction_count} transaksi)")

    cats = Table("kategori", "total", "%")
    for name, value in m.by_category:
        cats.add_row(name, rp(value), f"{value / m.spent:.0%}" if m.spent else "-")
    for name, value in m.excluded:
        cats.add_row(f"[dim]{name} (tidak dihitung)[/]", f"[dim]{rp(value)}[/]", "")
    console.print(cats)

    top = Table("paling sering / terbesar", "total", "kali")
    for name, value, count in m.top_counterparties:
        top.add_row(name[:30], rp(value), str(count))
    console.print(top)

    peak = max(m.per_day.values(), default=0) or 1
    days = Table("tanggal", "total", "", box=None)
    for day, value in m.per_day.items():
        if day > today:
            break
        bar = "#" * round(20 * value / peak)
        days.add_row(f"{day:%d %a}", rp(value), f"[cyan]{bar}[/]")
    console.print(days)


@rule_app.command("add")
def rule_add(pattern: str, category: str, priority: int = 100, db: str = DbOption):
    """Always put transactions whose name contains PATTERN into CATEGORY (applies to past ones too)."""
    with session_scope(_engine(db)) as session:
        session.add(CategoryRule(pattern=pattern, category=category, priority=priority))
        session.flush()
        stats = pipeline.reparse(session, get_settings(), only_unparsed=False)
    console.print(f"Rule saved. Re-applied to {stats.parsed} transactions.")


@rule_app.command("list")
def rule_list(db: str = DbOption):
    with session_scope(_engine(db)) as session:
        table = Table("id", "pattern", "category", "priority")
        for r in load_user_rules(session):
            table.add_row(str(r.id), r.pattern, r.category, str(r.priority))
        console.print(table)


@app.command("parties")
def parties_list(
    guesses: bool = typer.Option(False, "--guesses", help="Only ones you haven't confirmed yet"),
    people: bool = typer.Option(False, "--people", help="Only people"),
    db: str = DbOption,
):
    """Who you pay: friends vs stores, with totals. Confirm guesses with `tracker tag`."""
    with session_scope(_engine(db)) as session:
        totals = dict(
            (key, (total, count)) for key, total, count in session.execute(
                select(Transaction.counterparty_key, func.sum(Transaction.amount + Transaction.fee), func.count())
                .where(Transaction.counterparty_key.is_not(None))
                .group_by(Transaction.counterparty_key)
            )
        )
        query = select(Counterparty).order_by(Counterparty.id)
        if guesses:
            query = query.where(Counterparty.party_source == "guess")
        if people:
            query = query.where(Counterparty.party_type == PartyType.PERSON)
        table = Table("id", "name", "type", "", "category", "total", "x", "why")
        for party in session.scalars(query):
            total, count = totals.get(party.key, (0, 0))
            kind = "[magenta]teman[/]" if party.party_type == PartyType.PERSON else "toko"
            source = "[dim]guess[/]" if party.party_source == "guess" else "[green]ok[/]"
            table.add_row(str(party.id), party.name[:32], kind, source, party.category or "", rp(total or 0),
                          str(count), party.guess_reason if party.party_source == "guess" else "")
        console.print(table)
        console.print("Tag with: tracker tag <id or name> --teman | --toko [--category NAME]")


@app.command()
def tag(
    who: str = typer.Argument(..., help="id from `tracker parties`, or part of the name"),
    teman: bool = typer.Option(False, "--teman", "--person", help="This is a person (friend/family)"),
    toko: bool = typer.Option(False, "--toko", "--store", help="This is a store/merchant"),
    category: str = typer.Option(None, help="Always use this category for them ('' to clear)"),
    db: str = DbOption,
):
    """Confirm who someone is. Applies to their past transactions and every future one."""
    if teman and toko:
        raise typer.BadParameter("pick one: --teman or --toko")
    if not (teman or toko or category is not None):
        raise typer.BadParameter("give --teman, --toko and/or --category")
    with session_scope(_engine(db)) as session:
        matches = parties.find(session, who)
        if not matches:
            console.print(f"[red]No one matches {who!r}.[/] See `tracker parties`.")
            raise typer.Exit(1)
        if len(matches) > 1:
            console.print(f"[yellow]{len(matches)} match {who!r}, use the id:[/]")
            for party in matches:
                console.print(f"  {party.id}  {party.name}")
            raise typer.Exit(1)
        party = matches[0]
        party_type = PartyType.PERSON if teman else PartyType.MERCHANT if toko else None
        parties.tag(party, party_type, category)
        session.flush()
        n = pipeline.reclassify_party(session, get_settings(), party.key)
        label = "teman" if party.party_type == PartyType.PERSON else "toko"
        extra = f", category {party.category}" if party.category else ""
        console.print(f"[green]{party.name}[/] = {label}{extra}. Updated {n} transactions.")


@app.command()
def synthetic(out: str = typer.Option("data/synthetic", help="Folder to write .eml files to"),
              days: int = 60, seed: int = 42):
    """Generate fake bank emails for testing and the demo."""
    from .synthetic import generate

    n = generate(out, days=days, seed=seed, end=_today(), tz=get_settings().tz)
    console.print(f"Wrote {n} synthetic emails to {out}")


@app.command()
def bot():
    """Run the Telegram bot (checks email every few minutes, sends alerts and a daily summary)."""
    from .bot.app import run

    run(get_settings())


@app.command()
def status(db: str = DbOption):
    """Quick health check: counts and last transaction."""
    with session_scope(_engine(db)) as session:
        counts = pipeline.counts_by_status(session)
        last = session.scalars(select(Transaction).order_by(Transaction.occurred_at.desc()).limit(1)).first()
    console.print(f"Emails: {counts or 'none yet'}")
    if last:
        console.print(f"Last transaction: {last.occurred_at:%Y-%m-%d %H:%M} {last.counterparty} {rp(last.total_out)}")


if __name__ == "__main__":
    app()
