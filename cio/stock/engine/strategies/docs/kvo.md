# KVO — Klinger Volume Oscillator

**Engine key**: `kvo` · **Module**: `kvo_strategy.py` · **Inputs**: High, Low, Close, Volume

## Definition

Stephen Klinger's oscillator converts volume into "volume force" (signed by
whether the typical price rose or fell, scaled by range position), then takes
`EMA(VF, fast) − EMA(VF, slow)` with a signal line. Long-period defaults (34/55/13)
target the *long-term flow of money* while staying responsive at turns.

## Interpretation

- **KVO above its signal line** — volume force building upward.
- This strategy trades **swing structure in the KVO−signal spread**: a confirmed
  support swing in the spread while price is above its SMA = bullish; resistance
  swing below the SMA = bearish.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_KVO_CSLS` | count | signed bars since the KVO−signal spread's swing flipped |
| `c_KVO_CROSSOVER_BULL` | event | spread formed support (swing low) `cls_limit` bars ago **and** price above its SMA |
| `c_KVO_CROSSOVER_BEAR` | event | spread formed resistance **and** price below its SMA |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 34 | 34, 8, 12 | fast EMA |
| `slow` | 55 | 55, 21, 26 | slow EMA |
| `signal` | 13 | 13, 5, 9 | signal EMA; also the price-SMA window |
| `cls_limit` | 4 | 3, 4, 5 | required swing age for confirmation |

Grid restricted to coherent triples (34/55/13, 8/21/5, 12/26/9).
Default signal: `c_KVO_CROSSOVER_BULL`.

## How to use in stock investing

- **With-trend volume timing**: the price-above-SMA gate means the bull signal
  buys volume-flow turns *inside* an uptrend — Klinger's own guidance was to
  trade KVO only in the direction of the prevailing trend.
- `f_KVO_CSLS` is a maturity gauge for the current flow swing; small absolute
  values = fresh turn.
- Klinger divergence (price new high, KVO lower high) is a strong warning —
  visible here as the bear signal firing soon after new price highs.

## Example

```python
import cio.stock as s

sig = s.run_strategy("XLE", "kvo")
if sig.iloc[-1]["c_KVO_CROSSOVER_BULL"] == 1:
    print("Volume force swing-low confirmed within uptrend")
```

## References

- https://www.daytrading.com/klinger-volume-oscillator
- Stephen Klinger, "Identifying Trends with Volume Analysis", *Stocks & Commodities* (1997)
