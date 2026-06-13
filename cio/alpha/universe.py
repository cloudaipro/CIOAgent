"""Alpha Hunter candidate universe (PRD §9).

Source order: explicit path arg > CIO_ALPHA_UNIVERSE env > config/alpha_universe.txt
> a small built-in fallback. One ticker per line; '#' comments and blanks ignored.
Tickers are sanitized + de-duplicated (order preserving) via watchlist's rules so a
bad line can never reach a cache filename.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..watchlist import _safe_symbol

_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "alpha_universe.txt"

# Used only if neither the env nor the config file resolves (e.g. stripped deploy).
_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "AMD", "NFLX", "ADBE",
    "COST", "QCOM", "AMAT", "MU", "INTU", "PANW", "CRWD", "MRVL", "ADI", "DDOG",
]


def _parse(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for line in text.splitlines():
        tok = line.split("#", 1)[0].strip().upper()
        if not tok:
            continue
        try:
            seen.setdefault(_safe_symbol(tok), None)
        except ValueError:
            continue
    return list(seen.keys())


def load(path: str | os.PathLike | None = None) -> list[str]:
    """Return the candidate ticker list. Never raises — falls back to the built-in
    list if no file is readable."""
    src = path or os.getenv("CIO_ALPHA_UNIVERSE") or _CONFIG
    try:
        syms = _parse(Path(src).read_text(encoding="utf-8-sig"))
        if syms:
            return syms
    except Exception:
        pass
    return list(_FALLBACK)
