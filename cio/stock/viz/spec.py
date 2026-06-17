"""
Shared indicator-visualization core — the single source of truth that both the
matplotlib (PNG) and bokeh (HTML) adapters consume. Nothing here draws; it only
computes a backend-agnostic ``ChartSpec``.

Design follows the old AI4StockMarket ``AutoPlot`` contract: callers describe the
chart with a generic, typed ``indicators`` dict and the core translates it into
overlays + sub-panels. No indicator is special-cased in the engine — RSI/MACD/
KDJ are just the *default preset*, itself expressed in the same dict contract.

indicators dict
---------------
``{label: {"type": <type>, ...}}``. Placement is decided by type:

  over-chart (drawn on the price panel)
    "over"     {data: Series, color?}                generic line
    "MA"       {data: Series, color?}                moving-average line
    "Swings"   {data: swings_df|Series}              HH/HL/LH/LL markers
    "flags"    {bull?: BoolSeries, bear?: BoolSeries, target?: label|"price"}
                                                      ▲/▼ event triangles

  below-chart (each gets its own sub-panel)
    "below"     {data: Series, color?, levels?: [..]}   generic line panel
    "RSI"       {data: Series, levels?: [30,50,70]}      single-line + guides
    "MACD"      {macd, signal, histogram}                line+line+hist
    "multi"     {<name>: {data, color}, ..., levels?}    N lines (e.g. KDJ)
    "Crossover" {line1, line2, color1?, color2?}         two lines + zero guide
    "threshold" {data, levels: [..], color?}             line + level guides

``color`` overrides the auto palette anywhere a line is drawn. ``over_cap`` /
``below_cap`` bound how many of each are rendered (autoplot's max_indis_*).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
import pandas_ta  # noqa: F401  registers the df.ta accessor used by presets

# Default preset = exactly what conv_turns#210 sends users to TradingView for.
DEFAULT_INDICATORS: tuple[str, ...] = ("MACD", "RSI", "KDJ")
_OHLC = ("Open", "High", "Low", "Close")

_OVER_TYPES = {"over", "MA", "Swings", "flags", "bands"}
_BELOW_TYPES = {"below", "RSI", "MACD", "multi", "Crossover", "threshold", "squeeze"}

# TTM Squeeze momentum-histogram 4-color scheme (matches thinkorswim TTM_Squeeze)
_SQZ_POS_UP = "#22d3ee"    # positive & rising  — cyan
_SQZ_POS_DN = "#2563eb"    # positive & falling — blue
_SQZ_NEG_DN = "#dc2626"    # negative & falling — red
_SQZ_NEG_UP = "#f59e0b"    # negative & rising  — yellow
_SQZ_DOT_ON = "#dc2626"    # squeeze ON  (compressed) — red
_SQZ_DOT_OFF = "#16a34a"   # squeeze OFF (fired)      — green
_PALETTE = ["#2563eb", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d",
            "#dc2626", "#475569"]


# --------------------------------------------------------------------------- #
# dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Marker:
    """A divergence annotation: a sloped line between two pivots + a label."""
    x0: int
    y0: float
    x1: int
    y1: float
    kind: str              # "bull" | "bear"
    label: str = ""


@dataclass
class Flag:
    """A single-bar event flag (committee divergence, BUY/SELL signal, …)."""
    x: int
    kind: str              # "bull" | "bear"
    label: str = ""


@dataclass
class Line:
    label: str
    values: np.ndarray
    color: str
    width: float = 1.1


@dataclass
class Band:
    """An upper/lower channel drawn on the price panel (Bollinger, Keltner …)."""
    label: str
    upper: np.ndarray
    lower: np.ndarray
    color: str
    mid: Optional[np.ndarray] = None
    style: str = "-"
    fill_alpha: float = 0.0
    width: float = 0.9


@dataclass
class HLine:
    y: float
    color: str
    style: str = "--"
    width: float = 0.7


@dataclass
class Panel:
    """One sub-chart below the price panel."""
    name: str
    lines: list[Line] = field(default_factory=list)
    hist: Optional[tuple[str, np.ndarray]] = None
    hist_colors: Optional[list[str]] = None          # per-bar colors (TTM Squeeze)
    dots: list[tuple[int, str]] = field(default_factory=list)  # zero-line (x, color)
    hlines: list[HLine] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    ylim: Optional[tuple[float, float]] = None
    verdict: Optional[str] = None
    chip: Optional[str] = None              # deterministic 2-D state string (state.py)
    state: Optional[dict] = None            # full state dict (provenance for text/LLM path)


@dataclass
class ChartSpec:
    symbol: str
    df: pd.DataFrame
    price_bands: list[Band]                   # channels on price (Bollinger, Keltner)
    price_overlays: list[Line]               # lines drawn on the price panel (MA, …)
    price_markers: list[Marker]              # geometric divergence on price
    price_flags: list[Flag]                  # event flags on price
    swings: list[tuple[int, float, str]]     # (x, y, "HH"/"HL"/"LH"/"LL")
    panels: list[Panel]
    verdicts: dict[str, str]
    composite: Optional[str]
    profile: str
    asof: str
    n: int
    states: dict = field(default_factory=dict)   # {panel_name: state dict} (state.py)

    @property
    def x(self) -> np.ndarray:
        return np.arange(self.n)

    # convenience for legacy callers/tests that referenced .ma
    @property
    def ma(self) -> dict[str, np.ndarray]:
        return {ln.label: ln.values for ln in self.price_overlays}


# --------------------------------------------------------------------------- #
# divergence — deterministic, scipy-free
# --------------------------------------------------------------------------- #
def _pivots(values: np.ndarray, left: int = 2, right: int = 2) -> tuple[list[int], list[int]]:
    n = len(values)
    highs, lows = [], []
    for i in range(left, n - right):
        w = values[i - left:i + right + 1]
        c = values[i]
        if np.isnan(c) or np.isnan(w).any():
            continue
        if c == np.nanmax(w) and (w == c).sum() == 1:
            highs.append(i)
        if c == np.nanmin(w) and (w == c).sum() == 1:
            lows.append(i)
    return highs, lows


def divergence_markers(
    price: np.ndarray,
    indicator: np.ndarray,
    *,
    lookback: int = 60,
    left: int = 2,
    right: int = 2,
) -> list[Marker]:
    """Regular bullish/bearish divergence between price and an oscillator."""
    n = len(price)
    if n < left + right + 3:
        return []
    start = max(0, n - lookback)
    p = np.asarray(price, dtype=float)
    ind = np.asarray(indicator, dtype=float)

    p_hi, p_lo = _pivots(p, left, right)
    i_hi, i_lo = _pivots(ind, left, right)
    p_hi = [i for i in p_hi if i >= start]
    p_lo = [i for i in p_lo if i >= start]
    i_hi = [i for i in i_hi if i >= start]
    i_lo = [i for i in i_lo if i >= start]

    out: list[Marker] = []
    if len(p_hi) >= 2 and len(i_hi) >= 2:
        a, b = p_hi[-2], p_hi[-1]
        ia = min(i_hi, key=lambda k: abs(k - a))
        ib = min(i_hi, key=lambda k: abs(k - b))
        if ia != ib and p[b] > p[a] and ind[ib] < ind[ia]:
            out.append(Marker(ia, float(ind[ia]), ib, float(ind[ib]), "bear", "bear div"))
    if len(p_lo) >= 2 and len(i_lo) >= 2:
        a, b = p_lo[-2], p_lo[-1]
        ia = min(i_lo, key=lambda k: abs(k - a))
        ib = min(i_lo, key=lambda k: abs(k - b))
        if ia != ib and p[b] < p[a] and ind[ib] > ind[ia]:
            out.append(Marker(ia, float(ind[ia]), ib, float(ind[ib]), "bull", "bull div"))
    return out


# --------------------------------------------------------------------------- #
# swing / structure anchors
# --------------------------------------------------------------------------- #
def _swing_anchors(df, lookback=60, left=3, right=3, keep=4) -> list[tuple[int, float, str]]:
    highs_arr = np.asarray(df["High"].values, dtype=float)
    lows_arr = np.asarray(df["Low"].values, dtype=float)
    total = len(df)
    if total < left + right + 2:
        return []
    start = max(0, total - lookback)
    hi_idx, _ = _pivots(highs_arr, left, right)
    _, lo_idx = _pivots(lows_arr, left, right)
    hi_idx = [i for i in hi_idx if i >= start][-keep:]
    lo_idx = [i for i in lo_idx if i >= start][-keep:]
    anchors: list[tuple[int, float, str]] = []
    for j, i in enumerate(hi_idx):
        tag = "H" if j == 0 else ("HH" if highs_arr[i] > highs_arr[hi_idx[j - 1]] else "LH")
        anchors.append((i, float(highs_arr[i]), tag))
    for j, i in enumerate(lo_idx):
        tag = "L" if j == 0 else ("HL" if lows_arr[i] > lows_arr[lo_idx[j - 1]] else "LL")
        anchors.append((i, float(lows_arr[i]), tag))
    return anchors


# --------------------------------------------------------------------------- #
# committee-strategy divergence flags
# --------------------------------------------------------------------------- #
def _strategy_divergence(full: pd.DataFrame, names: Sequence[str]) -> dict[str, list[Flag]]:
    """DIVERGENCE_BULL/BEAR flags from each strategy that emits them (any of the
    ~40, not just macd/rsi/kdj), so the chart matches the committee/profile
    narrative. Returns {panel_label_upper: [Flag(pos, kind)]}; strategies with no
    divergence columns simply contribute nothing."""
    out: dict[str, list[Flag]] = {}
    try:
        from .. import get_engine
        eng = get_engine()
    except Exception:
        return out
    pos_of = {ts: i for i, ts in enumerate(full.index)}
    for name in names:
        strat = name.lower()
        try:
            sig = eng.run(full, strat)
        except Exception:
            continue
        flags: list[Flag] = []
        for kind, col in (("bear", f"c_{strat.upper()}_DIVERGENCE_BEAR"),
                          ("bull", f"c_{strat.upper()}_DIVERGENCE_BULL")):
            if col not in sig.columns:
                continue
            for ts in sig.index[sig[col].fillna(0).astype(bool)]:
                i = pos_of.get(ts)
                if i is not None:
                    flags.append(Flag(i, kind, f"{name} div"))
        if flags:
            out[name.upper()] = flags
    return out


# --------------------------------------------------------------------------- #
# series alignment helpers
# --------------------------------------------------------------------------- #
def _align(data, index) -> np.ndarray:
    """Project a Series/array/DataFrame-column onto ``index`` as a float array."""
    if isinstance(data, pd.DataFrame):
        data = data.iloc[:, 0]
    if isinstance(data, pd.Series):
        return np.asarray(data.reindex(index).values, dtype=float)
    arr = np.asarray(data, dtype=float)
    if len(arr) == len(index):
        return arr
    out = np.full(len(index), np.nan)
    out[-len(arr):] = arr[-len(index):]
    return out


def _bool_positions(data, index) -> list[int]:
    """Integer positions where a boolean-ish series is true."""
    if data is None:
        return []
    if isinstance(data, pd.Series):
        s = data.reindex(index).fillna(0).astype(bool)
        return [i for i, v in enumerate(s.values) if v]
    arr = np.asarray(data)
    return [i for i, v in enumerate(arr[-len(index):]) if bool(v)]


# --------------------------------------------------------------------------- #
# indicator registry — strategy name -> generic-dict entry (placement + series)
# --------------------------------------------------------------------------- #
def _macd_like(m):
    c = list(m.columns)  # value, hist, signal  (pandas_ta order)
    return {"type": "MACD", "macd": m[c[0]], "signal": m[c[2]], "histogram": m[c[1]]}


def _two_line(a, b, *, levels=(0,), la="line", lb="signal"):
    return {"type": "multi", "levels": list(levels),
            la: {"data": a, "color": "#2563eb"},
            lb: {"data": b, "color": "#d97706"}}


# Each builder takes the OHLCV frame and returns one indicators-dict entry.
# Placement (over vs below) is carried by the entry's "type".
_REGISTRY = {
    "macd":   lambda df: _macd_like(df.ta.macd()),
    "pvo":    lambda df: _macd_like(df.ta.pvo()),
    "rsi":    lambda df: {"type": "RSI", "data": df.ta.rsi(), "levels": [30, 50, 70]},
    "kdj":    lambda df: (lambda k, c=None: {
        "type": "multi", "levels": [20, 80],
        "K": {"data": k[k.columns[0]], "color": "#2563eb"},
        "D": {"data": k[k.columns[1]], "color": "#d97706"},
        "J": {"data": k[k.columns[2]], "color": "#db2777"}})(df.ta.kdj()),
    "stoch":  lambda df: (lambda s: _two_line(s[s.columns[0]], s[s.columns[1]],
                                              levels=(20, 80), la="K", lb="D"))(df.ta.stoch()),
    "trix":   lambda df: (lambda t: _two_line(t[t.columns[0]], t[t.columns[1]],
                                              la="TRIX", lb="signal"))(df.ta.trix()),
    "kst":    lambda df: (lambda t: _two_line(t[t.columns[0]], t[t.columns[1]],
                                              la="KST", lb="signal"))(df.ta.kst()),
    "fisher": lambda df: (lambda f: _two_line(f[f.columns[0]], f[f.columns[1]],
                                              la="Fisher", lb="trigger"))(df.ta.fisher()),
    "cmf":    lambda df: {"type": "below", "data": df.ta.cmf(), "levels": [0], "color": "#0891b2"},
    "efi":    lambda df: {"type": "below", "data": df.ta.efi(), "levels": [0], "color": "#0891b2"},
    "er":     lambda df: {"type": "below", "data": df.ta.er(), "levels": [0.5], "color": "#65a30d"},
    "squeeze": lambda df: (lambda s: {"type": "squeeze",
                                      "momentum": s[s.columns[0]],
                                      "on": s["SQZ_ON"] if "SQZ_ON" in s.columns else None})(
        df.ta.squeeze()),
    "vidya":  lambda df: {"type": "over", "data": df.ta.vidya(), "color": "#0891b2"},
}

# nicer display labels (fallback = name.upper())
_LABELS = {"vidya": "VIDYA", "squeeze": "Squeeze", "fisher": "Fisher",
           "efi": "EFI", "er": "ER"}


def _generic_entry(df: pd.DataFrame, name: str) -> Optional[dict]:
    """Fallback for any indicator not in the registry: call df.ta.<name>() and
    plot the first column as a below-panel line (best-effort)."""
    fn = getattr(df.ta, name.lower(), None)
    if fn is None:
        return None
    r = fn()
    if r is None:
        return None
    if hasattr(r, "columns"):
        if r.empty:
            return None
        return {"type": "below", "data": r[r.columns[0]]}
    if len(r) == 0:
        return None
    return {"type": "below", "data": r}


# --------------------------------------------------------------------------- #
# default preset — expressed in the generic dict contract
# --------------------------------------------------------------------------- #
def default_indicator_dict(
    df: pd.DataFrame,
    names: Sequence[str] = DEFAULT_INDICATORS,
    *,
    mas: Sequence[int] = (20, 60, 120),
    include_ma: bool = True,
) -> dict:
    """Build an ``indicators`` dict for the given strategy ``names`` by computing
    each one's plottable series (registry, with a generic df.ta fallback), plus
    optional MA overlays. This is *a* preset — callers may supply any dict."""
    out: dict[str, dict] = {}
    close = df["Close"]
    ma_colors = {20: "#2563eb", 60: "#f59e0b", 120: "#94a3b8"}
    if include_ma:
        for w in mas:
            if len(df) >= 2:
                out[f"MA{w}"] = {"type": "MA", "data": close.rolling(w).mean(),
                                 "color": ma_colors.get(w, "#94a3b8")}
    for name in names:
        label = _LABELS.get(name.lower(), name.upper())
        builder = _REGISTRY.get(name.lower())
        try:
            entry = builder(df) if builder else _generic_entry(df, name)
        except Exception:
            entry = None
        if entry is not None:
            out[label] = entry

    # TTM Squeeze is "BB inside KC"; when a squeeze panel is shown, overlay the
    # Bollinger Bands + Keltner Channels on price so the squeeze is visible there.
    if any(n.lower() == "squeeze" for n in names):
        try:
            bb = df.ta.bbands(length=20, std=2.0)
            kc = df.ta.kc(length=20, scalar=1.5)
            if bb is not None and not bb.empty:
                bc = list(bb.columns)            # BBL, BBM, BBU, ...
                out["Bollinger"] = {"type": "bands", "lower": bb[bc[0]],
                                    "mid": bb[bc[1]], "upper": bb[bc[2]],
                                    "color": "#2563eb", "fill_alpha": 0.06}
            if kc is not None and not kc.empty:
                kcc = list(kc.columns)           # KCLe, KCBe, KCUe
                out["Keltner"] = {"type": "bands", "lower": kc[kcc[0]],
                                  "upper": kc[kcc[2]], "color": "#ea580c",
                                  "style": "--"}
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# dict -> overlays + panels  (the translator; no indicator is special-cased)
# --------------------------------------------------------------------------- #
def _squeeze_panel(label: str, cfg: dict, index) -> Panel:
    """TTM Squeeze: 4-color momentum histogram + red/green squeeze dots on zero."""
    panel = Panel(name=label)
    mom = _align(cfg["momentum"], index)
    colors: list[str] = []
    for i, v in enumerate(mom):
        if np.isnan(v):
            colors.append(_SQZ_POS_DN)
            continue
        rising = i > 0 and not np.isnan(mom[i - 1]) and v > mom[i - 1]
        if v >= 0:
            colors.append(_SQZ_POS_UP if rising else _SQZ_POS_DN)
        else:
            colors.append(_SQZ_NEG_UP if rising else _SQZ_NEG_DN)
    panel.hist = ("Momentum", mom)
    panel.hist_colors = colors
    panel.hlines = [HLine(0.0, "#cbd5e1", "-", 0.7)]
    on = _align(cfg["on"], index) if cfg.get("on") is not None else None
    if on is not None:
        panel.dots = [(i, _SQZ_DOT_ON if (not np.isnan(on[i]) and on[i] >= 1)
                       else _SQZ_DOT_OFF) for i in range(len(index))]
    return panel


def _build_panel(label: str, cfg: dict, index, color_idx: int) -> Optional[Panel]:
    t = cfg["type"]
    panel = Panel(name=label)
    if t == "squeeze":
        return _squeeze_panel(label, cfg, index)
    if t == "MACD":
        panel.lines = [
            Line("MACD", _align(cfg["macd"], index), cfg.get("color", "#2563eb")),
            Line("Signal", _align(cfg["signal"], index), cfg.get("signal_color", "#d97706"), 1.0),
        ]
        if cfg.get("histogram") is not None:
            panel.hist = ("Hist", _align(cfg["histogram"], index))
        panel.hlines = [HLine(0.0, "#cbd5e1", "-", 0.7)]
    elif t == "RSI":
        panel.lines = [Line(label, _align(cfg["data"], index), cfg.get("color", "#7c3aed"))]
        panel.hlines = [HLine(float(y), "#cbd5e1") for y in cfg.get("levels", [30, 50, 70])]
        panel.ylim = cfg.get("ylim", (0, 100))
    elif t == "multi":
        i = 0
        for sub, sc in cfg.items():
            if sub in ("type", "levels", "ylim") or not isinstance(sc, dict):
                continue
            panel.lines.append(Line(sub, _align(sc["data"], index),
                                    sc.get("color", _PALETTE[i % len(_PALETTE)]),
                                    sc.get("width", 1.0)))
            i += 1
        panel.hlines = [HLine(float(y), "#cbd5e1") for y in cfg.get("levels", [])]
        if cfg.get("ylim"):
            panel.ylim = cfg["ylim"]
    elif t == "Crossover":
        panel.lines = [
            Line(cfg.get("label1", "line1"), _align(cfg["line1"], index),
                 cfg.get("color1", "#2563eb")),
            Line(cfg.get("label2", "line2"), _align(cfg["line2"], index),
                 cfg.get("color2", "#d97706")),
        ]
        panel.hlines = [HLine(0.0, "#cbd5e1", "-", 0.7)]
    elif t == "threshold":
        panel.lines = [Line(label, _align(cfg["data"], index),
                            cfg.get("color", _PALETTE[color_idx % len(_PALETTE)]))]
        panel.hlines = [HLine(float(y), "#cbd5e1") for y in cfg.get("levels", [])]
    else:  # "below" generic line (also the fallback for unknown below-types)
        panel.lines = [Line(label, _align(cfg["data"], index),
                            cfg.get("color", _PALETTE[color_idx % len(_PALETTE)]))]
        panel.hlines = [HLine(float(y), "#cbd5e1") for y in cfg.get("levels", [])]
    return panel if (panel.lines or panel.hist) else None


def _attach_state(panel: Panel) -> None:
    """Compute the deterministic 2-D state for a zero-centered single-line panel
    (EFI, CMF, …) and store it on `panel.chip` / `panel.state`. No-op for
    multi-line or non-zero-level panels. Must run on the FULL (pre-trim) series
    so the robust scale/lookback has history. Never raises."""
    if len(panel.lines) != 1 or not panel.hlines:
        return
    levels = [h.y for h in panel.hlines]
    if 0.0 not in levels:                      # zero-centered oscillators only
        return
    try:
        from .state import panel_state
        st = panel_state(panel.lines[0].values, level=0.0)
    except Exception:
        st = None
    if st:
        panel.state = st
        panel.chip = st["label"]


def _spec_from_dict(
    full: pd.DataFrame,
    indicators: dict,
    *,
    over_cap: int,
    below_cap: int,
) -> tuple[list[Line], list[tuple[int, float, str]], list[Flag], list[Panel], dict[str, list[Flag]]]:
    """Translate the generic indicators dict into overlays/panels/flags on the
    FULL frame (pre-trim). Returns (overlays, swings, price_flags, panels,
    panel_flags) where panel_flags maps a panel label -> flags to attach."""
    index = full.index
    overlays: list[Line] = []
    bands: list[Band] = []
    swings: list[tuple[int, float, str]] = []
    price_flags: list[Flag] = []
    panels: list[Panel] = []
    panel_flags: dict[str, list[Flag]] = {}
    n_over = n_below = 0

    for ci, (label, cfg) in enumerate(indicators.items()):
        if not isinstance(cfg, dict) or "type" not in cfg:
            continue
        t = cfg["type"]
        placement = "over" if t in _OVER_TYPES else "below"

        if placement == "over":
            if t == "flags":
                bull = _bool_positions(cfg.get("bull"), index)
                bear = _bool_positions(cfg.get("bear"), index)
                target = cfg.get("target", "price")
                fl = [Flag(i, "bull", label) for i in bull] + \
                     [Flag(i, "bear", label) for i in bear]
                if target == "price":
                    price_flags.extend(fl)
                else:
                    panel_flags.setdefault(target, []).extend(fl)
                continue
            if t == "bands":
                bands.append(Band(
                    label, _align(cfg["upper"], index), _align(cfg["lower"], index),
                    cfg.get("color", _PALETTE[ci % len(_PALETTE)]),
                    mid=_align(cfg["mid"], index) if cfg.get("mid") is not None else None,
                    style=cfg.get("style", "-"),
                    fill_alpha=cfg.get("fill_alpha", 0.0),
                    width=cfg.get("width", 0.9)))
                continue
            if n_over >= over_cap:
                continue
            if t == "Swings":
                swings = _swing_anchors(full)
            else:  # "over" / "MA"
                overlays.append(Line(label, _align(cfg["data"], index),
                                     cfg.get("color", _PALETTE[ci % len(_PALETTE)]),
                                     cfg.get("width", 1.1)))
            n_over += 1
        else:
            if n_below >= below_cap:
                continue
            p = _build_panel(label, cfg, index, ci)
            if p is not None:
                panels.append(p)
                n_below += 1

    return overlays, bands, swings, price_flags, panels, panel_flags


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def _coerce_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise ValueError("empty price frame")
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    missing = [c for c in _OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    return df


def build_spec(
    symbol_or_df,
    profile: str = "committee",
    *,
    indicators: "dict | Sequence[str] | None" = None,
    window: int = 60,
    history_days: int = 400,
    over_cap: int = 10,
    below_cap: int = 10,
    auto_divergence: bool = True,
    symbol: Optional[str] = None,
) -> ChartSpec:
    """
    Build a backend-agnostic ChartSpec.

    ``indicators`` may be:
      * None — use the default preset (MA + MACD/RSI/KDJ);
      * a sequence of names (e.g. ["RSI","MACD"]) — default preset for those;
      * a full autoplot-style dict — rendered exactly as described (see module
        docstring). When a dict is supplied, ``auto_divergence`` is off unless
        the dict itself includes "flags" entries.
    """
    if isinstance(symbol_or_df, pd.DataFrame):
        full = _coerce_ohlc(symbol_or_df)
        sym = symbol or "—"
    else:
        from ..data import load_or_download_stock_data
        sym = symbol or str(symbol_or_df)
        end = datetime.now()
        start = end - timedelta(days=history_days)
        full = _coerce_ohlc(load_or_download_stock_data(symbol_or_df, start, end))

    # resolve the indicators dict
    user_supplied_dict = isinstance(indicators, dict)
    if indicators is None:
        # default preset = the *profile's own* strategy set (dynamic per profile),
        # so profile=swing draws squeeze/kdj/fisher/efi/vidya, committee draws
        # trix/kst/rsi/cmf/er, etc. Falls back to MACD/RSI/KDJ on any failure.
        try:
            from ..profiles import PROFILES, resolve_profile
            preset_names = tuple(PROFILES[resolve_profile(profile)]["strategies"])
        except Exception:
            preset_names = DEFAULT_INDICATORS
        ind_dict = default_indicator_dict(full, preset_names)
    elif user_supplied_dict:
        ind_dict = indicators
        preset_names = ()
    else:  # sequence of names
        preset_names = tuple(indicators)
        ind_dict = default_indicator_dict(full, preset_names)

    # verdicts (best-effort)
    verdicts: dict[str, str] = {}
    composite: Optional[str] = None
    try:
        from ..profiles import profile_signals
        # Chart shows the live/last bar, so score it too (the chip already reads
        # the plotted series); the agent/committee paths use confirmed_only=True.
        res = profile_signals(full, profile, confirmed_only=False)
        verdicts = res.get("signals", {}) or {}
        composite = res.get("composite")
    except Exception:
        pass

    overlays, bands, swings_full, price_flags_full, panels, panel_flags = _spec_from_dict(
        full, ind_dict, over_cap=over_cap, below_cap=below_cap)

    # committee divergence — auto only for the default/name presets
    if auto_divergence and not user_supplied_dict:
        strat_div = _strategy_divergence(full, preset_names or DEFAULT_INDICATORS)
        for k, fl in strat_div.items():
            panel_flags.setdefault(k, []).extend(fl)
        seen: set[tuple[int, str]] = set()
        for fl in strat_div.values():
            for f in fl:
                if (f.x, f.kind) not in seen:
                    seen.add((f.x, f.kind))
                    price_flags_full.append(f)

    # attach panel verdicts + panel-targeted flags
    for p in panels:
        p.verdict = verdicts.get(p.name.lower()) or verdicts.get(p.name.upper())
        p.flags = panel_flags.get(p.name, []) + panel_flags.get(p.name.upper(), [])

    # ----- trailing-window trim -----
    n_full = len(full)
    keep = min(window, n_full)
    s = n_full - keep
    df_win = full.iloc[s:].copy()

    def _tv(v):
        return np.asarray(v, dtype=float)[s:]

    def _sf(flags):
        return [Flag(f.x - s, f.kind, f.label) for f in flags if f.x >= s]

    overlays = [Line(o.label, _tv(o.values), o.color, o.width) for o in overlays]
    bands = [Band(b.label, _tv(b.upper), _tv(b.lower), b.color,
                  mid=_tv(b.mid) if b.mid is not None else None,
                  style=b.style, fill_alpha=b.fill_alpha, width=b.width)
             for b in bands]
    for p in panels:
        _attach_state(p)                       # on FULL series, before trim
        p.lines = [Line(l.label, _tv(l.values), l.color, l.width) for l in p.lines]
        if p.hist is not None:
            p.hist = (p.hist[0], _tv(p.hist[1]))
        if p.hist_colors is not None:
            p.hist_colors = p.hist_colors[s:]
        if p.dots:
            p.dots = [(x - s, c) for (x, c) in p.dots if x >= s]
        p.flags = _sf(p.flags)
    swings = [(x - s, y, t) for (x, y, t) in swings_full if x >= s]
    price_flags = _sf(price_flags_full)

    # geometric divergence on price (price vs RSI) — for the auto presets
    price_markers: list[Marker] = []
    if auto_divergence and not user_supplied_dict:
        try:
            rsi_full = full.ta.rsi()
            price_full = np.asarray(full["Close"].values, dtype=float)
            if rsi_full is not None and len(rsi_full):
                for m in divergence_markers(price_full, np.nan_to_num(rsi_full.values),
                                            lookback=window):
                    if m.x0 >= s or m.x1 >= s:
                        price_markers.append(
                            Marker(max(m.x0 - s, 0), float(price_full[m.x0]),
                                   max(m.x1 - s, 0), float(price_full[m.x1]),
                                   m.kind, m.label))
        except Exception:
            pass

    asof = (str(df_win.index[-1].date()) if hasattr(df_win.index[-1], "date")
            else str(df_win.index[-1]))

    return ChartSpec(
        symbol=sym, df=df_win, price_bands=bands, price_overlays=overlays,
        price_markers=price_markers,
        price_flags=price_flags, swings=swings, panels=panels, verdicts=verdicts,
        composite=composite, profile=profile, asof=asof, n=keep,
        states={p.name: p.state for p in panels if p.state},
    )


def indicator_states(symbol_or_df, profile: str = "committee", **kw) -> dict[str, dict]:
    """Deterministic 2-D oscillator states (see state.py) for *profile*.

    Single source of truth with the rendered chart: a thin wrapper over
    ``build_spec`` returning its ``.states``, so the text/LLM path quotes the
    EXACT chips drawn on the panel — no parallel computation, no drift. Returns
    ``{PANEL_NAME: state dict}`` (empty on any failure; never raises)."""
    try:
        return build_spec(symbol_or_df, profile, **kw).states
    except Exception:
        return {}
