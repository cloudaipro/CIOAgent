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
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from . import charts, memory, recall, scheduler, watchlist
from .agent import CIOAgent

load_dotenv()
from .logsetup import configure_logging
configure_logging()   # console + optional date-based file (CIO_LOG_TO_FILE / Configure tab)
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
_SAFE_NAME = re.compile(r"[^A-Za-z0-9.\-^=]")


def _safe_name(text: str, fallback: str = "report") -> str:
    s = _SAFE_NAME.sub("", str(text)).lstrip(".")[:24]
    return s or fallback


# Commands surfaced in Telegram's "/" autocomplete and the ☰ Menu button.
# Registered once at boot via set_my_commands (see _post_init). The (command,
# description) pairs are what the user sees when they type "/".
BOT_COMMANDS = [
    ("watchlist", "Latest watchlist prices"),
    ("playbooks", "List saved playbooks you can ask me to run"),
    ("committee", "Investment committee on a symbol — /committee AAPL [zh]"),
    ("briefing", "Pre-market watchlist briefing — /briefing [SYMBOL…] [zh]"),
    ("subscribe", "Daily digest + 06:00 briefing"),
    ("unsubscribe", "Stop the daily digest"),
    ("stop", "Cancel whatever I'm running for you"),
    ("help", "Show what I can do"),
]

# Persistent tap-button grid shown under the text box. Button text must be the
# literal "/command" — Telegram only parses a tap as a command if it starts with
# "/", so emoji labels can't go here (they'd be sent as plain text to the agent).
_REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [["/watchlist", "/briefing"],
     ["/committee", "/playbooks"],
     ["/subscribe", "/unsubscribe"],
     ["/help"]],
    resize_keyboard=True,
    input_field_placeholder="Tap a command or just ask…",
)

# Inline buttons attached under the /start message. These carry callback_data
# (free of the "/" constraint, so they get friendly emoji labels) handled by
# on_callback. Only light, no-argument actions are wired here. /committee and
# /briefing take arguments and run long, tracked jobs, so they stay on the reply
# keyboard / "/" autocomplete where the normal command path handles args + /stop.
_INLINE_MENU = InlineKeyboardMarkup(
    [[InlineKeyboardButton("📋 Watchlist", callback_data="cb:watchlist"),
      InlineKeyboardButton("📒 Playbooks", callback_data="cb:playbooks")],
     [InlineKeyboardButton("🔔 Subscribe", callback_data="cb:subscribe"),
      InlineKeyboardButton("🔕 Unsubscribe", callback_data="cb:unsubscribe")],
     [InlineKeyboardButton("❓ Help", callback_data="cb:help")]],
)

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


def _chunk(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries so a URL (e.g. in
    the Sources footer) is never cut across two messages and rendered unclickable.
    A single line longer than limit is hard-split as a last resort."""
    out: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:                      # pathological single long line
            if buf:
                out.append(buf); buf = ""
            out.append(line[:limit]); line = line[limit:]
        piece = line if not buf else buf + "\n" + line
        if len(piece) > limit:
            out.append(buf); buf = line
        else:
            buf = piece
    if buf:
        out.append(buf)
    return out


async def _reply(update: Update, text: str, images: list[str],
                 docs: list[str] | None = None) -> None:
    for chunk in (_chunk(text) if text else []):
        await update.effective_message.reply_text(chunk)
    for img in images:
        with open(img, "rb") as f:
            await update.effective_message.reply_photo(f)
    for doc in docs or []:
        with open(doc, "rb") as f:
            await update.effective_message.reply_document(f, filename=Path(doc).name)


async def _run(update: Update, prompt: str) -> None:
    chat_id = update.effective_chat.id
    task = asyncio.current_task()
    if not _try_acquire(chat_id, task):
        await update.effective_message.reply_text(_BUSY_MSG)
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    agent = _agent(chat_id)
    try:
        try:
            text, images, docs = await agent.ask(prompt)
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
                    await update.effective_message.reply_text(
                        "🛑 Stopped. Anything already saved or charged before the "
                        "stop stands — I can only halt the remaining steps."
                    )
                except Exception:
                    pass
                return
            raise  # not a user stop (e.g. shutdown) — let it propagate
        except Exception as e:  # never let the bot die on one bad turn
            log.exception("agent error")
            await update.effective_message.reply_text(f"⚠️ Agent error: {e}")
            return
        # Persist the exchange for the dev dashboard + cold-store recall (best-effort).
        memory.log_turn(chat_id, agent._session_id, prompt, text)
        await _reply(update, text or "(no response)", images, docs)
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


def _help_text(chat_id: int) -> str:
    return (
        "📊 CIO agent online.\n\n"
        f"🪪 Your chat id: {chat_id}\n"
        "   (put this in CIO_ALLOWED_CHATS in .env to lock the bot to you)\n\n"
        "• Ask anything: \"how's my portfolio?\", \"top gainer?\", \"show allocation\"\n"
        "• Set a price: \"set AAPL 230\"\n"
        "• Upload a transactions CSV (txn_date,symbol,action,quantity,price,...) to import\n"
        "• Send a broker screenshot or receipt photo and I'll read it\n"
        "• /watchlist — latest prices for your active watchlist\n"
        "• /playbooks — list saved playbooks, then ask me to run one "
        "(e.g. \"Run the monthly_red_events playbook\")\n"
        "• /subscribe — opt in to the daily portfolio digest AND the 06:00 "
        "pre-market watchlist briefing on trading days (/unsubscribe to stop)\n"
        "• /committee SYMBOL [zh] — committee report as PDF (add zh for 繁體中文)\n"
        "• /briefing [SYMBOL…] [zh] — pre-market watchlist briefing as PDF "
        "(add zh for 繁體中文; auto-runs 06:00 on trading days)\n"
        "• /stop — cancel whatever I'm currently working on for you\n\n"
        "💡 Type \"/\" for the command list, tap the ☰ menu, or use the buttons below."
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    log.info("/start from chat %s", chat_id)
    # Inline buttons ride on the help text; a single message can carry only one
    # reply_markup, so a short follow-up installs the persistent reply keyboard.
    await update.effective_message.reply_text(
        _help_text(chat_id), reply_markup=_INLINE_MENU
    )
    await update.effective_message.reply_text(
        "Tap-command keyboard ready. ⌨️", reply_markup=_REPLY_KEYBOARD
    )


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, True)
    await update.effective_message.reply_text(
        "✅ Subscribed. You'll get the daily portfolio digest and the 06:00 "
        "pre-market watchlist briefing on trading days."
    )


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, False)
    await update.effective_message.reply_text("🔕 Unsubscribed from the daily digest.")


async def cmd_playbooks(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List this chat's saved playbooks (named, reusable procedures) so the user
    can discover what to run. Deterministic — no model tokens. Invoke one by
    asking in plain language, e.g. \"Run the monthly_red_events playbook\"."""
    chat_id = update.effective_chat.id
    log.info("/playbooks from chat %s", chat_id)
    pbs = memory.list_playbooks(scope=f"chat:{chat_id}")
    if not pbs:
        await update.effective_message.reply_text(
            "No playbooks saved yet. I build them as I learn recurring tasks, "
            "or you can ask me to \"save a playbook\". Manage them in the dashboard."
        )
        return
    lines = ["📒 Your playbooks — tap a button to run, or just ask:\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for pb in pbs[:12]:  # cap the button grid; all are still listed in text
        name = pb["name"]
        steps = " ".join(str(pb.get("steps") or "").split())
        if len(steps) > 140:
            steps = steps[:139] + "…"
        used = f"  ·  used {pb['hits']}×" if pb.get("hits") else ""
        lines.append(f"▶️ *{name}*{used}\n   {steps}")
        # callback_data is capped at 64 bytes by Telegram; skip the button for an
        # over-long name (it stays listed in the text, runnable by asking).
        if len(f"pb:{name}".encode()) <= 64:
            buttons.append([InlineKeyboardButton(f"▶️ {name}", callback_data=f"pb:{name}")])
    await update.effective_message.reply_text(
        "\n\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch taps on the inline buttons. The cb:* menu actions and pb:* run
    actions reuse the command/run paths, which read update.effective_message and
    so work for both message and callback updates."""
    q = update.callback_query
    await q.answer()  # ack so Telegram stops the button's loading spinner
    data = q.data or ""
    if data.startswith("pb:"):
        # Tap-to-run a saved playbook. Routes through _run so it gets the same
        # busy-guard, /stop tracking, and reply chunking as a typed message.
        name = data[3:]
        log.info("run playbook %r from chat %s", name, update.effective_chat.id)
        await _run(update, f"Run the {name} playbook.")
        return
    action = data.removeprefix("cb:")
    if action == "watchlist":
        await cmd_watchlist(update, ctx)
    elif action == "subscribe":
        await cmd_subscribe(update, ctx)
    elif action == "unsubscribe":
        await cmd_unsubscribe(update, ctx)
    elif action == "playbooks":
        await cmd_playbooks(update, ctx)
    elif action == "help":
        await update.effective_message.reply_text(_help_text(update.effective_chat.id))
    else:
        log.warning("unknown callback data: %r", q.data)


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
        await update.effective_message.reply_text(watchlist.format_prices(snap))
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
            await update.effective_message.reply_photo(f, caption=f"📋 {snap['watchlist']}")
    else:
        await update.effective_message.reply_text(watchlist.format_prices(snap))


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
    # The sender controls file_name — strip any path components / odd characters so
    # the write can never land outside UPLOAD_DIR (or fail on an embedded slash).
    raw_name = Path(str(doc.file_name or "upload")).name
    safe = re.sub(r"[^\w.\-]", "_", raw_name).lstrip(".")[:64] or "upload"
    dest = UPLOAD_DIR / f"{update.effective_chat.id}_{safe}"
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

        # ── 2. Acknowledge ────────────────────────────────────────────────
        await update.message.reply_text(
            f"🏛 Convening the investment committee on {sym}…\n"
            "This runs ~10-20 model calls (specialists → debate → CIO), "
            "typically 1-3 min. I'll send the full report when ready."
        )
        await update.effective_chat.send_action(ChatAction.TYPING)

        # ── 3. Run + render via the shared pipeline ───────────────────────
        from .committee.delivery import produce_report
        art = await produce_report(sym, lang, REPORTS_DIR)
        if art.error:
            await update.message.reply_text(art.error)
            return

        # ── 4. Send report document + short summary ───────────────────────
        with open(art.doc_path, "rb") as fh:
            await update.message.reply_document(fh, filename=art.doc_path.name)
        await update.message.reply_text(art.summary, parse_mode="Markdown")

    except Exception as e:
        log.exception("cmd_committee crashed unexpectedly")
        try:
            await update.message.reply_text(f"⚠️ Committee error: {e}")
        except Exception:
            pass


async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracked wrapper around a manual watchlist briefing so /stop can cancel it."""
    chat_id = update.effective_chat.id
    task = asyncio.current_task()
    if not _try_acquire(chat_id, task):
        await update.message.reply_text(_BUSY_MSG)
        return
    try:
        await _cmd_briefing_impl(update, ctx)
    except asyncio.CancelledError:
        if task in _stopping:
            _stopping.discard(task)
            log.info("watchlist briefing stopped by user for chat %s", chat_id)
            try:
                await update.message.reply_text("🛑 Briefing stopped.")
            except Exception:
                pass
            return
        raise  # genuine cancellation — propagate
    finally:
        _untrack_task(chat_id, task)


async def _cmd_briefing_impl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the Watchlist Monitoring Agent on demand and reply with the briefing.

    No args → the active watchlist. Args → those symbols only. Add ``zh`` for a
    Traditional-Chinese briefing (e.g. /briefing NVDA MU zh, or /briefing zh).
    """
    try:
        from .watchlist_monitor import (
            monitor_watchlist, global_macro_snapshot, build_briefing,
            briefing_summary, as_of_now,
        )
        from .committee.translate import normalize_lang, translate_report

        # Split a language token (zh) out of the symbol args.
        lang = "en"
        symbol_args: list[str] = []
        for arg in (ctx.args or []):
            if normalize_lang(arg) == "tc":
                lang = "tc"
            else:
                symbol_args.append(arg.upper())
        symbols = symbol_args or None
        lang_label = " (繁體中文)" if lang == "tc" else ""
        lang_suffix = "_zh" if lang == "tc" else ""

        scope = ", ".join(symbols) if symbols else "your active watchlist"
        await update.message.reply_text(
            f"📋 Scanning {scope} for the pre-market briefing{lang_label}…\n"
            "One model call per security — typically under a minute. "
            "I'll send the full briefing when ready."
        )
        await update.effective_chat.send_action(ChatAction.TYPING)

        try:
            assessments = await monitor_watchlist(symbols)
        except Exception as e:
            log.exception("monitor_watchlist error")
            await update.message.reply_text(f"⚠️ Briefing error: {e}")
            return

        if not assessments:
            await update.message.reply_text(
                "No active watchlist to review. Create one in the dashboard, "
                "or pass symbols: /briefing NVDA MU."
            )
            return

        try:
            macro = await global_macro_snapshot()
        except Exception:
            log.exception("global_macro_snapshot error; briefing without macro")
            macro = None
        briefing = build_briefing(assessments, as_of=as_of_now(), macro=macro)
        briefing = await translate_report(briefing, lang)
        summary = briefing_summary(assessments, macro)
        date_str = datetime.date.today().isoformat()

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path = REPORTS_DIR / f"watchlist_briefing_{date_str}{lang_suffix}.pdf"
        try:
            from .committee.render_pdf import markdown_to_pdf
            markdown_to_pdf(briefing, pdf_path,
                            title=f"Watchlist Briefing {date_str}{lang_label}")
            with open(pdf_path, "rb") as fh:
                await update.message.reply_document(fh, filename=pdf_path.name)
        except Exception:
            log.exception("briefing PDF render failed; falling back to .md")
            md_path = REPORTS_DIR / f"watchlist_briefing_{date_str}{lang_suffix}.md"
            md_path.write_text(briefing, encoding="utf-8")
            with open(md_path, "rb") as fh:
                await update.message.reply_document(fh, filename=md_path.name)

        await update.message.reply_text(summary)

    except Exception as e:
        log.exception("cmd_briefing crashed unexpectedly")
        try:
            await update.message.reply_text(f"⚠️ Briefing error: {e}")
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
    # Register the slash-command list so Telegram shows it in the "/" autocomplete
    # and the ☰ menu button. Idempotent — safe to call on every boot.
    await app.bot.set_my_commands(
        [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
    )
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


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    # Transient long-poll network drops: PTB's retry loop recovers on its own,
    # so log a one-liner instead of a full traceback.
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning("telegram network error (will retry): %s", err)
        return
    log.error("unhandled telegram error", exc_info=err)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather).")
    # Stamp the code version this process runs so a stale process (running code
    # older than the repo) is detectable — see cio/version.py for the incident.
    from . import version as _version
    boot = _version.stamp_boot()
    log.info("CIO bot booting: version %s (pid %s)", boot["version"], boot["pid"])
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
    app.add_handler(CommandHandler("playbooks", cmd_playbooks))
    app.add_handler(CommandHandler("stop", cmd_stop))
    # Long handlers run block=False so the dispatcher keeps reading updates while
    # one is in flight — that is what lets a /stop arrive and cancel it.
    app.add_handler(CommandHandler("committee", cmd_committee, block=False))
    app.add_handler(CommandHandler("briefing", cmd_briefing, block=False))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(cb|pb):", block=False))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    app.add_error_handler(_on_error)
    log.info("CIO bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
