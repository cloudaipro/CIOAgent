# PVT — Price Volume Trend

**Engine key**: `pvt` · **Module**: `pvt_strategy.py` · **Inputs**: Close, Volume

## Definition

`PVT = cumulative Σ(Volume × %ΔClose)` — like OBV, but each bar adds volume scaled
by the *percentage* price change rather than all-or-nothing. Gentle drifts add
little; big moves on big volume move PVT a lot. The strategy works on the
**PVT histogram** (PVT − SMA(PVT)) and its swing structure.

## Interpretation

- **PVT rising** — net money flowing in, proportional to conviction.
- **Histogram sign flips** — flow crossing its own average.
- A confirmed swing low (support) in the histogram = inflow resuming.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_PVT_TREND_CSLS` | count | signed bars since the histogram swing-trend flipped |
| `c_PVT_SUPPORT_BULL` | event | histogram formed a confirmed support swing 3 bars ago (inflow turn) |
| `c_PVT_SUPPORT_BEAR` | event | histogram formed a confirmed resistance swing 3 bars ago (outflow turn) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `drift` | 1 | 1–5 | %change lag |
| `sma_length` | 9 | 3, 5, 7, 9, 15, 20 | histogram baseline SMA |

Default signal: `c_PVT_SUPPORT_BULL`.

## How to use in stock investing

- **Flow-turn timing**: `c_PVT_SUPPORT_BULL` marks the bar where the volume-flow
  histogram's swing low is confirmed — use it to time entries in stocks already
  on an accumulation watchlist (e.g. from `adobv`).
- Divergence reading (manual): price lower low + PVT higher low = bullish — the
  proportional weighting makes PVT divergences more informative than OBV's.
- Swing confirmation takes 3 bars by construction — signals are deliberate, not
  same-bar reactive.

## Example

```python
import cio.stock as s

sig = s.run_strategy("ORCL", "pvt", sma_length=9)
if sig.iloc[-1]["c_PVT_SUPPORT_BULL"] == 1:
    print("PVT histogram swing-low confirmed — inflow resuming")
```

## References

- https://profitmart.in/knowledge-center/candlestick-patterns/what-is-a-volume-price-trend-indicator/
