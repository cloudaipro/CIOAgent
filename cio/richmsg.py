"""Telegram Rich Messages (Bot API 10.1) sender.

python-telegram-bot 22.x has no binding for ``sendRichMessage`` yet, so we
call the HTTP Bot API directly with the bot's token. Rich Markdown renders a
full document — headings, tables, lists, block quotations, collapsible
details, spoilers, strikethrough, and LaTeX formulas — far beyond the legacy
``parse_mode="Markdown"`` subset (which only handled bold/italic/code/links
and broke on bare ``_ * [`` in tickers and numbers).

Every send is best-effort: on the feature being disabled, the text being too
large for one rich message, a missing token, or any API/transport error, the
helper returns ``False`` so the caller can fall back to its existing
``reply_text`` / ``send_message`` path. It never raises.

Kill switch: ``CIO_RICH_MESSAGES=0`` forces every call down the legacy path.

Docs: https://core.telegram.org/bots/api#sendrichmessage
      https://core.telegram.org/bots/api#rich-message-formatting-options
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendRichMessage"
# Hard cap from the Bot API: up to 32768 UTF-8 chars in the rich message text.
# Over this, a single sendRichMessage is rejected, so we defer to the caller's
# legacy chunker instead.
_MAX_CHARS = 32768
_FALSEY = {"0", "false", "no", "off", ""}


def enabled() -> bool:
    """True unless CIO_RICH_MESSAGES is explicitly set to a falsey value."""
    return os.environ.get("CIO_RICH_MESSAGES", "1").strip().lower() not in _FALSEY


async def send_rich_text(bot, chat_id, markdown: str, *, timeout: float = 20.0) -> bool:
    """Send one Rich Markdown message via the HTTP Bot API.

    Returns True on a confirmed ``ok`` response; False to signal the caller to
    fall back to its legacy send. Never raises.

    ``bot`` is any object exposing a ``.token`` attribute (a PTB ``Bot`` does).
    """
    if not enabled() or not markdown:
        return False
    if len(markdown) > _MAX_CHARS:
        return False  # too big for one rich message; let the legacy chunker run
    token = getattr(bot, "token", None)
    if not token:
        return False

    payload = {"chat_id": chat_id, "rich_message": {"markdown": markdown}}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_API.format(token=token), json=payload)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return True
        log.warning(
            "sendRichMessage rejected (HTTP %s): %s",
            resp.status_code, data.get("description"),
        )
        return False
    except Exception:  # transport error, bad JSON, etc. — fall back quietly
        log.exception("sendRichMessage failed; falling back to legacy send")
        return False


async def reply_rich(message, markdown: str, **kw) -> bool:
    """Rich-send to the chat of a PTB ``Message`` (e.g. update.effective_message).

    Returns True on success, False to signal a legacy fallback.
    """
    if message is None:
        return False
    try:
        bot = message.get_bot()
        chat_id = message.chat_id
    except Exception:
        return False
    return await send_rich_text(bot, chat_id, markdown, **kw)
