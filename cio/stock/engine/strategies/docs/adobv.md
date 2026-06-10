# ADOBV — Accumulation/Distribution + OBV Divergence

**Engine key**: `adobv` · **Module**: `adobv_strategy.py` · **Inputs**: High, Low, Close, Volume

## Definition

A StockCharts screening pattern that requires **two independent volume-flow lines
to disagree with price simultaneously**. It computes the Accumulation/Distribution
line (intra-bar close location × volume, cumulative) and On-Balance Volume
(volume signed by close direction, cumulative), each with fast (20) and slow (65)
SMAs, plus the same SMAs on price.

## Interpretation

- **Bullish divergence**: price below both its SMAs (weak) while *both* A/D and
  OBV sit above their SMAs (volume flow strong) — accumulation hidden under a
  weak tape.
- **Bearish divergence**: price above its SMAs while both flow lines are below
  theirs — distribution into strength.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_ADOBV_DIVERGENCE_BULL` | state | price weak + A/D strong + OBV strong (both windows) |
| `c_ADOBV_DIVERGENCE_BEAR` | state | price strong + A/D weak + OBV weak (both windows) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 20 | 3, 5, 9, 20 | fast SMA window |
| `slow` | 65 | 9, 15, 21, 65 | slow SMA window |

Grid constrained to `fast < slow`. Default signal: `c_ADOBV_DIVERGENCE_BULL`.

## How to use in stock investing

- This is a **screening condition**, not an entry trigger: it persists while the
  divergence holds. Use it to build a watchlist of stocks under accumulation,
  then time entries with a faster signal (`macd`, `stoch`).
- Requiring both A/D and OBV (they weight volume differently) cuts false
  positives from single-line divergences.
- Bear side flags candidates to trim before technical breakdown becomes visible
  in price alone.

## Example

```python
import cio.stock as s

watchlist = ["F", "T", "PFE", "CSCO"]
for sym in watchlist:
    sig = s.run_strategy(sym, "adobv")
    if sig.iloc[-1]["c_ADOBV_DIVERGENCE_BULL"] == 1:
        print(sym, "— under accumulation while price weak")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:accumulation_distribution_line
- Joe Granville (OBV); Marc Chaikin (A/D line)
