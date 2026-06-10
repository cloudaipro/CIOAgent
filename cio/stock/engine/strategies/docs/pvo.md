# PVO — Percentage Volume Oscillator

**Engine key**: `pvo` · **Module**: `pvo_strategy.py` · **Inputs**: Close, Volume

## Definition

`PVO = 100 × (EMA(volume, fast) − EMA(volume, slow)) / EMA(volume, slow)` — the PPO
formula applied to **volume**. Positive PVO = short-term volume running above its
longer-term norm. This strategy also computes PPO on price to flag price-cross
events that occur *while volume expands*.

## Interpretation

- **PVO > 0** — volume expanding: moves carry conviction.
- **PVO < 0** — volume contracting: drifts, consolidations.
- A price signal (PPO cross) is more trustworthy when PVO crosses positive at the
  same time — volume confirms price.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_PVO_HISTOGRAM_CSLS` | count | signed bars since the PVO histogram swing flipped |
| `c_PVO_CROSSOVER_BULL` / `_BEAR` | event | PVO crossed its signal line |
| `f_PVO_CROSSOVER_CSLS` | count | signed bars since that cross |
| `c_PVO_ZEROCROSS_BULL` / `_BEAR` | event | PVO crossed zero (volume regime change) |
| `c_PPO_CROSSOVERPVOPOSITIVE_BULL` | event | PPO histogram turned positive within `cls_limit` bars of PVO turning positive (price breakout + volume surge together) |
| `c_PPO_CROSSOVERPVOPOSITIVE_BEAR` | event | PPO histogram turned negative while PVO turned positive (heavy-volume breakdown) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 12 | 3, 8, 12 | fast volume EMA |
| `slow` | 26 | 17, 21, 26 | slow volume EMA |
| `signal` | 9 | 5, 9 | signal EMA |
| `cls_limit` | 4 | 3, 4, 5 | max bar gap for the PPO+PVO coincidence signals |

Default signal: `c_PVO_CROSSOVER_BULL`.

## How to use in stock investing

- **Volume confirmation layer**: don't trade PVO alone — volume says *how much
  conviction*, not *which direction*. Attach it to a price signal.
- `c_PPO_CROSSOVERPVOPOSITIVE_BULL` is the packaged version: bullish price cross
  backed by expanding volume within a few bars — a classic breakout-quality
  filter.
- `c_PPO_CROSSOVERPVOPOSITIVE_BEAR` flags distribution: price breaking down on
  *rising* volume is more serious than on quiet volume.

## Example

```python
import cio.stock as s

sig = s.run_strategy("TSLA", "pvo")
t = sig.iloc[-1]
if t["c_PPO_CROSSOVERPVOPOSITIVE_BULL"] == 1:
    print("Price momentum turned up WITH expanding volume")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:percentage_volume_oscillator_pvo
