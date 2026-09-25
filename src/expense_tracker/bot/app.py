"""Telegram layer: wires handlers and scheduled jobs to the logic in actions.py.

Run with `tracker bot`. Uses long polling, so it works from a laptop with no public URL.
"""

import asyncio
import logging
from datetime import datetime, time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from .. import pipeline
from ..config import Settings
from ..db import init_db, make_engine, session_scope
from . import actions, texts
from .actions import Reply

log = logging.getLogger(__name__)


def make_source(settings: Settings):
    if settings.bot_source == "eml":
        from ..sources.eml_folder import EmlFolderSource

        return EmlFolderSource(settings.bot_eml_path, settings.tz)
    from ..sources.gmail import GmailSource

    return GmailSource(settings.gmail_credentials, settings.gmail_token, settings.gmail_query, settings.tz)


def _markup(reply: Reply) -> InlineKeyboardMarkup | None:
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                 for row in reply.buttons])


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.tz).replace(tzinfo=None)


async def _send(context: ContextTypes.DEFAULT_TYPE, reply: Reply) -> None:
    settings: Settings = context.bot_data["settings"]
    await context.bot.send_message(settings.telegram_chat_id, reply.text, parse_mode=ParseMode.HTML,
                                   reply_markup=_markup(reply))


# ------------------------------------------------------------------ sync + alerts

def _sync_blocking(bot_data: dict) -> pipeline.SyncStats:
    with session_scope(bot_data["engine"]) as session:
        return pipeline.sync(session, bot_data["source"], bot_data["settings"])


async def sync_and_alert(context: ContextTypes.DEFAULT_TYPE) -> pipeline.SyncStats | None:
    data, settings = context.bot_data, context.bot_data["settings"]
    async with data["lock"]:
        try:
            stats = await asyncio.to_thread(_sync_blocking, data)
            if data.get("sync_failing"):
                data["sync_failing"] = False
                await _send(context, Reply("Cek email jalan lagi."))
        except Exception as exc:  # Gmail down, token expired, no internet...
            log.exception("sync failed")
            if not data.get("sync_failing"):
                data["sync_failing"] = True
                await _send(context, Reply(f"Gagal cek email: <code>{texts.escape(str(exc))[:300]}</code>\n"
                                           "Nanti dicoba lagi otomatis."))
            return None

        now = _now(settings)
        with session_scope(data["engine"]) as session:
            pending = actions.pending_alerts(session, settings, now)
        for txn_id, reply in pending:
            await _send(context, reply)
            with session_scope(data["engine"]) as session:
                actions.mark_notified(session, txn_id, now)
        with session_scope(data["engine"]) as session:
            warnings = actions.budget_warnings(session, settings, now.date())
        for reply in warnings:
            await _send(context, reply)
        return stats


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await sync_and_alert(context)


async def summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.bot_data["settings"]
    await sync_and_alert(context)  # include anything from the last few minutes
    with session_scope(context.bot_data["engine"]) as session:
        reply = actions.today_report(session, settings, _now(settings).date(), title="Ringkasan")
    await _send(context, reply)


# ------------------------------------------------------------------ commands

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    chat_id = update.effective_chat.id
    if chat_id != settings.telegram_chat_id:
        await update.message.reply_text(
            f"Chat ID kamu: <code>{chat_id}</code>\nIsi TRACKER_TELEGRAM_CHAT_ID={chat_id} di .env, lalu restart bot.",
            parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text("Bot aktif. Transaksi baru akan dikirim ke sini.\n\n" + texts.HELP,
                                    parse_mode=ParseMode.HTML)


def _report_command(builder):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = context.bot_data["settings"]
        with session_scope(context.bot_data["engine"]) as session:
            reply = builder(session, settings, _now(settings).date())
        await _reply(update.message, reply)
    return handler


async def _reply(message, reply: Reply) -> None:
    if reply.photo is not None:
        await message.reply_photo(reply.photo, caption=reply.text, parse_mode=ParseMode.HTML,
                                  reply_markup=_markup(reply))
    else:
        await message.reply_text(reply.text, parse_mode=ParseMode.HTML, reply_markup=_markup(reply))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texts.HELP, parse_mode=ParseMode.HTML)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Cek email...")
    stats = await sync_and_alert(context)
    text = "Gagal cek email, lihat pesan di atas." if stats is None else \
        f"Selesai: {stats.new} email baru, {stats.parsed} transaksi."
    await msg.edit_text(text)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings: Settings = context.bot_data["settings"]
    if query.message is None or query.message.chat.id != settings.telegram_chat_id:
        await query.answer()
        return
    async with context.bot_data["lock"]:
        with session_scope(context.bot_data["engine"]) as session:
            reply = actions.handle_callback(session, settings, query.data or "", _now(settings).date())
    await query.answer()
    if reply is None:
        return
    if reply.photo is not None:  # a chart can't replace a text message, so send it as a new one
        await _reply(query.message, reply)
        return
    try:
        await query.edit_message_text(reply.text, parse_mode=ParseMode.HTML, reply_markup=_markup(reply))
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


# ------------------------------------------------------------------ app

async def set_commands(app: Application) -> None:
    """Register the command list, so typing "/" in Telegram shows a menu."""
    try:
        await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in texts.COMMANDS])
    except Exception:  # not worth crashing the bot over
        log.exception("could not set the command menu")


def build_app(settings: Settings) -> Application:
    if not settings.telegram_token:
        raise SystemExit("TRACKER_TELEGRAM_TOKEN is empty. See README, 'Telegram bot setup'.")

    engine = make_engine(settings.db_url)
    init_db(engine)
    app = ApplicationBuilder().token(settings.telegram_token).post_init(set_commands).build()
    app.bot_data.update(engine=engine, settings=settings, source=make_source(settings), lock=asyncio.Lock())

    app.add_handler(CommandHandler("start", start))
    if settings.telegram_chat_id:
        owner = filters.Chat(chat_id=settings.telegram_chat_id)
        app.add_handler(CommandHandler("hariini", _report_command(actions.today_report), filters=owner))
        app.add_handler(CommandHandler("bulanini", _report_command(actions.month_report), filters=owner))
        app.add_handler(CommandHandler("minggu", _report_command(actions.week_report), filters=owner))
        app.add_handler(CommandHandler("grafik", _report_command(actions.chart_report), filters=owner))
        app.add_handler(CommandHandler("budget", _report_command(actions.budget_report), filters=owner))
        app.add_handler(CommandHandler("sync", sync_command, filters=owner))
        app.add_handler(CommandHandler("help", help_command, filters=owner))
        app.add_handler(CallbackQueryHandler(on_button))

        hour, minute = (int(x) for x in settings.summary_time.split(":"))
        app.job_queue.run_repeating(sync_job, interval=settings.sync_every_minutes * 60, first=5)
        app.job_queue.run_daily(summary_job, time=time(hour, minute, tzinfo=settings.tz))
    else:
        log.warning("TRACKER_TELEGRAM_CHAT_ID not set: only /start works until you set it.")
    return app


def run(settings: Settings) -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log the token-bearing URLs
    build_app(settings).run_polling(allowed_updates=Update.ALL_TYPES)
