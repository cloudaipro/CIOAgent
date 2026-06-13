"""Plain-text Alpha Hunter report for Telegram. Pure string formatting — no I/O."""
from __future__ import annotations

_LIGHT = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "UNKNOWN": "⚪"}


def format_telegram(result, meta: dict | None = None, max_show: int = 20) -> str:
    reg = result.regime or {}
    status = reg.get("status", "UNKNOWN")
    threshold = (meta or {}).get("threshold")
    lines = [
        f"{_LIGHT.get(status, '⚪')} Alpha Hunter — {result.run_date}",
        f"Market regime: {status}  ({reg.get('detail', '')})",
    ]
    if result.sectors:
        top_sect = ", ".join(f"{s['ticker']}({s['rs']:g})" for s in result.sectors[:3])
        lines.append(f"Top sectors: {top_sect}")
    lines.append("")

    # Selected = candidates at/above the threshold (the published set).
    selected = result.select(threshold) if threshold is not None else result.candidates
    thr_txt = f" (Final ≥ {threshold:g})" if threshold is not None else ""
    if not selected:
        if not result.candidates:
            lines.append("No candidates passed the quality filter this run.")
        else:
            best = result.candidates[0]
            lines.append(f"No candidates met the threshold{thr_txt}. "
                         f"Best was {best['ticker']} at {best['final']:g}.")
    else:
        lines.append(f"{len(selected)} candidate(s) selected{thr_txt}:")
        for c in selected[:max_show]:
            lines.append(
                f"  {c.get('rank', 0):>2}. {c['ticker']:<6} "
                f"final {c['final']:g}  mom {c['momentum']:g}  "
                f"trend {c['trend']:g}  earn {c['earnings']:g}")
        if len(selected) > max_show:
            lines.append(f"  …and {len(selected) - max_show} more.")
    if meta and meta.get("watchlist_name"):
        lines += ["", f"📋 Published watchlist *{meta['watchlist_name']}* (now active). "
                      f"Use /watchlist to see prices, or ask me to add/remove names."]
    return "\n".join(lines)


def format_regime(reg: dict) -> str:
    """One-line-ish market-regime summary for the market_regime tool / chat."""
    status = reg.get("status", "UNKNOWN")
    out = [f"{_LIGHT.get(status, '⚪')} Market regime: {status}",
           reg.get("detail", "")]
    if reg.get("qqq") is not None:
        out.append(f"QQQ {reg.get('qqq')} · 50MA {reg.get('ma50')} · 200MA {reg.get('ma200')}"
                   + (" · 50MA rising" if reg.get("slope_up") else ""))
    return "\n".join(x for x in out if x)
