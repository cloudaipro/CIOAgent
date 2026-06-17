"""
bokeh adapter: ChartSpec -> interactive standalone HTML.

This is the refactored migration of the old AutoPlot (autotrader/bokeh) module
from the AI4StockMarket project. Rather than vendoring AutoPlot's ~1.8k lines —
most of which (renko, pivot points, grids, backtest overlays, supertrend/
halftrend) are unused here and written against the bokeh 2.x API — it keeps only
the indicator-charting capability the CIO needs and renders it through the SAME
backend-agnostic ChartSpec the matplotlib adapter uses (KISS + DRY).

HTML output needs only ``bokeh``; no selenium / webdriver (that was only ever
required for bokeh's static PNG export, which the matplotlib adapter now owns).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

import bokeh  # noqa: F401  fail fast at import if the optional dep is absent

from .spec import ChartSpec, build_spec
from . import style as S


def _out_dir() -> Path:
    try:
        from ...charts import OUT_DIR  # type: ignore[import]
        return OUT_DIR
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "charts"


def _fig(width, height, x_range=None, title=None):
    from bokeh.plotting import figure
    f = figure(width=width, height=height, x_range=x_range,
               tools="xpan,xwheel_zoom,box_zoom,reset,save",
               active_scroll="xwheel_zoom", toolbar_location="right",
               background_fill_color=S.BG, border_fill_color=S.BG)
    if title:
        f.title.text = title
        f.title.text_color = S.INK
    f.xgrid.grid_line_color = None
    f.ygrid.grid_line_color = S.HAIR
    f.outline_line_color = None
    f.axis.axis_line_color = S.HAIR
    f.axis.major_label_text_color = S.MUTED
    f.axis.minor_tick_line_color = None
    return f


def _candles(fig, spec: ChartSpec):
    df = spec.df
    x = list(spec.x)
    # channels (Bollinger / Keltner) behind the candles
    for bd in spec.price_bands:
        up = np.asarray(bd.upper, dtype=float)
        lo = np.asarray(bd.lower, dtype=float)
        if len(up) != spec.n or len(lo) != spec.n:
            continue
        dash = "dashed" if bd.style == "--" else "solid"
        if bd.fill_alpha:
            fig.varea(x=x, y1=[None if np.isnan(v) else v for v in lo],
                      y2=[None if np.isnan(v) else v for v in up],
                      fill_color=bd.color, fill_alpha=bd.fill_alpha)
        fig.line(x, [None if np.isnan(v) else v for v in up], color=bd.color,
                 line_width=bd.width, line_dash=dash, legend_label=bd.label)
        fig.line(x, [None if np.isnan(v) else v for v in lo], color=bd.color,
                 line_width=bd.width, line_dash=dash)
        if bd.mid is not None and len(bd.mid) == spec.n:
            fig.line(x, [None if np.isnan(v) else v for v in bd.mid],
                     color=bd.color, line_width=bd.width * 0.8, line_dash="dotted")
    o = df["Open"].values
    c = df["Close"].values
    h = df["High"].values
    lo = df["Low"].values
    pc = df["Close"].shift(1).values  # prev_close for hollow style
    fig.segment(x, h, x, lo, color=S.MUTED, line_width=0.6)
    if spec.candle_style == "hollow":
        # color = day-over-day; fill = solid if close<open, hollow if close>=open
        for i in x:
            day_up = (not np.isnan(pc[i])) and c[i] >= pc[i]
            color = S.UP if day_up else S.DOWN
            intraday_up = c[i] >= o[i]
            top_y = max(o[i], c[i])
            bot_y = min(o[i], c[i])
            if top_y == bot_y:
                top_y += 1e-6
            if intraday_up:
                # hollow: no fill, colored border
                fig.vbar(x=[i], width=0.7, top=[top_y], bottom=[bot_y],
                         fill_color=None, fill_alpha=0, line_color=color, line_width=1.0)
            else:
                # solid fill
                fig.vbar(x=[i], width=0.7, top=[top_y], bottom=[bot_y],
                         fill_color=color, line_color=color)
    else:
        # standard: close vs open
        inc = c >= o
        xi = [i for i in x if inc[i]]
        xd = [i for i in x if not inc[i]]
        if xi:
            fig.vbar(x=xi, width=0.7, top=[max(o[i], c[i]) for i in xi],
                     bottom=[min(o[i], c[i]) for i in xi], fill_color=S.UP,
                     line_color=S.UP)
        if xd:
            fig.vbar(x=xd, width=0.7, top=[max(o[i], c[i]) for i in xd],
                     bottom=[min(o[i], c[i]) for i in xd], fill_color=S.DOWN,
                     line_color=S.DOWN)
    for ov in spec.price_overlays:
        if ov.values is not None and len(ov.values) == spec.n:
            fig.line(x, list(ov.values), color=ov.color,
                     line_width=ov.width + 0.1, legend_label=ov.label)
    fig.legend.location = "top_left"
    fig.legend.label_text_font_size = "8pt"
    fig.legend.background_fill_alpha = 0.0
    fig.legend.border_line_color = None


def _flags(fig, flags, ys):
    for f in flags:
        if not (0 <= f.x < len(ys)):
            continue
        y = ys[f.x]
        if y is None or (isinstance(y, float) and np.isnan(y)):
            continue
        color = S.BEAR if f.kind == "bear" else S.BULL
        fig.scatter([f.x], [y], marker="inverted_triangle" if f.kind == "bear"
                    else "triangle", size=11, color=color)


def render_html(
    symbol_or_df,
    profile: str = "committee",
    *,
    indicators=None,
    window: int = 60,
    out_dir=None,
    filename: Optional[str] = None,
    symbol: Optional[str] = None,
    candle_style: str = "standard",
) -> str:
    """Render the indicator chart as standalone bokeh HTML; returns the path."""
    from bokeh.layouts import column
    from bokeh.models import Range1d, Span
    from bokeh.io import output_file, save

    kw = {} if indicators is None else {"indicators": indicators}
    spec = build_spec(symbol_or_df, profile, window=window, symbol=symbol,
                      candle_style=candle_style, **kw)
    x = list(spec.x)

    price = _fig(900, 380, x_range=(-1, spec.n),
                 title=f"{spec.symbol} · 指標視覺化 · {spec.profile} · {spec.asof}"
                       + (f"  [{(spec.composite or '').upper()}]" if spec.composite else ""))
    _candles(price, spec)
    _flags(price, [f for f in spec.price_flags if f.kind == "bear"],
           list(spec.df["High"].values))
    _flags(price, [f for f in spec.price_flags if f.kind == "bull"],
           list(spec.df["Low"].values))

    figs = [price]
    for panel in spec.panels:
        pf = _fig(900, 150, x_range=price.x_range,
                  title=panel.name + (f"  ({panel.verdict})" if panel.verdict else ""))
        if panel.hist is not None:
            hv = np.asarray(panel.hist[1], dtype=float)
            if panel.hist_colors is not None:
                fill = list(panel.hist_colors)       # TTM Squeeze 4-color scheme
                alpha = 0.9
            else:
                fill = [S.UP if (not np.isnan(v) and v >= 0) else S.DOWN for v in hv]
                alpha = 0.4
            pf.vbar(x=x, width=0.8,
                    top=[0 if np.isnan(v) else v for v in hv],
                    fill_color=fill, line_color=None, fill_alpha=alpha)
        if panel.dots:
            pf.scatter([d[0] for d in panel.dots], [0.0] * len(panel.dots),
                       marker="circle", size=5, color=[d[1] for d in panel.dots])
        for hl in panel.hlines:
            pf.add_layout(Span(location=hl.y, dimension="width",
                               line_color=hl.color, line_dash="dashed",
                               line_width=hl.width))
        for ln in panel.lines:
            v = np.asarray(ln.values, dtype=float)
            if len(v) == spec.n:
                pf.line(x, [None if np.isnan(t) else t for t in v],
                        color=ln.color, line_width=ln.width, legend_label=ln.label)
        if panel.flags and panel.lines:
            base = list(np.asarray(panel.lines[0].values, dtype=float))
            _flags(pf, panel.flags, base)
        if panel.ylim:
            pf.y_range = Range1d(*panel.ylim)
        if panel.lines:                       # no legend when only a histogram (Squeeze)
            pf.legend.location = "top_left"
            pf.legend.label_text_font_size = "7pt"
            pf.legend.background_fill_alpha = 0.0
            pf.legend.border_line_color = None
        figs.append(pf)

    out = Path(out_dir) if out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    if filename is None:
        safe = "".join(c for c in spec.symbol if c.isalnum() or c in "._-") or "chart"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"indicators_{safe}_{stamp}.html"
    path = out / filename
    output_file(str(path), title=f"{spec.symbol} 指標視覺化")
    save(column(*figs))
    return str(path)
