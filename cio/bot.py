"""Telegram front-end for the CIO agent.

Receives text / photos / CSV documents, routes them to a per-chat CIOAgent
(running on your Claude Pro subscription), and replies with text plus any
charts the agent generated. Run:  python -m cio.bot
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from . import charts, memory, recall, scheduler, watchlist
from .agent import CIOAgent

load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("cio.bot")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"
TG_LIMIT = 4096

# Access control: if CIO_ALLOWED_CHATS is set (comma-separated chat ids), the bot
# ignores every other chat. Unset = open (back-compat) but warned about at startup.
ALLOWED_CHATS = {
    int(x) for x in os.getenv("CIO_ALLOWED_CHATS", "").replace(" ", "").split(",")
    if x and x.lstrip("-").isdigit()
}

# Keep generated filenames inside their directory (no path traversal from a symbol).
_SAFE_NAME = __import__("re").compile(r"[^A-Za-z0-9.\-^=]")


def _safe_name(text: str, fallback: str = "report") -> str:
    s = _SAFE_NAME.sub("", str(text)).lstrip(".")[:24]
    return s or fallback

# One conversational agent per chat, created lazily.
_agents: dict[int, CIOAgent] = {}


def _agent(chat_id: int) -> CIOAgent:
    if chat_id not in _agents:
        memory.touch_chat(chat_id)
        _agents[chat_id] = CIOAgent(
            resume=memory.get_session_id(chat_id),
            on_session_id=lambda sid: memory.set_session_id(chat_id, sid),
            chat_id=chat_id,
        )
    return _agents[chat_id]


# ----- in-flight task tracking (for /stop) ----------------------------------
# The long handlers (on_text/on_photo/on_document/cmd_committee) are registered
# block=False, so the dispatcher keeps reading updates while one runs — that lets
# a /stop arrive and cancel the in-flight task. `_running` maps chat_id → the set
# of its live handler tasks; `_stopping` marks the tasks a user explicitly asked
# to cancel, so a genuine shutdown cancellation (not in the set) still propagates.
_running: dict[int, set[asyncio.Task]] = {}
_stopping: set[asyncio.Task] = set()


def _track_task(chat_id: int, task: asyncio.Task | None) -> None:
    if task is not None:
        _running.setdefault(chat_id, set()).add(task)


def _untrack_task(chat_id: int, task: asyncio.Task | None) -> None:
    if task is not None:
        _stopping.discard(task)   # central cleanup: never leak a stop marker
    s = _running.get(chat_id)
    if s is not None and task is not None:
        s.discard(task)
        if not s:
            _running.pop(chat_id, None)


# Per-chat single-flight: at most one heavy operation (turn or committee run) in
# flight per chat. A second message that arrives while one runs is rejected with
# this notice rather than racing the first on the same agent. /stop is exempt.
_BUSY_MSG = ("⏳ I'm still working on your previous request. Send /stop to cancel "
             "it, then try again.")


def _chat_busy(chat_id: int, exclude: asyncio.Task | None = None) -> bool:
    """True if this chat has a live (non-done) tracked task other than *exclude*."""
    return any(t is not exclude and not t.done()
               for t in _running.get(chat_id, ()))


def _try_acquire(chat_id: int, task: asyncio.Task | None) -> bool:
    """Atomically claim the chat's single in-flight slot for *task*.

    Returns False if another live task already holds it. Synchronous start-to-end
    (no await), so two block=False handlers scheduled back-to-back can't both win.
    """
    if _chat_busy(chat_id, exclude=task):
        return False
    _track_task(chat_id, task)
    return True


async def _reset_agent(chat_id: int) -> None:
    """Drop a chat's agent after a cancelled turn so a half-consumed SDK response
    stream can't corrupt the next turn. The next message lazily rebuilds it,
    resuming from the saved session_id; financial truth lives in the DB regardless.
    Never raises."""
    agent = _agents.pop(chat_id, None)
    if agent is None:
        return
    try:
        await agent.close()
    except Exception:
        log.debug("agent close during reset failed", exc_info=True)


async def _reply(update: Update, text: str, images: list[str]) -> None:
    for chunk in (text[i:i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)) if text else []:
        await update.message.reply_text(chunk)
    for img in images:
        with open(img, "rb") as f:
            await update.message.reply_photo(f)


async def _run(update: Update, prompt: str) -> None:
    chat_id = update.effective_chat.id
    task = asyncio.current_task()
    if not _try_acquire(chat_id, task):
        await update.message.reply_text(_BUSY_MSG)
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    agent = _agent(chat_id)
    try:
        try:
            text, images = await agent.ask(prompt)
        except asyncio.CancelledError:
            # A user /stop cancelled this turn. Distinguish from a genuine
            # shutdown cancellation: only the former is in `_stopping`.
            if task in _stopping:
                _stopping.discard(task)
                log.info("turn stopped by user for chat %s", chat_id)
                # Reset the agent: the in-flight LLM stream was interrupted, so a
                # fresh client avoids any half-consumed state on the next turn. The
                # turn is NOT logged — it never completed.
                await _reset_agent(chat_id)
                try:
                    await update.message.reply_text(
                        "🛑 Stopped. Anything already saved or charged before the "
                        "stop stands — I can only halt the remaining steps."
                    )
                except Exception:
                    pass
                return
            raise  # not a user stop (e.g. shutdown) — let it propagate
        except Exception as e:  # never let the bot die on one bad turn
            log.exception("agent error")
            await update.message.reply_text(f"⚠️ Agent error: {e}")
            return
        # Persist the exchange for the dev dashboard + cold-store recall (best-effort).
        memory.log_turn(chat_id, agent._session_id, prompt, text)
        await _reply(update, text or "(no response)", images)
    finally:
        _untrack_task(chat_id, task)


# ----- handlers -------------------------------------------------------------

async def _gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Access-control gate (runs before all handlers). When an allowlist is set,
    drop updates from any other chat so the bot — which can read the operator's
    portfolio, ingest CSVs, and spend NIM credits via /committee — is not open to
    strangers who discover it."""
    chat = update.effective_chat
    if ALLOWED_CHATS and (chat is None or chat.id not in ALLOWED_CHATS):
        log.warning("blocked unauthorized chat %s", getattr(chat, "id", None))
        raise ApplicationHandlerStop


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    log.info("/start from chat %s", chat_id)
    await update.message.reply_text(
        "📊 CIO agent online.\n\n"
        f"🪪 Your chat id: {chat_id}\n"
        "   (put this in CIO_ALLOWED_CHATS in .env to lock the bot to you)\n\n"
        "• Ask anything: \"how's my portfolio?\", \"top gainer?\", \"show allocation\"\n"
        "• Set a price: \"set AAPL 230\"\n"
        "• Upload a transactions CSV (txn_date,symbol,action,quantity,price,...) to import\n"
        "• Send a broker screenshot or receipt photo and I'll read it\n"
        "• /watchlist — latest prices for your active watchlist\n"
        "• /subscribe for a daily portfolio digest (/unsubscribe to stop)\n"
        "• /committee SYMBOL [zh] — committee report as PDF (add zh for 繁體中文)\n"
        "• /stop — cancel whatever I'm currently working on for you"
    )


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, True)
    await update.message.reply_text("✅ Subscribed. You'll get a daily portfolio digest.")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, False)
    await update.message.reply_text("🔕 Unsubscribed from the daily digest.")


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Latest prices for the active watchlist. Deterministic (no model tokens).

    Manage the lists themselves in the dashboard; this just snapshots prices.
    The price fetch is network-bound and synchronous, so it runs in a thread to
    keep the event loop responsive."""
    chat_id = update.effective_chat.id
    log.info("/watchlist from chat %s", chat_id)
    await update.effective_chat.send_action(ChatAction.TYPING)
    snap = await asyncio.to_thread(watchlist.prices)
    if snap["id"] is None:
        await update.message.reply_text(watchlist.format_prices(snap))
        return
    # Render the quote-board image (broker-style table). Fall back to the plain
    # text layout if rendering fails for any reason — the user still gets prices.
    try:
        path = await asyncio.to_thread(charts.watchlist_table, snap,
                                       watchlist.NASDAQ_INDEX)
    except Exception:
        log.exception("watchlist table render failed")
        path = None
    if path:
        with open(path, "rb") as f:
            await update.message.reply_photo(f, caption=f"📋 {snap['watchlist']}")
    else:
        await update.message.reply_text(watchlist.format_prices(snap))


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the in-flight turn / committee run for THIS chat.

    Only halts work that hasn't run yet — side effects already committed (DB
    writes, model/NIM credits spent) cannot be rolled back. Affects only the
    requesting chat; other chats' running tasks are untouched."""
    chat_id = update.effective_chat.id
    tasks = [t for t in _running.get(chat_id, set()) if not t.done()]
    if not tasks:
        await update.message.reply_text("Nothing is running to stop.")
        return
    for t in tasks:
        _stopping.add(t)
        t.cancel()
    await update.message.reply_text(
        f"🛑 Stopping {len(tasks)} running task(s)… already-saved work stands."
    )


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, update.message.text)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    photo = update.message.photo[-1]  # highest resolution
    tg_file = await photo.get_file()
    dest = UPLOAD_DIR / f"{update.effective_chat.id}_{photo.file_unique_id}.jpg"
    await tg_file.download_to_drive(dest)
    caption = update.message.caption or "Read this image and extract the relevant financial figures."
    await _run(update, f"{caption}\nThe image is saved at: {dest}\nUse the Read tool to view it.")


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{update.effective_chat.id}_{doc.file_name}"
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(dest)
    if str(doc.file_name).lower().endswith(".csv"):
        await _run(update, f"User uploaded a transactions CSV at: {dest}. "
                           f"Import it with ingest_transactions_csv, then confirm the count "
                           f"and give a one-line portfolio summary.")
    else:
        await update.message.reply_text("Saved, but I only import .csv files for now.")


async def cmd_committee(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracked wrapper around the committee run so /stop can cancel it mid-flight."""
    chat_id = update.effective_chat.id
    task = asyncio.current_task()
    if not _try_acquire(chat_id, task):
        await update.message.reply_text(_BUSY_MSG)
        return
    try:
        await _cmd_committee_impl(update, ctx)
    except asyncio.CancelledError:
        if task in _stopping:
            _stopping.discard(task)
            log.info("committee run stopped by user for chat %s", chat_id)
            try:
                await update.message.reply_text("🛑 Committee run stopped.")
            except Exception:
                pass
            return
        raise  # genuine cancellation — propagate
    finally:
        _untrack_task(chat_id, task)


async def _cmd_committee_impl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        # ── 1. Parse symbol + optional language ──────────────────────────
        if not ctx.args:
            await update.message.reply_text(
                "Usage: /committee SYMBOL [zh]  "
                "(e.g. /committee AAPL or /committee 2330.TW zh)"
            )
            return
        sym = ctx.args[0].upper()

        from .committee.translate import normalize_lang
        lang = normalize_lang(ctx.args[1] if len(ctx.args) > 1 else None)
        lang_label = " (繁體中文)" if lang == "tc" else ""

        # ── 2. Acknowledge ────────────────────────────────────────────────
        await update.message.reply_text(
            f"🏛 Convening the investment committee on {sym}…\n"
            "This runs ~10-20 model calls (specialists → debate → CIO), "
            "typically 1-3 min. I'll send the full report when ready."
        )
        await update.effective_chat.send_action(ChatAction.TYPING)

        # ── 3. Run committee ──────────────────────────────────────────────
        from .committee import run_committee, build_report
        try:
            result = await run_committee(sym)
        except Exception as e:
            log.exception("run_committee error for %s", sym)
            await update.message.reply_text(f"⚠️ Committee error: {e}")
            return

        # ── 4. No data ────────────────────────────────────────────────────
        if result.error:
            await update.message.reply_text(
                f"No data for {sym}. Check the symbol (TW codes need .TW/.TWO)."
            )
            return

        # ── 5. Build markdown (+ translate when tc) ───────────────────────
        md = build_report(sym, result)
        date_str = datetime.date.today().isoformat()
        lang_suffix = "_zh" if lang == "tc" else ""

        from .committee.translate import translate_report
        md = await translate_report(md, lang)

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        # ── 6. Render PDF (preferred); .md only on render failure ─────────
        pdf_path = REPORTS_DIR / f"{_safe_name(sym)}_committee_{date_str}{lang_suffix}.pdf"
        report_title = f"Investment Committee Report: {sym}{lang_label}"
        try:
            from .committee.render_pdf import markdown_to_pdf
            markdown_to_pdf(md, pdf_path, title=report_title)
            with open(pdf_path, "rb") as fh:
                await update.message.reply_document(fh, filename=pdf_path.name)
        except Exception:
            log.exception("PDF render failed for %s; falling back to .md", sym)
            md_path = REPORTS_DIR / f"{_safe_name(sym)}_committee_{date_str}{lang_suffix}.md"
            md_path.write_text(md, encoding="utf-8")
            with open(md_path, "rb") as fh:
                await update.message.reply_document(fh, filename=md_path.name)

        # ── 7. Short summary message ──────────────────────────────────────
        from .committee.report import confidence_band

        cio = result.cio or {}
        tally = result.vote_tally or {}
        consensus = result.consensus or {}

        final_rec = cio.get("final_recommendation") or "N/A"
        conf_score = cio.get("confidence_score")
        band = confidence_band(conf_score) if conf_score is not None else "N/A"
        conf_str = f"{conf_score}" if conf_score is not None else "N/A"
        buy_c = tally.get("buy_count", 0)
        hold_c = tally.get("hold_count", 0)
        sell_c = tally.get("sell_count", 0)
        agree = consensus.get("agreement_score") or "N/A"

        summary = (
            f"📋 *{sym} Committee Summary{lang_label}*\n\n"
            f"*Recommendation:* {final_rec}\n"
            f"*Confidence:* {conf_str} — {band}\n"
            f"*Vote Tally:* BUY {buy_c} | HOLD {hold_c} | SELL {sell_c}\n"
            f"*Agreement Score:* {agree}\n\n"
            f"_Full report attached above._"
        )
        await update.message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        log.exception("cmd_committee crashed unexpectedly")
        try:
            await update.message.reply_text(f"⚠️ Committee error: {e}")
        except Exception:
            pass


async def _prewarm_chat(chat_id: int) -> None:
    """Connect + resume one chat's session; never let a bad resume block boot."""
    try:
        await _agent(chat_id).warm()
    except Exception:
        log.exception("pre-warm failed for chat %s", chat_id)


async def _post_init(app: Application) -> None:
    """Start the scheduler and eagerly resume known chat sessions at boot."""
    if memory.get_meta("vec_reindex_needed"):   # embedding dim/model changed
        log.info("re-embedding memory after model change…")
        n, t = recall.reindex_all()
        log.info("reindex complete: %d notes, %d turns", n, t)
    app.bot_data["scheduler"] = scheduler.start(app.bot)
    chats = memory.all_chats()
    if chats:
        log.info("pre-warming %d chat session(s)…", len(chats))
        await asyncio.gather(*(_prewarm_chat(c) for c in chats))
        log.info("pre-warm complete")


async def _post_shutdown(app: Application) -> None:
    sched = app.bot_data.get("scheduler")
    if sched:
        sched.shutdown(wait=False)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather).")
    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    # Access-control gate first (group=-1) so it runs before every handler.
    app.add_handler(TypeHandler(Update, _gate), group=-1)
    if ALLOWED_CHATS:
        log.info("access control: %d allowed chat(s)", len(ALLOWED_CHATS))
    else:
        log.warning("CIO_ALLOWED_CHATS not set — bot responds to ANY chat. "
                    "Set it to your Telegram chat id(s) to lock the bot down.")
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("stop", cmd_stop))
    # Long handlers run block=False so the dispatcher keeps reading updates while
    # one is in flight — that is what lets a /stop arrive and cancel it.
    app.add_handler(CommandHandler("committee", cmd_committee, block=False))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    log.info("CIO bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
