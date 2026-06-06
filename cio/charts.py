"""Chart generation. Returns PNG file paths the bot can send as photos."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless, no display
import matplotlib.pyplot as plt

from . import portfolio

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "charts"


def _out(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / name


# Palette sampled from the reference quote-board (light theme).
_C_BG       = "#ffffff"
_C_HEADER   = "#787b86"   # column-header gray
_C_TEXT     = "#131722"   # instrument / neutral numbers (near-black)
_C_UP       = "#22ab94"   # positive change (green)
_C_DOWN     = "#f7525f"   # negative change (red)
_C_FLAT     = "#787b86"   # zero / missing
_C_INDEX_BG = "#fdf3e4"   # highlighted index row (beige)
_C_RULE     = "#e0e3eb"   # row separator


def _fmt_vol(v) -> str:
    """Human-readable volume: 9.90K / 22.5M / 1.05B."""
    if v is None:
        return "—"
    v = float(v)
    for unit, div, dp in (("B", 1e9, 2), ("M", 1e6, 1), ("K", 1e3, 2)):
        if abs(v) >= div:
            return f"{v / div:.{dp}f}{unit}"
    return f"{int(v)}"


def _chg_color(change) -> str:
    if change is None:
        return _C_FLAT
    return _C_UP if change >= 0 else _C_DOWN


def watchlist_table(snapshot: dict, index_symbol: str = "^IXIC") -> str | None:
    """Render a watchlist price snapshot as a quote-board image (Instrument / Last /
    Change / Change % / Volume), styled like a broker watchlist: light theme,
    green/red changes, a colored up/down dot, and the NASDAQ index highlighted on
    top. Returns the PNG path, or None if there's nothing to draw.

    *snapshot* is the dict from cio.watchlist.prices(); each quote should carry
    change / change_pct / volume (added by stock.data.latest_quote)."""
    quotes = list(snapshot.get("quotes") or [])
    missing = list(snapshot.get("missing") or [])
    if not quotes and not missing:
        return None

    # Index row floats to the top (it's the market benchmark), rest keep order.
    idx = [q for q in quotes if q["symbol"] == index_symbol]
    rest = [q for q in quotes if q["symbol"] != index_symbol]
    ordered = idx + rest

    rows = len(ordered) + len(missing)
    fig_h = 0.5 + 0.42 * (rows + 1)               # +1 for the header row
    fig, ax = plt.subplots(figsize=(7.4, fig_h))
    fig.patch.set_facecolor(_C_BG)
    ax.set_facecolor(_C_BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, rows + 1)
    ax.axis("off")

    # column anchors (x). instrument left-aligned; numbers right-aligned.
    X_INST, X_DOT, X_LAST, X_CHG, X_PCT, X_VOL = 0.02, 0.33, 0.52, 0.70, 0.86, 0.995
    top = rows + 1

    def cell(x, y, s, color, ha="right", size=12, weight="normal"):
        ax.text(x, y, s, color=color, ha=ha, va="center", fontsize=size,
                fontweight=weight)

    # header
    yh = top - 0.5
    cell(X_INST, yh, "Instrument", _C_HEADER, ha="left", size=11)
    cell(X_LAST, yh, "Last", _C_HEADER, size=11)
    cell(X_CHG, yh, "Change", _C_HEADER, size=11)
    cell(X_PCT, yh, "Change %", _C_HEADER, size=11)
    cell(X_VOL, yh, "Volume", _C_HEADER, size=11)
    ax.plot([0, 1], [yh - 0.5, yh - 0.5], color=_C_RULE, lw=1)

    def draw_row(r, label, q, dim=False):
        y = top - 0.5 - (r + 1)
        is_index = q is not None and q["symbol"] == index_symbol
        if is_index:                                # beige highlight band
            ax.add_patch(plt.Rectangle((0, y - 0.5), 1, 1, color=_C_INDEX_BG, zorder=0))
        cell(X_INST, y, label, _C_FLAT if dim else _C_TEXT, ha="left", size=13,
             weight="bold")
        if q is None:                               # no-data row
            for x in (X_LAST, X_CHG, X_PCT, X_VOL):
                cell(x, y, "—", _C_FLAT)
        else:
            change, pct = q.get("change"), q.get("change_pct")
            col = _chg_color(change)
            ax.plot([X_DOT], [y], "o", ms=6, color=col)
            cell(X_LAST, y, f"{q['close']:,.2f}", _C_TEXT)
            cell(X_CHG, y, f"{change:+,.2f}" if change is not None else "—", col)
            cell(X_PCT, y, f"{pct:+.2f}%" if pct is not None else "—", col)
            cell(X_VOL, y, _fmt_vol(q.get("volume")), _C_TEXT)
        ax.plot([0, 1], [y - 0.5, y - 0.5], color=_C_RULE, lw=0.8)

    r = 0
    for q in ordered:
        draw_row(r, "COMP" if q["symbol"] == index_symbol else q["symbol"], q)
        r += 1
    for sym in missing:
        draw_row(r, "COMP" if sym == index_symbol else sym, None, dim=True)
        r += 1

    path = _out("watchlist.png")
    fig.savefig(path, bbox_inches="tight", dpi=150, facecolor=_C_BG)
    plt.close(fig)
    return str(path)


_C_HI_BG = "#fdeaec"   # light-red band behind HIGH-impact rows
_IMPACT_COLOR = {"high": _C_DOWN, "medium": "#f5a623", "low": _C_UP}


def econ_events_table(events: list[dict], title: str = "Economic Red-Events") -> str | None:
    """Render upcoming high-impact economic events as a table image (Date / Day /
    Time ET / Event / Impact), styled like the quote-board: impact-colored dot,
    HIGH rows on a light-red band. Returns the PNG path, or None if no events.

    *events* are dicts from econ_calendar.list_upcoming(): event_date, name,
    impact, time_et."""
    from datetime import date as _date
    if not events:
        return None

    rows = len(events)
    fig_h = 0.7 + 0.42 * (rows + 1)               # +1 for the header row
    fig, ax = plt.subplots(figsize=(8.6, fig_h))
    fig.patch.set_facecolor(_C_BG)
    ax.set_facecolor(_C_BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, rows + 1.6)
    ax.axis("off")

    X_DATE, X_DAY, X_TIME, X_EVENT, X_DOT, X_IMP = 0.02, 0.20, 0.30, 0.44, 0.85, 0.995
    top = rows + 1

    def cell(x, y, s, color, ha="left", size=12, weight="normal"):
        ax.text(x, y, s, color=color, ha=ha, va="center", fontsize=size,
                fontweight=weight)

    # title
    cell(X_DATE, top + 0.45, title, _C_TEXT, size=15, weight="bold")

    # header
    yh = top - 0.5
    cell(X_DATE, yh, "Date", _C_HEADER, size=11)
    cell(X_DAY, yh, "Day", _C_HEADER, size=11)
    cell(X_TIME, yh, "Time ET", _C_HEADER, size=11)
    cell(X_EVENT, yh, "Event", _C_HEADER, size=11)
    cell(X_IMP, yh, "Impact", _C_HEADER, ha="right", size=11)
    ax.plot([0, 1], [yh - 0.5, yh - 0.5], color=_C_RULE, lw=1)

    for r, e in enumerate(events):
        y = top - 0.5 - (r + 1)
        impact = (e.get("impact") or "high").lower()
        col = _IMPACT_COLOR.get(impact, _C_DOWN)
        if impact == "high":                       # light-red highlight band
            ax.add_patch(plt.Rectangle((0, y - 0.5), 1, 1, color=_C_HI_BG, zorder=0))
        d = e.get("event_date", "")
        try:
            dow = _date.fromisoformat(d).strftime("%a")
        except ValueError:
            dow = ""
        cell(X_DATE, y, d, _C_TEXT, size=12, weight="bold")
        cell(X_DAY, y, dow, _C_FLAT, size=12)
        cell(X_TIME, y, e.get("time_et") or "—", _C_TEXT, size=12)
        cell(X_EVENT, y, e.get("name") or "", _C_TEXT, size=11)
        ax.plot([X_DOT], [y], "o", ms=7, color=col)
        cell(X_IMP, y, impact.upper(), col, ha="right", size=11, weight="bold")
        ax.plot([0, 1], [y - 0.5, y - 0.5], color=_C_RULE, lw=0.8)

    path = _out("econ_events.png")
    fig.savefig(path, bbox_inches="tight", dpi=150, facecolor=_C_BG)
    plt.close(fig)
    return str(path)


def allocation_pie(db_path=portfolio.db.DB_PATH) -> str | None:
    """Pie chart of market value by symbol. None if no priced positions."""
    pos = portfolio.positions(db_path)
    pos = pos.dropna(subset=["market_value"])
    if pos.empty:
        return None
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(pos["market_value"], labels=pos["symbol"], autopct="%1.1f%%", startangle=90)
    ax.set_title("Portfolio Allocation by Market Value")
    path = _out("allocation.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    return str(path)


def pl_bar(db_path=portfolio.db.DB_PATH) -> str | None:
    """Bar chart of unrealized P&L by symbol. None if nothing priced."""
    pos = portfolio.positions(db_path)
    pos = pos.dropna(subset=["unrealized_pl"])
    if pos.empty:
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in pos["unrealized_pl"]]
    ax.bar(pos["symbol"], pos["unrealized_pl"], color=colors)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Unrealized P&L")
    ax.set_title("Unrealized P&L by Holding")
    path = _out("pl.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    return str(path)
