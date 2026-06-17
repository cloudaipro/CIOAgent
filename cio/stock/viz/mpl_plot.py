"""
matplotlib adapter: ChartSpec -> PNG.

Primary render path — feeds Telegram photos and the committee PDF. Headless
(Agg), no browser, design-matched to cio/stock/panel.py.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from . import style as S
from .spec import ChartSpec, Flag, Marker, Panel, build_spec


def _out_dir() -> Path:
    try:
        from ...charts import OUT_DIR  # type: ignore[import]
        return OUT_DIR
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "charts"


def _verdict_color(v: Optional[str]) -> str:
    if v == "bull":
        return S.BULL
    if v == "bear":
        return S.BEAR
    return S.MUTED


def _state_color(level: Optional[str]) -> str:
    if level == "pos":
        return S.BULL
    if level == "neg":
        return S.BEAR
    return S.MUTED


def _date_ticks(spec: ChartSpec, max_ticks: int = 6):
    idx = spec.df.index
    n = spec.n
    if n == 0:
        return [], []
    step = max(1, n // max_ticks)
    pos = list(range(0, n, step))
    if pos[-1] != n - 1:
        pos.append(n - 1)
    labels = []
    for p in pos:
        d = idx[p]
        labels.append(d.strftime("%m/%d") if hasattr(d, "strftime") else str(d))
    return pos, labels


def _draw_markers(ax, markers: Sequence[Marker]):
    for m in markers:
        color = S.BEAR if m.kind == "bear" else S.BULL
        ax.plot([m.x0, m.x1], [m.y0, m.y1], color=color, linewidth=1.4,
                linestyle="--", zorder=7, alpha=0.9)
        ax.scatter([m.x0, m.x1], [m.y0, m.y1], s=18, color=color, zorder=8)
        if m.label:
            ax.annotate(
                ("⚠ " if m.kind == "bear" else "") + m.label,
                xy=(m.x1, m.y1), xytext=(0, 8 if m.kind == "bear" else -12),
                textcoords="offset points", fontsize=6.5, color=color,
                ha="right", fontweight="bold", zorder=9)


def _draw_flags(ax, flags, ys, *, top: bool):
    """Triangle event flags at given bars (committee divergence)."""
    if not flags:
        return
    ymin, ymax = ax.get_ylim()
    span = ymax - ymin
    for f in flags:
        color = S.BEAR if f.kind == "bear" else S.BULL
        if 0 <= f.x < len(ys) and ys[f.x] is not None and not np.isnan(ys[f.x]):
            y = ys[f.x]
        else:
            y = ymax if top else ymin
        off = (0.06 if top else -0.06) * span
        marker = "v" if f.kind == "bear" else "^"
        ax.scatter([f.x], [y + off], marker=marker, s=42, color=color,
                   zorder=9, clip_on=False)


def _render_price(ax, spec: ChartSpec):
    df = spec.df
    if df is None or len(df) == 0:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "無價格資料", transform=ax.transAxes,
                ha="center", va="center", color=S.MUTED)
        return
    xb = np.arange(spec.n)
    for bd in spec.price_bands:
        up = np.asarray(bd.upper, dtype=float)
        lo = np.asarray(bd.lower, dtype=float)
        if len(up) != spec.n or len(lo) != spec.n:
            continue
        ax.plot(xb, up, color=bd.color, linewidth=bd.width, linestyle=bd.style,
                zorder=1, alpha=0.85, label=bd.label)
        ax.plot(xb, lo, color=bd.color, linewidth=bd.width, linestyle=bd.style,
                zorder=1, alpha=0.85)
        if bd.mid is not None and len(bd.mid) == spec.n:
            ax.plot(xb, bd.mid, color=bd.color, linewidth=bd.width * 0.8,
                    linestyle=":", zorder=1, alpha=0.6)
        if bd.fill_alpha:
            ax.fill_between(xb, lo, up, color=bd.color, alpha=bd.fill_alpha, zorder=0)

    x = S.candlestick(ax, df)

    for ov in spec.price_overlays:
        if ov.values is None or len(ov.values) != spec.n:
            continue
        ax.plot(x, ov.values, color=ov.color, linewidth=ov.width,
                label=ov.label, zorder=4)

    # swing anchors
    for px, py, tag in spec.swings:
        up = tag in ("HH", "LH", "H")
        ax.annotate(tag, xy=(px, py), xytext=(0, 6 if up else -10),
                    textcoords="offset points", fontsize=6, color=S.FAINT,
                    ha="center", zorder=5)

    _draw_markers(ax, spec.price_markers)
    highs = df["High"].values
    _draw_flags(ax, [f for f in spec.price_flags if f.kind == "bear"],
                list(highs), top=True)
    _draw_flags(ax, [f for f in spec.price_flags if f.kind == "bull"],
                list(df["Low"].values), top=False)

    S.despine(ax, left=False, bottom=False)
    ax.grid(axis="y", color=S.HAIR, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlim(-1, spec.n)
    ax.tick_params(axis="x", labelbottom=False)

    if spec.price_overlays or spec.price_bands:
        leg = ax.legend(loc="upper left", fontsize=7, frameon=False,
                        handlelength=1.2, ncol=3, columnspacing=1.2, borderpad=0.2)
        for txt in leg.get_texts():
            txt.set_color(S.MUTED)

    closes = df["Close"].values
    opens = df["Open"].values
    last = float(closes[-1])
    tag_color = S.UP if closes[-1] >= opens[-1] else S.DOWN
    ax.text(1.0, last, f" {last:,.2f}", transform=ax.get_yaxis_transform(),
            va="center", ha="left", fontsize=7.5, fontweight="bold",
            color="#ffffff",
            bbox=dict(boxstyle="round,pad=0.25", fc=tag_color, ec="none"),
            clip_on=False, zorder=6)


def _render_panel(ax, panel: Panel, spec: ChartSpec, is_last: bool):
    x = spec.x
    # histogram (MACD / TTM Squeeze) first, behind lines
    if panel.hist is not None:
        _, hv = panel.hist
        hv = np.asarray(hv, dtype=float)
        if panel.hist_colors is not None:
            colors = panel.hist_colors           # TTM Squeeze 4-color scheme
            alpha = 0.9
        else:
            colors = [S.UP if (not np.isnan(v) and v >= 0) else S.DOWN for v in hv]
            alpha = 0.45
        ax.bar(x, np.nan_to_num(hv), width=0.8, color=colors, linewidth=0,
               alpha=alpha, zorder=2)

    # zero-line squeeze dots (red = ON / compressed, green = OFF / fired)
    if panel.dots:
        dx = [d[0] for d in panel.dots]
        dc = [d[1] for d in panel.dots]
        ax.scatter(dx, [0.0] * len(dx), s=10, c=dc, marker="o", zorder=6,
                   edgecolors="none")

    for hl in panel.hlines:
        ax.axhline(hl.y, color=hl.color, linestyle=hl.style,
                   linewidth=hl.width, zorder=1)

    for ln in panel.lines:
        v = np.asarray(ln.values, dtype=float)
        if len(v) != spec.n:
            continue
        ax.plot(x, v, color=ln.color, linewidth=ln.width, label=ln.label, zorder=3)

    _draw_markers(ax, panel.markers)
    if panel.flags and panel.lines:
        base = np.asarray(panel.lines[0].values, dtype=float)
        _draw_flags(ax, [f for f in panel.flags if f.kind == "bear"], list(base), top=True)
        _draw_flags(ax, [f for f in panel.flags if f.kind == "bull"], list(base), top=False)

    S.despine(ax, left=False, bottom=is_last)
    ax.set_xlim(-1, spec.n)
    if panel.ylim:
        ax.set_ylim(*panel.ylim)

    # panel title chip + verdict
    title = panel.name
    ax.text(0.0, 1.0, title, transform=ax.transAxes, fontsize=8,
            fontweight="bold", color=S.INK, va="top", ha="left")
    # Prefer the deterministic 2-D state chip (level·direction + cross note) over
    # the one-word verdict — the bare word ("neutral") collapsed level+direction
    # and misled the chart's downstream narrator. Fall back to verdict if absent.
    if panel.chip:
        ax.text(0.12, 1.0, panel.chip, transform=ax.transAxes,
                fontsize=6.5, color=_state_color((panel.state or {}).get("level")),
                va="top", ha="left")
    elif panel.verdict:
        ax.text(0.12, 1.0, panel.verdict, transform=ax.transAxes,
                fontsize=7, color=_verdict_color(panel.verdict), va="top",
                ha="left")
    if panel.lines:
        leg = ax.legend(loc="upper right", fontsize=6.5, frameon=False,
                        handlelength=1.0, ncol=len(panel.lines), columnspacing=1.0,
                        borderpad=0.1)
        if leg:
            for txt in leg.get_texts():
                txt.set_color(S.MUTED)

    if is_last:
        pos, labels = _date_ticks(spec)
        ax.set_xticks(pos)
        ax.set_xticklabels(labels, fontsize=6.5, color=S.FAINT)
    else:
        ax.tick_params(axis="x", labelbottom=False)


def render(
    symbol_or_df,
    profile: str = "committee",
    *,
    indicators=None,
    window: int = 60,
    out_dir=None,
    filename: Optional[str] = None,
    symbol: Optional[str] = None,
) -> str:
    """
    Render an indicator chart PNG and return its path.

    Accepts a ticker string or an OHLC DataFrame (same contract as build_spec).
    """
    import matplotlib.pyplot as plt

    kw = {} if indicators is None else {"indicators": indicators}
    spec = build_spec(symbol_or_df, profile, window=window, symbol=symbol, **kw)

    n_panels = len(spec.panels)
    height_ratios = [3.2] + [1.05] * n_panels
    fig_h = 4.2 + 1.3 * n_panels
    fig = plt.figure(figsize=(8.0, fig_h), dpi=130)
    fig.patch.set_facecolor(S.BG)
    import matplotlib.gridspec as gridspec
    gs = gridspec.GridSpec(1 + n_panels, 1, height_ratios=height_ratios,
                           hspace=0.12, left=0.07, right=0.94,
                           top=0.90, bottom=0.06)

    # header
    comp = spec.composite or ""
    comp_color = _verdict_color(comp if comp in ("bull", "bear") else None)
    fig.text(0.07, 0.965, f"{spec.symbol}", fontsize=15, fontweight="bold",
             color=S.INK, ha="left", va="top")
    fig.text(0.07, 0.93, f"指標視覺化 · {spec.profile} profile · as of {spec.asof}",
             fontsize=8.5, color=S.MUTED, ha="left", va="top")
    if comp:
        fig.text(0.94, 0.965, comp.upper(), fontsize=10, fontweight="bold",
                 color=comp_color, ha="right", va="top")

    ax_price = fig.add_subplot(gs[0])
    _render_price(ax_price, spec)

    for i, panel in enumerate(spec.panels):
        ax = fig.add_subplot(gs[i + 1], sharex=ax_price)
        _render_panel(ax, panel, spec, is_last=(i == n_panels - 1))

    fig.text(0.07, 0.018, "CIO · 指標視覺化", fontsize=7, color=S.FAINT, ha="left")
    fig.text(0.94, 0.018,
             "divergence = 價創新高/低、動能未跟上", fontsize=6.5,
             color=S.FAINT, ha="right")

    out = Path(out_dir) if out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    if filename is None:
        safe = "".join(c for c in spec.symbol if c.isalnum() or c in "._-") or "chart"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"indicators_{safe}_{stamp}.png"
    path = out / filename
    fig.savefig(path, facecolor=S.BG, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return str(path)
