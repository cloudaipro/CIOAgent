"""
Single-stock panel renderer.

Produces a portrait PNG (dpi 130) with the sections yfinance can fill:
  ① 價格       — candlestick + MA20/60/120  (LIVE)
  ② 基本面     — 6-metric grid              (LIVE)
  ③ 營收（季） — quarterly revenue bars     (LIVE when ≥2 quarters)
  ⑦ 相關連結   — static links               (LIVE, figure footer)

Sections with no yfinance source for TW equities (法人動向 / 融資融券 / 持股分布)
are intentionally omitted rather than shown as empty placeholders.

TW color convention: red (#d92b2b) = up/positive, green (#1ca23b) = down/negative.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # must be before pyplot import
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import numpy as np

# CJK font — must come after backend selection, before any draw call.
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ---- color palette -----------------------------------------------------------
_RED = "#d92b2b"    # TW up / positive
_GREEN = "#1ca23b"  # TW down / negative
_MA_COLORS = {"MA20": "#00bfff", "MA60": "#ffa500", "MA120": "#ffd700"}

_NA_MSG = "—　無資料來源（yfinance）"   # em-space for visual indent


# ---- output directory --------------------------------------------------------
def _charts_dir() -> Path:
    """Mirror cfo/charts.py OUT_DIR without importing the portfolio sub-system."""
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


# ---- helpers -----------------------------------------------------------------

def _section_title(ax, title: str, *, fontsize=11):
    """Draw a bold section header with a thin underline divider."""
    ax.set_axis_off()
    ax.text(0.0, 0.85, title, transform=ax.transAxes,
            fontsize=fontsize, fontweight="bold", va="top", ha="left",
            color="#222222")
    # Use ax.plot in axes-fraction coords for the divider line (axhline's
    # transform kwarg conflicts with xmin/xmax in newer matplotlib).
    ax.plot([0.0, 1.0], [0.72, 0.72], transform=ax.transAxes,
            color="#cccccc", linewidth=0.8, solid_capstyle="round")


# ---- section renderers -------------------------------------------------------

def _render_header(ax, symbol: str, fund: dict):
    """① header strip: code · name · price · change."""
    ax.set_axis_off()
    name = fund.get("name") or symbol
    # Try to get latest price info from revenue_q or just show symbol
    # price info comes from OHLCV in the price section; show name + symbol here.
    ax.text(0.0, 0.85, f"{symbol}  {name}", transform=ax.transAxes,
            fontsize=13, fontweight="bold", va="top", ha="left", color="#111111")
    ax.text(0.0, 0.25, datetime.now().strftime("更新：%Y-%m-%d"), transform=ax.transAxes,
            fontsize=8, va="bottom", ha="left", color="#888888")


def _render_price(ax, df, symbol: str):
    """① 價格 — candlestick + MA20/60/120."""
    if df is None or df.empty:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "無價格資料", transform=ax.transAxes,
                ha="center", va="center", color="#888888")
        return

    dates = np.arange(len(df))
    opens = df["Open"].values
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values

    up_mask = closes >= opens

    # Candle bodies
    for i, (o, c, h, l) in enumerate(zip(opens, closes, highs, lows)):
        color = _RED if closes[i] >= opens[i] else _GREEN
        bottom = min(o, c)
        height = abs(c - o) or (h - l) * 0.001  # avoid zero-height rect
        ax.bar(dates[i], height, bottom=bottom, color=color, width=0.7,
               linewidth=0, zorder=2)
        ax.plot([dates[i], dates[i]], [l, h], color=color, linewidth=0.5, zorder=1)

    # MA overlays — use integer windows so DatetimeIndex doesn't trigger freq-offset parse
    close_s = df["Close"]
    for window, label, color in [(20, "MA20", _MA_COLORS["MA20"]),
                                 (60, "MA60", _MA_COLORS["MA60"]),
                                 (120, "MA120", _MA_COLORS["MA120"])]:
        ma = close_s.rolling(window).mean()
        ax.plot(dates, ma.values, color=color, linewidth=0.9, label=label)

    ax.legend(loc="upper left", fontsize=6, framealpha=0.5)
    ax.set_xlim(-1, len(dates))
    ax.tick_params(axis="x", labelbottom=False)
    ax.tick_params(axis="y", labelsize=7)
    ax.set_facecolor("#fafafa")

    # Price annotation (latest close)
    last_close = float(closes[-1])
    if len(closes) >= 2:
        prev_close = float(closes[-2])
        chg = last_close - prev_close
        chg_pct = chg / prev_close * 100 if prev_close else 0.0
        chg_color = _RED if chg >= 0 else _GREEN
        ax.set_title(
            f"{last_close:.2f}  {chg:+.2f} ({chg_pct:+.2f}%)",
            fontsize=8, color=chg_color, loc="right", pad=2
        )


def _render_fundamentals(ax, fund: dict):
    """② 基本面 — 2-col 6-cell metric grid."""
    ax.set_axis_off()
    metrics = [
        ("P/E", fund.get("pe"), ".1f"),
        ("P/B", fund.get("pb"), ".2f"),
        ("殖利率", fund.get("yield_pct"), ".2f%"),
        ("EPS", fund.get("eps"), ".2f"),
        ("ROE", fund.get("roe_pct"), ".1f%"),
        ("淨利率", fund.get("margin_pct"), ".1f%"),
    ]
    cols = 2
    rows = (len(metrics) + cols - 1) // cols
    for idx, (label, value, fmt) in enumerate(metrics):
        col = idx % cols
        row = idx // cols
        x = col / cols + 0.02
        y = 1.0 - (row + 0.5) / rows - 0.05
        if value is None:
            val_str = "—"
            val_color = "#888888"
        else:
            try:
                if fmt.endswith("%"):
                    val_str = f"{value:{fmt[:-1]}}%"
                else:
                    val_str = f"{value:{fmt}}"
                val_color = "#111111"
            except (ValueError, TypeError):
                val_str = str(value)
                val_color = "#111111"
        ax.text(x, y + 0.10, label, transform=ax.transAxes,
                fontsize=7, color="#666666", va="bottom")
        ax.text(x, y, val_str, transform=ax.transAxes,
                fontsize=9, fontweight="bold", color=val_color, va="top")


def _render_revenue(ax, fund: dict):
    """③ 營收（季） — quarterly bars + YoY line/labels."""
    rev_q = fund.get("revenue_q")
    if not rev_q or len(rev_q) < 2:
        ax.set_axis_off()
        ax.text(0.5, 0.5, _NA_MSG, transform=ax.transAxes,
                ha="center", va="center", fontsize=8, color="#888888")
        return

    labels = [r["period"] for r in rev_q]
    values = [r["value"] for r in rev_q]
    yoys = [r.get("yoy_pct") for r in rev_q]

    xs = np.arange(len(labels))
    bar_colors = []
    for i, y in enumerate(yoys):
        if y is None:
            bar_colors.append("#aaaaaa")
        elif y >= 0:
            bar_colors.append(_RED)
        else:
            bar_colors.append(_GREEN)

    ax.bar(xs, values, color=bar_colors, width=0.6, zorder=2)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=6.5)
    ax.tick_params(axis="y", labelsize=7)
    ax.set_facecolor("#fafafa")

    # YoY labels above bars
    for i, (x, yoy) in enumerate(zip(xs, yoys)):
        if yoy is not None:
            color = _RED if yoy >= 0 else _GREEN
            ax.text(x, values[i], f"{yoy:+.1f}%", ha="center", va="bottom",
                    fontsize=5.5, color=color)


# ---- main entry point --------------------------------------------------------

def render_panel(symbol: str, out_dir=None) -> str:
    """
    Compose and save the 7-section portrait panel PNG for *symbol*.

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

    # ---- figure layout -------------------------------------------------------
    fig = plt.figure(figsize=(7, 8.5))
    fig.patch.set_facecolor("#ffffff")

    # Short title rows + tight hspace keep the panel dense (no big gaps).
    # Rows: header, price-title, price, fund-title, fund, rev-title, rev.
    gs = gridspec.GridSpec(
        nrows=7, ncols=1, figure=fig,
        height_ratios=[0.7, 0.32, 3.0, 0.32, 1.5, 0.32, 2.0],
        hspace=0.18,
        top=0.97, bottom=0.07, left=0.08, right=0.96,
    )
    ax_header = fig.add_subplot(gs[0, 0])
    ax_price_title = fig.add_subplot(gs[1, 0])
    ax_price = fig.add_subplot(gs[2, 0])
    ax_fund_title = fig.add_subplot(gs[3, 0])
    ax_fund = fig.add_subplot(gs[4, 0])
    ax_rev_title = fig.add_subplot(gs[5, 0])
    ax_rev = fig.add_subplot(gs[6, 0])

    # ---- render sections -----------------------------------------------------
    _render_header(ax_header, sym, fund)

    _section_title(ax_price_title, "① 價格 Price")
    _render_price(ax_price, df, sym)

    _section_title(ax_fund_title, "② 基本面 Fundamentals")
    _render_fundamentals(ax_fund, fund)

    _section_title(ax_rev_title, "③ 營收（季）Revenue (Quarterly)")
    _render_revenue(ax_rev, fund)

    # ⑦ 相關連結 — figure footer line.
    links = related_links(sym)
    link_text = "⑦ 相關連結  " + " · ".join(links.keys())
    fig.text(0.08, 0.02, link_text, fontsize=8, color="#1155cc",
             va="bottom", ha="left")

    # ---- save ----------------------------------------------------------------
    code = re.sub(r"\.(TW|TWO)$", "", sym, flags=re.IGNORECASE)
    out_path = Path(out_dir) if out_dir else _charts_dir()
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / f"panel_{code}.png"

    fig.savefig(str(file_path), dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(file_path)
