# PGO — Pretty Good Oscillator

**Engine key**: `pgo` · **Module**: `pgo_strategy.py` · **Inputs**: High, Low, Close

## Definition

Mark Johnson's PGO: `PGO = (Close − SMA(Close, length)) / EMA(TrueRange, length)` —
price's distance from its average, **measured in ATR units**. A PGO of 3 means
price sits three average-true-ranges above its mean: a volatility-adjusted
breakout measure.

## Interpretation

- **PGO crossing above +limit (default 3)** — price escaped its normal volatility
  envelope upward: breakout long.
- **PGO crossing below −limit** — breakout short / breakdown.
- Johnson designed it for longer-term trades: enter on ±3 crossings, exit at zero.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_PGO_CROSSOVER_BULL` | event | PGO crossed up through +limit |
| `c_PGO_CROSSOVER_BEAR` | event | PGO crossed down through −limit |
| `c_PGO_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs PGO-EMA swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 13 | 5, 9, 13, 20 | SMA/ATR window |
| `limit` | 3 | 1.5, 2, 2.5, 3 | breakout threshold in ATR units |
| `ema_length` | 9 | — | divergence smoothing |

Default signal: `c_PGO_CROSSOVER_BULL`.

## How to use in stock investing

- **Volatility-adjusted breakout entry**: because the threshold is in ATR units,
  the same `limit` works across quiet and volatile stocks — good for systematic
  screens over a heterogeneous watchlist.
- Johnson's exit: close the position when PGO returns to zero (price back at its
  mean). Track the raw PGO via a custom run if you need the exit level.
- Lower `limit` (1.5–2) → swing-trading frequency; 3 → rare, high-conviction
  position entries.

## Example

```python
import cio.stock as s

sig = s.run_strategy("SHOP", "pgo", length=13, limit=3)
if sig.iloc[-1]["c_PGO_CROSSOVER_BULL"] == 1:
    print("Price 3 ATRs above its mean — volatility breakout")
```

## References

- https://www.marketinout.com/stock-screener/industry.php?picker=pgo
- Mark Johnson, Pretty Good Oscillator (TradeStation forums origin)
