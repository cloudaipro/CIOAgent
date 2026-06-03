"""
Single-stock panel renderer.

Produces a portrait PNG (dpi 130) with the sections yfinance can fill:
  Hero      — ticker · name · price · change pill · date  (LIVE)
  價格       — candlestick + MA20/60/120                    (LIVE)
  基本面     — 9-metric card grid                           (LIVE)
  營收（季） — quarterly revenue bars + YoY                 (LIVE when ≥2 quarters)
  Footer    — related links · CIO wordmark                  (LIVE)

Sections with no yfinance source for TW equities (法人動向 / 融資融券 / 持股分布)
are intentionally omitted rather than shown as empty placeholders.

Design system
  ink #1f2937 · muted #64748b · hairline #e6e8ec · card #f5f6f8 · accent #334155
Color convention: green (#16a34a) = up/positive, red (#d92b2b) = down/negative
(matches cio/charts.py and the rest of the reports).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # must be before pyplot import
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
import matplotlib.gridspec as gridspec
import numpy as np

# CJK font — must come after backend selection, before any draw call.
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ---- design tokens -----------------------------------------------------------
_INK = "#1f2937"      # primary text
_MUTED = "#64748b"    # secondary text / labels
_FAINT = "#94a3b8"    # tertiary (dates, axis ticks)
_HAIR = "#e6e8ec"     # hairlines / dividers
_CARD = "#f5f6f8"     # metric-tile fill
_ACCENT = "#334155"   # section accent bar / wordmark
_BG = "#ffffff"

_UP = "#16a34a"       # up / positive (green)
_DOWN = "#d92b2b"     # down / negative (red)
_MA_COLORS = {"MA20": "#2563eb", "MA60": "#f59e0b", "MA120": "#94a3b8"}

_NA_MSG = "—　無資料來源（yfinance）"   # em-space for visual indent


# ---- output directory --------------------------------------------------------
def _charts_dir() -> Path:
    """Mirror cio/charts.py OUT_DIR without importing the portfolio sub-system."""
    try:
        from ..charts import OUT_DIR  # type: ignore[import]
        return OUT_DIR
    except Exception:
        base = Path(__file__).resolve().parent.parent.parent / "data" / "charts"
        return base


# ---- related links -----------------------------------------------------------

def related_links(symbol: str) -> dict[str, str]:
    """
    Return a {name: url} dict of useful links for *symbol*.

    TW: Yahoo · Google · TradingView · Goodinfo · Wantgoo
    US: Yahoo · Google · TradingView · Finviz
    """
    sym = symbol.strip()
    # Detect TW: bare 4-digit numeric OR ends with .TW / .TWO
    tw_match = re.match(r"^(\d{4,5})(\.TW|\.TWO)?$", sym, re.IGNORECASE)
    if tw_match:
        code = tw_match.group(1)
        return {
            "Yahoo": f"https://tw.stock.yahoo.com/quote/{code}",
            "Google": f"https://www.google.com/search?q={code}+股價",
            "TradingView": f"https://www.tradingview.com/symbols/TWSE-{code}/",
            "Goodinfo": f"https://goodinfo.tw/tw/StockInfo/StockDetail.asp?STOCK_ID={code}",
            "Wantgoo": f"https://www.wantgoo.com/stock/{code}",
        }
    else:
        # US / other
        us_sym = sym.split(".")[0].upper()
        return {
            "Yahoo": f"https://finance.yahoo.com/quote/{us_sym}",
            "Google": f"https://www.google.com/search?q={us_sym}+stock+price",
            "TradingView": f"https://www.tradingview.com/symbols/{us_sym}/",
            "Finviz": f"https://finviz.com/quote.ashx?t={us_sym}",
        }


# ---- formatting helpers ------------------------------------------------------

def _fmt_compact(value) -> str:
    """Compact market-cap style: 1.2T / 50.0B / 800M / 1.2k."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return f"{v / div:.2f}{suf}" if div >= 1e12 else f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def _fmt_num(value, fmt: str) -> str:
    """Format with a python format spec; trailing '%' appends a percent sign."""
    if value is None:
        return "—"
    try:
        if fmt.endswith("%"):
            return f"{value:{fmt[:-1]}}%"
        return f"{value:{fmt}}"
    except (ValueError, TypeError):
        return str(value)


# ---- drawing primitives ------------------------------------------------------

def _round_rect(ax, x, y, w, h, *, fc, ec="none", lw=0.0, radius=0.04, z=1):
    """Add a subtly-rounded rectangle in *ax* axes-fraction coords."""
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=ax.transAxes, mutation_aspect=0.5,
        fc=fc, ec=ec, lw=lw, zorder=z, clip_on=False,
    )
    ax.add_patch(patch)
    return patch


def _section_title(ax, zh: str, en: str):
    """Accent bar + bilingual heading + bottom hairline (replaces circled numbers)."""
    ax.set_axis_off()
    # short crisp vertical accent bar
    ax.add_patch(Rectangle((0.0, 0.34), 0.009, 0.46, transform=ax.transAxes,
                           fc=_ACCENT, ec="none", zorder=3, clip_on=False))
    ax.text(0.026, 0.57, zh, transform=ax.transAxes,
            fontsize=11.5, fontweight="bold", va="center", ha="left", color=_INK)
    # english subtitle trails the chinese at a fixed offset, lighter weight
    ax.text(0.175, 0.55, en, transform=ax.transAxes,
            fontsize=8.5, va="center", ha="left", color=_FAINT)
    ax.plot([0.0, 1.0], [0.02, 0.02], transform=ax.transAxes,
            color=_HAIR, linewidth=1.0, zorder=1)


def _despine(ax, *, left=True, bottom=True):
    """Hide top/right spines and soften the rest."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(left if side == "left" else bottom)
        ax.spines[side].set_color(_HAIR)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=_FAINT, labelsize=7, length=0)


# ---- section renderers -------------------------------------------------------

def _render_hero(ax, symbol: str, fund: dict, df):
    """Hero strip: ticker + name (left), price + change pill (right), date."""
    ax.set_axis_off()
    name = fund.get("name") or ""

    # left: ticker (bold) + company name (muted) + update date
    ax.text(0.0, 0.78, symbol, transform=ax.transAxes,
            fontsize=20, fontweight="bold", va="center", ha="left", color=_INK)
    if name:
        ax.text(0.0, 0.40, name, transform=ax.transAxes,
                fontsize=11, va="center", ha="left", color=_MUTED)
    ax.text(0.0, 0.10, datetime.now().strftime("更新 %Y-%m-%d"), transform=ax.transAxes,
            fontsize=8, va="center", ha="left", color=_FAINT)

    # right: latest price + change pill (derived from OHLCV close series)
    if df is not None and not df.empty:
        closes = df["Close"].values
        last = float(closes[-1])
        ax.text(1.0, 0.74, f"{last:,.2f}", transform=ax.transAxes,
                fontsize=22, fontweight="bold", va="center", ha="right", color=_INK)
        if len(closes) >= 2:
            prev = float(closes[-2])
            chg = last - prev
            pct = (chg / prev * 100) if prev else 0.0
            color = _UP if chg >= 0 else _DOWN
            arrow = "▲" if chg >= 0 else "▼"
            ax.text(1.0, 0.30, f"{arrow} {chg:+.2f}  ({pct:+.2f}%)",
                    transform=ax.transAxes, fontsize=10, fontweight="bold",
                    va="center", ha="right", color="#ffffff",
                    bbox=dict(boxstyle="round,pad=0.45", fc=color, ec="none"))

    # baseline hairline under the hero
    ax.plot([0.0, 1.0], [-0.10, -0.10], transform=ax.transAxes,
            color=_HAIR, linewidth=1.2, solid_capstyle="round", clip_on=False)


def _render_price(ax, df, symbol: str):
    """價格 — candlestick + MA20/60/120, despined with soft y-grid."""
    if df is None or df.empty:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "無價格資料", transform=ax.transAxes,
                ha="center", va="center", color=_MUTED)
        return

    dates = np.arange(len(df))
    opens = df["Open"].values
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values

    width = 0.7 if len(dates) < 160 else 0.85

    # candle bodies + wicks
    for i in range(len(dates)):
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        color = _UP if c >= o else _DOWN
        bottom = min(o, c)
        height = abs(c - o) or (h - l) * 0.001  # avoid zero-height rect
        ax.bar(dates[i], height, bottom=bottom, color=color, width=width,
               linewidth=0, zorder=3)
        ax.plot([dates[i], dates[i]], [l, h], color=color, linewidth=0.5, zorder=2)

    # MA overlays
    close_s = df["Close"]
    for window, label in ((20, "MA20"), (60, "MA60"), (120, "MA120")):
        ma = close_s.rolling(window).mean()
        ax.plot(dates, ma.values, color=_MA_COLORS[label], linewidth=1.1,
                label=label, zorder=4)

    _despine(ax, left=False, bottom=False)
    ax.grid(axis="y", color=_HAIR, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlim(-1, len(dates))
    ax.tick_params(axis="x", labelbottom=False)

    leg = ax.legend(loc="upper left", fontsize=7, frameon=False,
                    handlelength=1.2, ncol=3, columnspacing=1.2, borderpad=0.2)
    for txt in leg.get_texts():
        txt.set_color(_MUTED)

    # right-edge price tag at the latest close
    last = float(closes[-1])
    tag_color = _UP if closes[-1] >= opens[-1] else _DOWN
    ax.text(1.0, last, f" {last:,.0f}", transform=ax.get_yaxis_transform(),
            va="center", ha="left", fontsize=7.5, fontweight="bold",
            color="#ffffff",
            bbox=dict(boxstyle="round,pad=0.25", fc=tag_color, ec="none"),
            clip_on=False, zorder=6)


def _render_fundamentals(ax, fund: dict):
    """基本面 — 3×3 card grid of metric tiles."""
    ax.set_axis_off()
    metrics = [
        ("P/E", _fmt_num(fund.get("pe"), ".1f")),
        ("預估 P/E", _fmt_num(fund.get("forward_pe"), ".1f")),
        ("P/B", _fmt_num(fund.get("pb"), ".2f")),
        ("殖利率", _fmt_num(fund.get("yield_pct"), ".2f%")),
        ("EPS", _fmt_num(fund.get("eps"), ".2f")),
        ("ROE", _fmt_num(fund.get("roe_pct"), ".1f%")),
        ("淨利率", _fmt_num(fund.get("margin_pct"), ".1f%")),
        ("市值", _fmt_compact(fund.get("market_cap"))),
        ("52週區間", _range_str(fund.get("wk52_low"), fund.get("wk52_high"))),
    ]
    cols, rows = 3, 3
    gap_x, gap_y = 0.025, 0.10
    tile_w = (1 - gap_x * (cols - 1)) / cols
    tile_h = (1 - gap_y * (rows - 1)) / rows

    for idx, (label, value) in enumerate(metrics):
        col, row = idx % cols, idx // cols
        x = col * (tile_w + gap_x)
        y = 1 - (row + 1) * tile_h - row * gap_y
        _round_rect(ax, x, y, tile_w, tile_h, fc=_CARD, ec=_HAIR, lw=0.8,
                    radius=0.025, z=1)
        is_na = value == "—"
        ax.text(x + 0.045, y + tile_h - 0.16, label, transform=ax.transAxes,
                fontsize=7.5, color=_MUTED, va="center", ha="left", zorder=2)
        ax.text(x + 0.045, y + 0.20, value, transform=ax.transAxes,
                fontsize=12 if len(value) <= 7 else 9.5, fontweight="bold",
                color=_FAINT if is_na else _INK, va="center", ha="left", zorder=2)


def _range_str(lo, hi) -> str:
    if lo is None or hi is None:
        return "—"
    try:
        return f"{float(lo):,.0f}–{float(hi):,.0f}"
    except (ValueError, TypeError):
        return "—"


def _render_revenue(ax, fund: dict):
    """營收（季） — quarterly bars + YoY labels, latest quarter highlighted."""
    rev_q = fund.get("revenue_q")
    if not rev_q or len(rev_q) < 2:
        ax.set_axis_off()
        ax.text(0.5, 0.5, _NA_MSG, transform=ax.transAxes,
                ha="center", va="center", fontsize=9, color=_MUTED)
        return

    labels = [r["period"] for r in rev_q]
    values = [r["value"] for r in rev_q]
    yoys = [r.get("yoy_pct") for r in rev_q]
    xs = np.arange(len(labels))

    bar_colors = []
    for i, y in enumerate(yoys):
        if y is None:
            bar_colors.append("#cbd5e1")
        else:
            base = _UP if y >= 0 else _DOWN
            # latest quarter at full strength; prior quarters muted
            bar_colors.append(base if i == len(yoys) - 1 else _soft(base))

    ax.bar(xs, values, color=bar_colors, width=0.62, zorder=3)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=7, color=_MUTED)

    _despine(ax, left=True, bottom=True)
    ax.grid(axis="y", color=_HAIR, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.margins(y=0.18)

    for i, (x, yoy) in enumerate(zip(xs, yoys)):
        if yoy is not None:
            color = _UP if yoy >= 0 else _DOWN
            ax.text(x, values[i], f"{yoy:+.1f}%", ha="center", va="bottom",
                    fontsize=7, fontweight="bold", color=color,
                    transform=ax.transData)


def _soft(hex_color: str) -> str:
    """Lighten a hex color toward white for muted prior-period bars."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda v: int(v + (255 - v) * 0.55)
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


# ---- main entry point --------------------------------------------------------

def render_panel(symbol: str, out_dir=None) -> str:
    """
    Compose and save the portrait panel PNG for *symbol*.

    Returns the path to the saved PNG (str).  Never raises — on error a
    minimal stub panel is written and its path returned.
    """
    from .data import load_or_download_stock_data, fundamentals, normalize_symbol

    # Resolve bare 4-digit TW codes.
    sym = normalize_symbol(symbol)

    # Fetch data (best-effort; sections degrade gracefully if None).
    end = datetime.now()
    start = end - timedelta(days=400)
    try:
        df = load_or_download_stock_data(sym, start, end)
    except Exception:
        df = None
    try:
        fund = fundamentals(sym)
    except Exception:
        fund = {}

    code = re.sub(r"\.(TW|TWO)$", "", sym, flags=re.IGNORECASE)
    out_path = Path(out_dir) if out_dir else _charts_dir()
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / f"panel_{code}.png"

    try:
        _compose(file_path, sym, df, fund)
    except Exception:
        # Last-resort stub so the caller always gets a valid PNG path.
        fig = plt.figure(figsize=(7, 2))
        fig.text(0.5, 0.5, f"{sym}\n（面板產生失敗）", ha="center", va="center",
                 fontsize=14, color=_INK)
        fig.savefig(str(file_path), dpi=130, facecolor=_BG)
        plt.close(fig)

    return str(file_path)


def _compose(file_path: Path, sym: str, df, fund: dict) -> None:
    """Lay out and write the full panel figure."""
    fig = plt.figure(figsize=(7, 9))
    fig.patch.set_facecolor(_BG)

    gs = gridspec.GridSpec(
        nrows=7, ncols=1, figure=fig,
        height_ratios=[1.15, 0.34, 3.0, 0.34, 2.4, 0.34, 2.0],
        hspace=0.16,
        top=0.965, bottom=0.065, left=0.075, right=0.95,
    )
    ax_hero = fig.add_subplot(gs[0, 0])
    ax_price_t = fig.add_subplot(gs[1, 0])
    ax_price = fig.add_subplot(gs[2, 0])
    ax_fund_t = fig.add_subplot(gs[3, 0])
    ax_fund = fig.add_subplot(gs[4, 0])
    ax_rev_t = fig.add_subplot(gs[5, 0])
    ax_rev = fig.add_subplot(gs[6, 0])

    _render_hero(ax_hero, sym, fund, df)

    _section_title(ax_price_t, "價格", "Price")
    _render_price(ax_price, df, sym)

    _section_title(ax_fund_t, "基本面", "Fundamentals")
    _render_fundamentals(ax_fund, fund)

    _section_title(ax_rev_t, "營收（季）", "Revenue · Quarterly")
    _render_revenue(ax_rev, fund)

    # footer: hairline + related links (accent) + CIO wordmark (right)
    fig.add_artist(plt.Line2D([0.075, 0.95], [0.038, 0.038],
                              color=_HAIR, linewidth=1.0, solid_capstyle="round"))
    links = related_links(sym)
    fig.text(0.075, 0.018, "相關連結", fontsize=8, color=_MUTED, va="center", ha="left")
    fig.text(0.165, 0.018, "  " + "  ·  ".join(links.keys()),
             fontsize=8, color=_ACCENT, va="center", ha="left")
    fig.text(0.95, 0.018, "CIO · Investment Committee",
             fontsize=7.5, color=_FAINT, va="center", ha="right")

    fig.savefig(str(file_path), dpi=130, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
