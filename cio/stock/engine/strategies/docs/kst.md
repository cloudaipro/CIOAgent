# KST — Know Sure Thing

**Engine key**: `kst` · **Module**: `kst_strategy.py` · **Inputs**: Close

## Definition

Martin Pring's KST sums four smoothed rate-of-change readings with rising weights:
`KST = 1×SMA(ROC10) + 2×SMA(ROC15) + 3×SMA(ROC20) + 4×SMA(ROC30)` (daily
parameters), plus a 9-period signal line. It captures momentum across four
horizons in one curve, weighted toward the longer cycles.

## Interpretation

- **KST crossing its signal line** — the composite momentum cycle is turning.
- **KST above zero** — multi-horizon momentum net positive.
- Divergence against price warns of cycle exhaustion.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_KST_CROSSOVER_BULL` / `_BEAR` | event | KST crossed its signal line up / down |
| `f_KST_CROSSOVER_CSLS` | count | signed bars since the cross |
| `c_KST_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs KST-signal swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `roc1..roc4` | 10, 15, 20, 30 | — | ROC windows (daily defaults) |
| `sma1..sma4` | 10, 10, 10, 15 | — | smoothing per ROC |
| `signal` | 9 | 5, 9, 14 | signal-line SMA |
| `ema_length` | 9 | — | divergence price-EMA |

Weekly variant: ROC 10/13/15/20, SMA 10/13/15/20. Monthly: ROC 9/12/18/24, SMA 6/6/6/9.
Default signal: `c_KST_CROSSOVER_BULL`.

## How to use in stock investing

- Pring designed KST for **major cycle turns** — it suits position investors
  re-weighting on weekly/monthly horizons more than day-to-day timing.
- Buy `c_KST_CROSSOVER_BULL` when it occurs below zero (early-cycle), hold while
  KST stays above its signal; the same cross far above zero is late-cycle and
  better treated as a hold confirmation than a fresh entry.
- `f_KST_CROSSOVER_CSLS` shows how mature the current swing is — useful for
  position-sizing decisions in committee reviews.

## Example

```python
import cio.stock as s

sig = s.run_strategy("SPY", "kst")
t = sig.iloc[-1]
if t["c_KST_CROSSOVER_BULL"] == 1:
    print("KST composite momentum turned up")
print("bars since cross:", t["f_KST_CROSSOVER_CSLS"])
```

## References

- https://www.tradingview.com/support/solutions/43000502329-know-sure-thing-kst/
- Martin Pring, *Technical Analysis Explained*
