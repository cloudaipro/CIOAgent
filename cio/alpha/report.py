"""Plain-text Alpha Hunter report for Telegram. Pure string formatting — no I/O."""
from __future__ import annotations

_LIGHT = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "UNKNOWN": "⚪"}


def format_telegram(result, meta: dict | None = None, top_n: int = 10) -> str:
    reg = result.regime or {}
    status = reg.get("status", "UNKNOWN")
    lines = [
        f"{_LIGHT.get(status, '⚪')} Alpha Hunter — {result.run_date}",
        f"Market regime: {status}  ({reg.get('detail', '')})",
    ]
    if result.sectors:
        top_sect = ", ".join(f"{s['ticker']}({s['rs']:g})" for s in result.sectors[:3])
        lines.append(f"Top sectors: {top_sect}")
    lines.append("")
    if not result.candidates:
        lines.append("No candidates passed the quality filter this run.")
    else:
        lines.append(f"Top {min(top_n, len(result.candidates))} candidates "
                     f"(of {len(result.candidates)} passed):")
        for c in result.top(top_n):
            lines.append(
                f"  {c.get('rank', 0):>2}. {c['ticker']:<6} "
                f"final {c['final']:g}  mom {c['momentum']:g}  "
                f"trend {c['trend']:g}  earn {c['earnings']:g}")
    if meta and meta.get("watchlist_name"):
        lines += ["", f"📋 Published watchlist *{meta['watchlist_name']}* (now active). "
                      f"Use /watchlist to see prices, or ask me to add/remove names."]
    return "\n".join(lines)
