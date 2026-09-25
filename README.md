# Expense Tracker

Tracks daily and monthly spending automatically from Mandiri (Livin') transaction emails. No manual input for normal transactions.

```
Gmail (Mandiri emails) -> Parser -> Categorizer -> SQLite -> Summary (CLI now, Telegram bot + web app later)
```

**Status: Phase 1 (core pipeline) done except the first real Gmail sync; Phase 2 (Telegram bot) built.** Parser verified on real Livin' QRIS and GoPay top-up emails; other types (transfer, bills, ATM, money in) still need samples.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | Gmail reader, parser, SQLite, rule categorizer, CLI summaries, synthetic data | waiting on real Gmail sync |
| 2 | Telegram bot: new-expense alerts, one-tap categories, daily budget, end-of-day summary | built |
| 3 | Utang/piutang: loans, split bills, auto-matching repayments, reminders | |
| 4 | Web app (PWA): FastAPI + Next.js dashboard | |
| 5 | ML categorizer, end-of-month forecast, spike detection | |
| 6 | Deploy, public demo on synthetic data | |

## Setup (Windows, PowerShell)

```powershell
cd expense-tracker
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy .env.example .env      # then edit .env (at least TRACKER_OWN_NAMES)
tracker init
```

Try it without Gmail first, on fake data:

```powershell
tracker synthetic --days 60
tracker sync --source eml --path data/synthetic
tracker summary today
tracker summary month
```

(`data/` is git-ignored. Delete `data/tracker.db` to start fresh before syncing real emails.)

## Sample emails (needed to finish the parser)

1. In Gmail on a browser, open a Mandiri transaction email.
2. Click the three dots (More) next to Reply, then **Download message**. It saves a `.eml` file.
3. Put it in `samples/`. One or two per type is enough: QRIS, transfer out, transfer in, top-up, PLN/pulsa, ATM withdrawal, plus any non-transaction Mandiri email (promo, e-statement).

`samples/` is git-ignored, so real emails never end up on GitHub. You can already test them:

```powershell
tracker sync --source eml --path samples --db sqlite:///data/samples.db
tracker emails --db sqlite:///data/samples.db
```

## Gmail setup (one time)

1. Go to https://console.cloud.google.com and create a project (e.g. `expense-tracker`).
2. **APIs & Services > Library**: enable **Gmail API**.
3. **Google Auth Platform > Branding / OAuth consent screen**: user type **External**, fill in the app name and your email.
4. **Audience**: add your Gmail as a test user, then click **Publish app** so it is **In production**. In "Testing" mode Google expires the login every 7 days. It stays unverified, which is fine for personal use: on login click **Advanced > Go to expense-tracker (unsafe)**.
5. **Credentials > Create credentials > OAuth client ID > Desktop app**. Download the JSON and save it as `credentials.json` in this folder (it is git-ignored).
6. Run:

```powershell
tracker auth           # opens a browser once, saves data/token.json
tracker sync           # first run imports all past Mandiri emails
tracker summary month
```

After that, `tracker sync` only fetches new emails.

## Telegram bot setup

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name. Copy the token into `.env` as `TRACKER_TELEGRAM_TOKEN`.
2. Set `TRACKER_MONTHLY_BUDGET` (e.g. `1500000`). To run the budget from a day other than the 1st, set `TRACKER_PERIOD_START_DAY` (e.g. `25` for the 25th to the 24th of next month); `/bulanini`, `/budget` and `tracker summary month` then follow that period.
3. Run `tracker bot`, open your bot in Telegram and send `/start`. It replies with your chat ID.
4. Put it in `.env` as `TRACKER_TELEGRAM_CHAT_ID`, stop the bot (Ctrl+C) and run `tracker bot` again. Only that chat can use it.

What it does while running:

- Checks email every `TRACKER_SYNC_EVERY_MINUTES` and sends each new transaction, with buttons to change the category (once or always for that name/QR) and to confirm friend vs store.
- A store no rule knows yet is asked about right away ("Toko baru. Ini kategori apa?"). One tap files it, and the answer is remembered for that QR code from then on. Answering "Ke teman" marks it as a person.
- Reports are cards: numbers in aligned columns, a budget progress bar, and the names under each category, so a wrong category is easy to spot. Buttons switch between Hari ini / 7 hari / Periode; tap a category to see its transactions and fix any of them. "Grafik" sends a chart image (per category and per day, with the average daily budget line).
- Budget: today's allowance = (monthly budget - spent before today) / days left. Top-ups don't count toward today's warning, only the month, so they get spread over the remaining days. Warns once at 80% and once when over.
- Sends a summary every day at `TRACKER_SUMMARY_TIME`.
- Commands: `/hariini`, `/minggu`, `/bulanini`, `/grafik`, `/budget`, `/sync`, `/help`.
- On the first run it imports your history quietly and only alerts transactions from the last 24 hours.

It uses long polling, so it runs fine on your laptop with no public URL, but only while the laptop is on. Moving it to a server is phase 6.

Try it without Gmail: `tracker synthetic`, then set `TRACKER_BOT_SOURCE=eml` and use a separate test database (`TRACKER_DB_URL=sqlite:///data/demo.db`).

## Run it in the background on Windows (no terminal)

Starts the bot hidden every time you log in to Windows. It stops while the laptop is off or asleep, and catches up on missed emails when it wakes up.

```powershell
deploy\windows\install-autostart.bat     # turn on (also starts it now)
deploy\windows\stop-bot.bat              # stop it (e.g. to run "tracker bot" yourself)
deploy\windows\start-bot.vbs             # start it again without logging out
deploy\windows\uninstall-autostart.bat   # turn auto-start off
Get-Content data\bot.log -Wait -Tail 20  # watch the log
```

## Run it on a server (Google Cloud free e2-micro)

So the bot keeps running without a terminal open. Short version (details in the chat guide):

1. Push this repo to GitHub, then in Google Cloud > Google Auth Platform > Branding set the homepage to the repo URL and the privacy policy to `PRIVACY.md` on GitHub, and **Publish app** (otherwise the Gmail login expires every 7 days).
2. Create an **e2-micro** VM (free tier regions: `us-west1`, `us-central1` or `us-east1`), Debian, standard persistent disk.
3. In the VM's SSH window:
   ```bash
   git clone https://github.com/<you>/expense-tracker.git
   cd expense-tracker
   bash deploy/setup.sh
   ```
4. Upload `.env`, `credentials.json`, `data/token.json` and `data/tracker.db` (SSH window > Upload file), move them into place, then `sudo systemctl start expense-tracker`.
5. Logs: `journalctl -u expense-tracker -f`. Update: `git pull && sudo systemctl restart expense-tracker`.

Stop the bot on the laptop once the server runs it, or both will send the same alerts.

## Commands

| Command | What it does |
|---|---|
| `tracker sync [--source eml --path DIR] [--since YYYY-MM-DD] [--full]` | Fetch and parse new emails |
| `tracker summary today` / `day YYYY-MM-DD` / `month [YYYY-MM]` | Totals, categories, per-day chart |
| `tracker emails [--status failed]` | See how each email was parsed |
| `tracker reparse [--all]` | Re-run the parser on stored emails after improving it |
| `tracker parties [--guesses] [--people]` | Who you pay, friend (teman) or store (toko), with totals |
| `tracker tag <id or name> --teman / --toko [--category NAME]` | Confirm who someone is; applies to past and future payments |
| `tracker rule add PATTERN CATEGORY` | Your own category rule, applied to past transactions too |
| `tracker bot` | Run the Telegram bot |
| `tracker status` | Counts and latest transaction |

## How it counts money

- **Expense**: QRIS, purchases, bills, transfers to other people, ATM withdrawals (cash counts as spent when withdrawn), and e-wallet top-ups (money counts as spent when it leaves Mandiri, since e-wallets are only filled from here).
- **Income**: money coming in from someone else.
- **Internal (not counted)**: transfers to your own accounts (`TRACKER_OWN_NAMES`). Admin fees on these still count, as "Biaya Admin".
- **Friends vs stores**: a QR payment can go to a friend's personal QR ("Erin Josephine, Edukasi" via GoPay) or to a store. The tracker guesses from the name and the QR provider, then remembers your answer per QR code (by its Merchant PAN, which never changes). Payments to people go to "Transfer ke Orang" (and to the utang flow in phase 3). Check guesses with `tracker parties --guesses`.
- Every raw email is stored, so parser fixes can be applied to history with `tracker reparse`. Categories you set yourself are never overwritten.

## Project layout

```
src/expense_tracker/
  sources/     gmail.py (Gmail API), eml_folder.py (local .eml files)
  parsing/     fields.py (label/value extraction, Rupiah + Indonesian date parsing), mandiri.py
  categorize.py  rules + expense/income/internal detection
  parties.py     friend vs store: guess, stable keys (QR Merchant PAN), tagging
  pipeline.py    sync and reparse
  summary.py     daily/monthly numbers (shared by CLI, bot and API later)
  budget.py      monthly budget split into a daily allowance
  period.py      budget periods (calendar month or e.g. 25th to 24th)
  charts.py      chart image for /grafik (matplotlib)
  bot/           Telegram bot: actions.py (logic, testable), texts.py (messages), app.py (Telegram glue)
  synthetic.py   fake emails for tests and the demo
tests/
```

Run tests with `pytest`. Parser tests use real Livin' email layouts from `tests/fixtures/`, anonymized with `scripts/anonymize_eml.py` (names, accounts and reference numbers replaced, images and links removed).
