"""Telegram front-end for the CIO agent.

Receives text / photos / CSV documents, routes them to a per-chat CIOAgent
(running on your Claude Pro subscription), and replies with text plus any
charts the agent generated. Run:  python -m cio.bot
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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
TG_LIMIT = 4096

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

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📊 CIO agent online.\n\n"
        "• Ask anything: \"how's my portfolio?\", \"top gainer?\", \"show allocation\"\n"
        "• Set a price: \"set AAPL 230\"\n"
        "• Upload a transactions CSV (txn_date,symbol,action,quantity,price,...) to import\n"
        "• Send a broker screenshot or receipt photo and I'll read it\n"
        "• /subscribe for a daily portfolio digest (/unsubscribe to stop)"
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
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("CIO bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
