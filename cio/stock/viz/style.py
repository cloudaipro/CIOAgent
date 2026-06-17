"""
Shared design tokens + low-level matplotlib drawing helpers for the
indicator-visualization package.

These mirror cio/stock/panel.py's design system so an indicator chart and a
stock panel look like they came from the same product. The tokens are plain
hex strings (not behaviour), so keeping a copy here rather than importing
panel.py's private names is intentional: viz must not depend on the panel
renderer, and the panel may reuse these later.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless, must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# CJK font — after backend selection, before any draw call.
plt.rcParams["font.sans-serif"] = [
    "Noto Sans CJK JP",
    "Droid Sans Fallback",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

# ---- design tokens (mirror panel.py) ----------------------------------------
INK = "#1f2937"      # primary text
MUTED = "#64748b"    # secondary text / labels
FAINT = "#94a3b8"    # tertiary (dates, axis ticks)
HAIR = "#e6e8ec"     # hairlines / dividers
CARD = "#f5f6f8"     # tile fill
ACCENT = "#334155"   # section accent / wordmark
BG = "#ffffff"

UP = "#16a34a"       # up / positive (green)
DOWN = "#d92b2b"     # down / negative (red)

MA_COLORS = {"MA20": "#2563eb", "MA60": "#f59e0b", "MA120": "#94a3b8"}

# Indicator line palette (kept distinct from MA + up/down colors).
LINE = {
    "macd": "#2563eb",
    "signal": "#d97706",
    "rsi": "#7c3aed",
    "k": "#2563eb",
    "d": "#d97706",
    "j": "#db2777",
    "generic": "#334155",
}

BULL = UP
BEAR = DOWN


def despine(ax, *, left=True, bottom=True):
    """Remove top/right spines; optionally drop left/bottom too."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(left)
    ax.spines["bottom"].set_visible(bottom)
    for side in ("left", "bottom"):
        if ax.spines[side].get_visible():
            ax.spines[side].set_color(HAIR)
    ax.tick_params(colors=MUTED, labelsize=7, length=0)


def candlestick(ax, df, *, width=None, zorder=3, candle_style="standard"):
    """Draw OHLC candles on an integer x-axis (0..len-1). No axis styling.

    candle_style:
      "standard" — color = close vs open (intraday direction)
      "hollow"   — color = close vs prev_close (day-over-day direction);
                   hollow outline when close >= open, solid fill when close < open
    """
    opens = np.asarray(df["Open"].values, dtype=float)
    closes = np.asarray(df["Close"].values, dtype=float)
    highs = np.asarray(df["High"].values, dtype=float)
    lows = np.asarray(df["Low"].values, dtype=float)
    prev_closes = np.asarray(df["Close"].shift(1).values, dtype=float)
    n = len(closes)
    x = np.arange(n)
    if width is None:
        width = 0.7 if n < 160 else 0.85
    for i in range(n):
        o, c, h, lo = opens[i], closes[i], highs[i], lows[i]
        if candle_style == "hollow":
            pc = prev_closes[i]
            day_up = (not np.isnan(pc)) and c >= pc
            color = UP if day_up else DOWN
            intraday_up = c >= o
            if intraday_up:
                # hollow: outline only (up day, gained intraday too — or held)
                fc, ec, lw = "none", color, 0.8
            else:
                # solid fill: up day but sold off intraday
                fc, ec, lw = color, color, 0
        else:
            # standard: close vs open
            color = UP if c >= o else DOWN
            fc, ec, lw = color, color, 0
        bottom = min(o, c)
        height = abs(c - o) or max((h - lo) * 0.001, 1e-9)  # avoid zero-height
        ax.bar(x[i], height, bottom=bottom, color=fc, edgecolor=ec,
               width=width, linewidth=lw, zorder=zorder)
        ax.plot([x[i], x[i]], [lo, h], color=color, linewidth=0.5,
                zorder=zorder - 1)
    return x
