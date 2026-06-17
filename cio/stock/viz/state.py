"""
Deterministic 2-D state for a level-centered oscillator panel.

WHY THIS EXISTS
---------------
A panel used to print one overloaded word ("neutral") computed from a
window-net vote of _BULL/_BEAR crossings. "neutral" reads as "flat / weak",
so a downstream LLM narrating the chart confabulated direction ("EFI 未明確
翻正 / 反彈缺力") that contradicted the plotted line (EFI had clearly crossed
>0 for ~3 bars). Root cause: a single token collapsed two orthogonal facts —
*level sign* and *direction* — and the LLM filled the gap with a guess.

Research backs the design (do NOT hand raw numbers/slope to the LLM and hope):
  * LLMs read raw numeric series poorly — tokenization, lost-in-the-middle,
    coarse pattern recognition (arXiv 2502.01477, 2510.01111).
  * Removing the LLM from TS forecasters doesn't hurt — they don't model the
    sequence (NeurIPS 2024, arXiv 2406.16964).
  * LLMs add value as narrators over PRE-COMPUTED discretized signals, not as
    number crunchers (Frontiers/PMC survey; arXiv 2506.16813).

So: compute the stats deterministically here (zero LLM cost), emit ONE
unambiguous categorical state + provenance. The narrator quotes it; it never
re-derives direction.

ESTIMATORS (robust, not 2-point slope)
  * level   : sign of (last - level), with a dead-band in robust (MAD) units
              so a value sitting on the line reads "near-zero", not pos/neg.
  * trend   : Theil-Sen slope (median of pairwise slopes — barely moved by the
              volume-spike outliers EFI is full of) over a short window, gated
              by a magnitude-vs-noise band (hysteresis against whipsaw).
              Kendall tau / p reported as provenance (Mann-Kendall proxy).
  * scale   : MAD over a lookback (robust, scale-free across volume regimes).

Generic on (series, level); the caller decides which panels get a state.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

_MAD_K = 1.4826  # MAD -> sigma for normal data


def _scale(dev: np.ndarray) -> float:
    """Robust scale of deviations: MAD, falling back to std, then 1.0."""
    if dev.size == 0:
        return 1.0
    mad = float(np.median(np.abs(dev - np.median(dev)))) * _MAD_K
    if mad > 0:
        return mad
    sd = float(np.std(dev))
    return sd if sd > 0 else 1.0


def _theil_sen(y: np.ndarray) -> tuple[float, float, float]:
    """(slope_per_bar, kendall_tau, kendall_p). Robust; safe on tiny n."""
    n = y.size
    if n < 3:
        return 0.0, 0.0, 1.0
    x = np.arange(n, dtype=float)
    try:
        from scipy.stats import theilslopes, kendalltau
        slope = float(theilslopes(y, x)[0])
        tau, p = kendalltau(x, y)
        tau = 0.0 if tau != tau else float(tau)
        p = 1.0 if (p != p) else float(p)
        return slope, tau, p
    except Exception:
        # last-resort OLS slope; no significance
        slope = float(np.polyfit(x, y, 1)[0])
        return slope, 0.0, 1.0


_WORD = {
    ("pos", "up"): "positive·building",
    ("pos", "down"): "positive·fading",
    ("pos", "flat"): "positive·steady",
    ("neg", "up"): "negative·recovering",
    ("neg", "down"): "negative·deepening",
    ("neg", "flat"): "negative·steady",
    ("zero", "up"): "near-zero·rising",
    ("zero", "down"): "near-zero·falling",
    ("zero", "flat"): "neutral·flat",
}


def panel_state(
    values,
    *,
    level: float = 0.0,
    lookback: int = 252,
    trend_k: int = 7,
    level_band: float = 0.5,
    dir_band: float = 0.5,
    cross_note_max: int = 10,
) -> Optional[dict[str, Any]]:
    """Compute the 2-D state of a level-centered series.

    Returns a dict (see module docstring) or None if there isn't enough clean
    data. `level_band`/`dir_band` are in robust (MAD) units — raise them to
    de-sensitise, lower to react sooner.
    """
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if v.size < max(trend_k, 5):
        return None

    look = v[-lookback:] if v.size > lookback else v
    dev_look = look - level
    level_scale = _scale(dev_look)            # dispersion of the LEVEL (for dead-band)
    noise_scale = _scale(np.diff(look))       # per-bar NOISE (for the trend gate)

    last = float(v[-1])
    z = (last - level) / level_scale

    # level with dead-band (hysteresis around the line)
    if z > level_band:
        lvl = "pos"
    elif z < -level_band:
        lvl = "neg"
    else:
        lvl = "zero"

    # Robust trend over the last trend_k bars (Theil-Sen), gated against the
    # random-walk band: a drift counts only if the modeled net change exceeds
    # dir_band * (per-bar noise) * sqrt(window). Comparing the short-window drift
    # to PER-BAR NOISE (not level dispersion) avoids a steady trend inflating its
    # own threshold and reading as "flat".
    win = v[-trend_k:]
    slope, tau, mk_p = _theil_sen(win)
    net = slope * (trend_k - 1)               # Theil-Sen modeled change across window
    gate = dir_band * noise_scale * np.sqrt(max(trend_k - 1, 1))
    if net > gate:
        direction = "up"
    elif net < -gate:
        direction = "down"
    else:
        direction = "flat"

    # magnitude percentile of |last-level| within the lookback
    mag_pctile = float(100.0 * np.mean(np.abs(dev_look) <= abs(last - level)))

    # bars since last sign-vs-level cross
    sign_last = 1 if last >= level else -1
    signs = np.where((v - level) >= 0, 1, -1)
    bars_since_cross: Optional[int] = None
    cross_dir: Optional[str] = None
    for i in range(v.size - 2, -1, -1):
        if signs[i] != sign_last:
            bars_since_cross = (v.size - 1) - i
            cross_dir = "up" if sign_last > 0 else "down"
            break

    # extreme since the cross (in the current-sign direction)
    peak: Optional[float] = None
    peak_bars_ago: Optional[int] = None
    if bars_since_cross is not None:
        seg = v[-bars_since_cross:] if bars_since_cross > 0 else v[-1:]
        idx = int(np.argmax(seg * sign_last))      # furthest from level
        peak = float(seg[idx])
        peak_bars_ago = (len(seg) - 1) - idx

    label = _WORD[(lvl, direction)]
    if bars_since_cross is not None and bars_since_cross <= cross_note_max:
        side = ">" if cross_dir == "up" else "<"
        label = f"{label} ({side}{level:g} {bars_since_cross}d)"

    return {
        "level": lvl,
        "direction": direction,
        "z": round(z, 2),
        "mag_pctile": round(mag_pctile, 0),
        "slope": slope,
        "tau": round(tau, 2),
        "mk_p": round(mk_p, 3),
        "bars_since_cross": bars_since_cross,
        "cross_dir": cross_dir,
        "peak": peak,
        "peak_bars_ago": peak_bars_ago,
        "label": label,
        "color_key": lvl,
    }
