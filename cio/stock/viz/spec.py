"""
Shared indicator-visualization core — the single source of truth that both the
matplotlib (PNG) and bokeh (HTML) adapters consume. Nothing here draws; it only
computes a backend-agnostic ``ChartSpec``.

Pipeline:
  OHLC df  ->  raw indicator series (pandas_ta)  ->  divergence + swing markers
           ->  profile verdicts (cio.stock.profiles)  ->  ChartSpec

Indicator series are recomputed here because the strategy modules in
cio/stock/engine/strategies only emit c_*/f_* signal flags, not the plottable
lines (macd/signal/hist, K/D/J, rsi). Recomputing with the same pandas_ta
defaults keeps the picture consistent with the signal layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
import pandas_ta  # noqa: F401  registers the df.ta accessor used by the builders

# Default indicators = exactly what the user is sent to TradingView for in
# conv_turns#210: RSI / MACD / KDJ.
DEFAULT_INDICATORS: tuple[str, ...] = ("MACD", "RSI", "KDJ")
_OHLC = ("Open", "High", "Low", "Close")


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
    """A single-bar event flag (e.g. committee divergence signal)."""
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
class HLine:
    y: float
    color: str
    style: str = "--"
    width: float = 0.7


@dataclass
class Panel:
    """One sub-chart below the price panel (MACD / RSI / KDJ / ...)."""
    name: str
    lines: list[Line] = field(default_factory=list)
    hist: Optional[tuple[str, np.ndarray]] = None   # (label, values) signed bars
    hlines: list[HLine] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)  # committee divergence flags
    ylim: Optional[tuple[float, float]] = None
    verdict: Optional[str] = None                    # bull/bear/neutral


@dataclass
class ChartSpec:
    symbol: str
    df: pd.DataFrame
    ma: dict[str, np.ndarray]                # label -> values (price overlays)
    price_markers: list[Marker]              # geometric divergence on price panel
    price_flags: list[Flag]                  # committee divergence flags on price
    swings: list[tuple[int, float, str]]     # (x, y, "HH"/"HL"/"LH"/"LL")
    panels: list[Panel]
    verdicts: dict[str, str]                 # strategy -> verdict
    composite: Optional[str]
    profile: str
    asof: str
    n: int

    @property
    def x(self) -> np.ndarray:
        return np.arange(self.n)


# --------------------------------------------------------------------------- #
# divergence — deterministic, scipy-free
# --------------------------------------------------------------------------- #
def _pivots(values: np.ndarray, left: int = 2, right: int = 2) -> tuple[list[int], list[int]]:
    """Indices of local maxima and minima (strict within a +/- window)."""
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
    """
    Detect regular bullish/bearish divergence between price and an oscillator
    over the trailing ``lookback`` bars.

    Bearish: price makes a higher high while the indicator makes a lower high.
    Bullish: price makes a lower low while the indicator makes a higher low.

    Returns markers anchored on the *indicator* axis (x positions are shared
    with the price axis, so the caller may re-project onto price if desired).
    """
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

    # bearish: last two confirmed price highs that also have indicator pivots
    if len(p_hi) >= 2 and len(i_hi) >= 2:
        a, b = p_hi[-2], p_hi[-1]
        ia = min(i_hi, key=lambda k: abs(k - a))
        ib = min(i_hi, key=lambda k: abs(k - b))
        if ia != ib and p[b] > p[a] and ind[ib] < ind[ia]:
            out.append(Marker(ia, float(ind[ia]), ib, float(ind[ib]),
                              "bear", "bear div"))

    # bullish: last two confirmed price lows
    if len(p_lo) >= 2 and len(i_lo) >= 2:
        a, b = p_lo[-2], p_lo[-1]
        ia = min(i_lo, key=lambda k: abs(k - a))
        ib = min(i_lo, key=lambda k: abs(k - b))
        if ia != ib and p[b] < p[a] and ind[ib] > ind[ia]:
            out.append(Marker(ia, float(ind[ia]), ib, float(ind[ib]),
                              "bull", "bull div"))
    return out


# --------------------------------------------------------------------------- #
# swing / structure anchors
# --------------------------------------------------------------------------- #
def _swing_anchors(
    df: pd.DataFrame,
    lookback: int = 60,
    left: int = 3,
    right: int = 3,
    keep: int = 4,
) -> list[tuple[int, float, str]]:
    """
    Recent swing-high/low pivots for structure context, classified HH/LH/HL/LL.

    Uses the same deterministic local-extrema detector as the divergence layer
    (rather than classify_swings' forward-filled level columns, whose fill
    semantics make per-bar flagging error-prone). Capped to the last ``keep``
    highs and lows in the window to keep the chart readable.
    """
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
# indicator panel builders
# --------------------------------------------------------------------------- #
def _macd_panel(df: pd.DataFrame, price: np.ndarray, lookback: int) -> Optional[Panel]:
    from .style import LINE
    m = df.ta.macd()
    if m is None or m.empty:
        return None
    cols = list(m.columns)
    macd = m[cols[0]].values
    hist = m[cols[1]].values
    sig = m[cols[2]].values
    panel = Panel(
        name="MACD",
        lines=[Line("MACD", macd, LINE["macd"]),
               Line("Signal", sig, LINE["signal"], 1.0)],
        hist=("Hist", hist),
        hlines=[HLine(0.0, "#cbd5e1", "-", 0.7)],
        markers=divergence_markers(price, np.nan_to_num(macd), lookback=lookback),
    )
    return panel


def _rsi_panel(df: pd.DataFrame, price: np.ndarray, lookback: int) -> Optional[Panel]:
    from .style import LINE
    r = df.ta.rsi()
    if r is None or len(r) == 0:
        return None
    rsi = r.values
    panel = Panel(
        name="RSI",
        lines=[Line("RSI 14", rsi, LINE["rsi"])],
        hlines=[HLine(70, "#cbd5e1"), HLine(30, "#cbd5e1"),
                HLine(50, "#e6e8ec", ":")],
        markers=divergence_markers(price, np.nan_to_num(rsi), lookback=lookback),
        ylim=(0, 100),
    )
    return panel


def _kdj_panel(df: pd.DataFrame, price: np.ndarray, lookback: int) -> Optional[Panel]:
    from .style import LINE
    k = df.ta.kdj()
    if k is None or k.empty:
        return None
    cols = list(k.columns)
    panel = Panel(
        name="KDJ",
        lines=[Line("K", k[cols[0]].values, LINE["k"]),
               Line("D", k[cols[1]].values, LINE["d"]),
               Line("J", k[cols[2]].values, LINE["j"], 0.9)],
        hlines=[HLine(80, "#cbd5e1"), HLine(20, "#cbd5e1")],
    )
    return panel


_BUILDERS = {"MACD": _macd_panel, "RSI": _rsi_panel, "KDJ": _kdj_panel}

# indicator name -> strategy key in the vendored signal engine
_STRAT_FOR = {"MACD": "macd", "RSI": "rsi", "KDJ": "kdj"}


def _strategy_divergence(full: pd.DataFrame, names: Sequence[str]) -> dict[str, list[Flag]]:
    """
    Read DIVERGENCE_BULL/BEAR flags from the same strategy signals the committee
    uses, so the chart's divergence markers match conv_turns#210's narrative.
    Returns {indicator_name: [Flag(positional_index, kind, label)]}. Best-effort.
    """
    out: dict[str, list[Flag]] = {}
    try:
        from .. import get_engine
        eng = get_engine()
    except Exception:
        return out
    pos_of = {ts: i for i, ts in enumerate(full.index)}
    for name in names:
        strat = _STRAT_FOR.get(name.upper())
        if strat is None:
            continue
        try:
            sig = eng.run(full, strat)
        except Exception:
            continue
        flags: list[Flag] = []
        for kind, col in (("bear", f"c_{strat.upper()}_DIVERGENCE_BEAR"),
                          ("bull", f"c_{strat.upper()}_DIVERGENCE_BULL")):
            if col not in sig.columns:
                continue
            fired = sig.index[sig[col].fillna(0).astype(bool)]
            for ts in fired:
                i = pos_of.get(ts)
                if i is not None:
                    flags.append(Flag(i, kind, f"{name} div"))
        if flags:
            out[name.upper()] = flags
    return out


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def _coerce_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Validate + normalize an OHLC(V) frame; raise on missing columns."""
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
    indicators: Sequence[str] = DEFAULT_INDICATORS,
    window: int = 60,
    history_days: int = 400,
    symbol: Optional[str] = None,
) -> ChartSpec:
    """
    Build a backend-agnostic ChartSpec for ``symbol_or_df``.

    ``symbol_or_df`` may be an OHLC(V) DataFrame or a ticker string. When a
    ticker is given, up to ``history_days`` of cached history is fetched and the
    chart shows the trailing ``window`` bars (plus warm-up for the indicators).
    """
    if isinstance(symbol_or_df, pd.DataFrame):
        full = _coerce_ohlc(symbol_or_df)
        sym = symbol or "—"
    else:
        from ..data import load_or_download_stock_data
        sym = symbol or str(symbol_or_df)
        end = datetime.now()
        start = end - timedelta(days=history_days)
        full = load_or_download_stock_data(symbol_or_df, start, end)
        full = _coerce_ohlc(full)

    # verdicts (best-effort — never let signal layer break the picture)
    verdicts: dict[str, str] = {}
    composite: Optional[str] = None
    try:
        from ..profiles import profile_signals
        res = profile_signals(full, profile)
        verdicts = res.get("signals", {}) or {}
        composite = res.get("composite")
    except Exception:
        pass

    # compute indicator panels on the FULL frame (warm-up), then trim to window
    price_full = np.asarray(full["Close"].values, dtype=float)
    panels: list[Panel] = []
    for name in indicators:
        builder = _BUILDERS.get(name.upper())
        if builder is None:
            continue
        try:
            p = builder(full, price_full, window)
        except Exception:
            p = None
        if p is not None:
            p.verdict = verdicts.get(name.lower()) or verdicts.get(name.upper())
            panels.append(p)

    swings_full = _swing_anchors(full, lookback=window)
    strat_div = _strategy_divergence(full, indicators)

    # trailing-window trim (keep last `window` bars for display)
    n_full = len(full)
    keep = min(window, n_full)
    s = n_full - keep
    df_win = full.iloc[s:].copy()

    def _trim_vals(v: np.ndarray) -> np.ndarray:
        return np.asarray(v, dtype=float)[s:]

    def _shift_marker(m: Marker) -> Optional[Marker]:
        if m.x0 < s and m.x1 < s:
            return None
        return Marker(max(m.x0 - s, 0), m.y0, max(m.x1 - s, 0), m.y1, m.kind, m.label)

    def _shift_flags(flags: list[Flag]) -> list[Flag]:
        return [Flag(f.x - s, f.kind, f.label) for f in flags if f.x >= s]

    for p in panels:
        p.lines = [Line(l.label, _trim_vals(l.values), l.color, l.width) for l in p.lines]
        if p.hist is not None:
            p.hist = (p.hist[0], _trim_vals(p.hist[1]))
        p.markers = [mm for mm in (_shift_marker(m) for m in p.markers) if mm is not None]
        p.flags = _shift_flags(strat_div.get(p.name.upper(), []))

    swings = [(x - s, y, t) for (x, y, t) in swings_full if x >= s]

    # committee divergence flags projected onto the price panel (dedup by bar+kind)
    price_flags: list[Flag] = []
    seen: set[tuple[int, str]] = set()
    for name, flags in strat_div.items():
        for f in _shift_flags(flags):
            key = (f.x, f.kind)
            if key in seen:
                continue
            seen.add(key)
            price_flags.append(f)

    # price MAs (computed on full, trimmed)
    ma: dict[str, np.ndarray] = {}
    close_full = full["Close"]
    for win_, label in ((20, "MA20"), (60, "MA60"), (120, "MA120")):
        if n_full >= 5:
            ma[label] = _trim_vals(close_full.rolling(win_).mean().values)

    # divergence on price panel too (price vs RSI is the canonical one)
    price_markers: list[Marker] = []
    try:
        rsi_full = full.ta.rsi()
        if rsi_full is not None and len(rsi_full):
            for m in divergence_markers(price_full, np.nan_to_num(rsi_full.values),
                                        lookback=window):
                sm = _shift_marker(Marker(m.x0, float(price_full[m.x0]),
                                          m.x1, float(price_full[m.x1]),
                                          m.kind, m.label))
                if sm is not None:
                    price_markers.append(sm)
    except Exception:
        pass

    asof = str(df_win.index[-1].date()) if hasattr(df_win.index[-1], "date") else str(df_win.index[-1])

    return ChartSpec(
        symbol=sym,
        df=df_win,
        ma=ma,
        price_markers=price_markers,
        price_flags=price_flags,
        swings=swings,
        panels=panels,
        verdicts=verdicts,
        composite=composite,
        profile=profile,
        asof=asof,
        n=keep,
    )
