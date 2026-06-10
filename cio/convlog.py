"""Detailed conversation history — full-fidelity LLM call logs as day-based text files.

Opt-in (off by default). When enabled (``CIO_DETAILED_LOG`` truthy), every LLM call —
main agent turns and each committee agent call — is appended verbatim to a per-day text
file so the operator can audit exactly what was sent and returned:

    logs/<yyyy>/<mm>/<yyyy-mm-dd>.txt        (base dir overridable via CIO_LOG_DIR)

Each entry records: timestamp, kind (chat|committee|internal), scope, role, provider
(LLM service), model, token usage, and the full system prompt / user prompt / response.

Everything here is best-effort and never raises into a chat/committee turn. The dashboard
"Detailed history" tab reads these files (list/select/delete by day), mirroring Telegram.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from . import timeutil

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")          # strict; blocks path traversal
_ENTRY_SEP = "=" * 80                                  # one per logged call


def enabled() -> bool:
    """Feature flag — OFF by default. The ``CIO_DETAILED_LOG`` env var wins (and locks
    the dashboard toggle) when set; otherwise the persisted dashboard setting decides
    (toggled from the Configure tab, shared across the bot + dashboard processes)."""
    env = os.getenv("CIO_DETAILED_LOG")
    if env is not None:
        return env.strip().lower() in ("1", "true", "on", "yes")
    try:
        from .dashboard import settings
        return settings.get_detailed_log()
    except Exception:
        return False


def locked_by_env() -> bool:
    """True when CIO_DETAILED_LOG is set, so the dashboard toggle is read-only."""
    return os.getenv("CIO_DETAILED_LOG") is not None


def _base_dir() -> Path:
    # Resolved per call so CIO_LOG_DIR can be set after import (tests).
    return Path(os.getenv("CIO_LOG_DIR") or (_REPO_ROOT / "logs"))


def _day_path(day: str) -> Path:
    """logs/<yyyy>/<mm>/<yyyy-mm-dd>.txt for a YYYY-MM-DD day string."""
    return _base_dir() / day[:4] / day[5:7] / f"{day}.txt"


def _section(title: str, text: str) -> str:
    bar = f" {title} ".center(80, "-")
    return f"{bar}\n{(text or '').rstrip()}\n"


def log_call(provider: str | None, model: str | None, system_prompt: str,
             user_prompt: str, response: str, tokens: int, *,
             scope: str | None = None, role: str | None = None,
             kind: str = "chat", when: datetime | None = None) -> None:
    """Append one LLM call to today's detailed-history file. No-op when disabled.
    Best-effort: never raises."""
    if not enabled():
        return
    try:
        now = when or datetime.now(timeutil.local_tz())
        day = now.strftime("%Y-%m-%d")
        path = _day_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] kind={kind} "
                  f"scope={scope or '-'} role={role or '-'} "
                  f"provider={provider or '-'} model={model or '-'} tokens={tokens}")
        block = (f"{_ENTRY_SEP}\n{header}\n"
                 f"{_section('SYSTEM PROMPT', system_prompt)}"
                 f"{_section('USER PROMPT', user_prompt)}"
                 f"{_section('RESPONSE', response)}\n")
        with open(path, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        log.debug("convlog write failed", exc_info=True)


# ----- dashboard read API ---------------------------------------------------

def list_days() -> list[dict]:
    """All logged days, newest first: [{day, entries, bytes}]. Empty if disabled-but-
    -no-files or the dir is missing. Never raises."""
    out: list[dict] = []
    try:
        base = _base_dir()
        if not base.is_dir():
            return []
        for p in base.rglob("*.txt"):
            day = p.stem
            if not _DAY_RE.match(day):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                entries = text.count(_ENTRY_SEP + "\n")
                out.append({"day": day, "entries": entries, "bytes": p.stat().st_size})
            except Exception:
                continue
    except Exception:
        log.debug("convlog list_days failed", exc_info=True)
    out.sort(key=lambda d: d["day"], reverse=True)
    return out


def read_day(day: str) -> str | None:
    """Full text of one day's log, or None if the day is invalid/missing."""
    if not _DAY_RE.match(day or ""):
        return None
    p = _day_path(day)
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        log.debug("convlog read_day failed", exc_info=True)
        return None


def delete_day(day: str) -> bool:
    """Delete one day's log file. Returns True if a file was removed. Validates the
    day string (no traversal). Never raises."""
    if not _DAY_RE.match(day or ""):
        return False
    p = _day_path(day)
    try:
        if p.is_file():
            p.unlink()
            # Tidy now-empty month/year dirs (best-effort).
            for d in (p.parent, p.parent.parent):
                try:
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                except Exception:
                    break
            return True
    except Exception:
        log.debug("convlog delete_day failed", exc_info=True)
    return False
