# TSI — True Strength Index

**Engine key**: `tsi` · **Module**: `tsi_strategy.py` · **Inputs**: Close

## Definition

`TSI = 100 × EMA(EMA(ΔC, slow), fast) / EMA(EMA(|ΔC|, slow), fast)` — momentum
double-smoothed and normalized by double-smoothed absolute momentum. Range −100 to
+100; a signal line (EMA of TSI) completes the system.

## Interpretation

- **Sign** — positive TSI = buyers in control on the smoothed horizon.
- **TSI vs signal line** — short-term momentum turning within the larger swing.
- **±limit_delta zones (default ±50)** — stretched conditions.
- Double smoothing makes TSI one of the cleaner momentum lines — crossovers are
  rarer and more deliberate than MACD's.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_TSI_CROSSOVER_BULL` | event | TSI crossed above its signal line **while TSI > 0** (with-trend buy) |
| `c_TSI_CROSSOVER_BEAR` | event | TSI crossed below signal **while TSI < 0** (with-trend sell) |
| `c_TSI_OVERBOUGHT` / `c_TSI_OVERSOLD` | state | TSI beyond ±limit_delta |
| `f_TSI_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_TSI_OVERBOUGHT_BULL` / `_BEAR`, `c_TSI_OVERSOLD_BULL` / `_BEAR` | event | zone-edge crossings |
| `c_TSI_CENTRALLINE_BULL` / `_BEAR` | event | zero-line cross up / down |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 13 | 5, 9, 13 | second smoothing EMA |
| `slow` | 25 | 10, 18, 25 | first smoothing EMA |
| `signal` | 13 | 3, 7, 9, 13 | signal-line EMA |
| `limit_delta` | 50 | 20, 30, 50, 60 | zone threshold |
| `drift` | 1 | — | difference period |

Grid constrained to `slow > fast > signal`. Default signal: `c_TSI_CROSSOVER_BULL`.

## How to use in stock investing

- The built-in sign filter matters: `c_TSI_CROSSOVER_BULL` only fires when TSI is
  already positive — it is a *continuation* entry (buy momentum re-acceleration in
  an uptrend), not a bottom-picker.
- For reversal hunting use `c_TSI_CENTRALLINE_BULL` (zero cross) instead.
- TSI divergence with price at zone extremes is a strong exhaustion tell; compare
  `f_TSI_OVERBOUGHTSOLD_CSLS` age against prior visits.

## Example

```python
import cio.stock as s

sig = s.run_strategy("CRM", "tsi")
t = sig.iloc[-1]
if t["c_TSI_CROSSOVER_BULL"] == 1:
    print("TSI re-accelerating within positive territory — continuation long")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:true_strength_index
- https://phemex.com/academy/what-is-smi-ergodic-indicator
- William Blau, *Momentum, Direction, and Divergence* (1995)
