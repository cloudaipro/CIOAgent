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

from . import memory, recall, scheduler
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


async def _reply(update: Update, text: str, images: list[str]) -> None:
    for chunk in (text[i:i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)) if text else []:
        await update.message.reply_text(chunk)
    for img in images:
        with open(img, "rb") as f:
            await update.message.reply_photo(f)


async def _run(update: Update, prompt: str) -> None:
    chat_id = update.effective_chat.id
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        text, images = await _agent(chat_id).ask(prompt)
    except Exception as e:  # never let the bot die on one bad turn
        log.exception("agent error")
        await update.message.reply_text(f"⚠️ Agent error: {e}")
        return
    await _reply(update, text or "(no response)", images)


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
        "• /subscribe for a daily portfolio digest (/unsubscribe to stop)\n"
        "• /committee SYMBOL — full AI investment-committee report"
    )


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, True)
    await update.message.reply_text("✅ Subscribed. You'll get a daily portfolio digest.")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    memory.set_subscribed(update.effective_chat.id, False)
    await update.message.reply_text("🔕 Unsubscribed from the daily digest.")


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
    try:
        # ── 1. Parse symbol ───────────────────────────────────────────────
        if not ctx.args:
            await update.message.reply_text(
                "Usage: /committee SYMBOL  (e.g. /committee AAPL or /committee 2330.TW)"
            )
            return
        sym = ctx.args[0].upper()

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

        # ── 5. Build + upload report ──────────────────────────────────────
        md = build_report(sym, result)
        date_str = datetime.date.today().isoformat()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = REPORTS_DIR / f"{_safe_name(sym)}_committee_{date_str}.md"
        report_path.write_text(md, encoding="utf-8")
        with open(report_path, "rb") as fh:
            await update.message.reply_document(
                fh, filename=report_path.name
            )

        # ── 6. Short summary message ──────────────────────────────────────
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
            f"📋 *{sym} Committee Summary*\n\n"
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
    app.add_handler(CommandHandler("committee", cmd_committee))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("CIO bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
