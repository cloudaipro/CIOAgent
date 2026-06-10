# ER — Kaufman Efficiency Ratio

**Engine key**: `er` · **Module**: `er_strategy.py` · **Inputs**: Close

## Definition

`ER = |Close_t − Close_{t−length}| / Σ|ΔClose|` — net price change divided by the
sum of all bar-to-bar changes over the window. Range 0–1: **1 = perfectly
efficient move** (straight line), **0 = pure noise** (went nowhere despite lots of
movement). Perry Kaufman built it to drive his adaptive moving average (KAMA).

## Interpretation

- **High ER (> ~0.6)** — strong directional trend (direction itself not encoded —
  check price separately).
- **Low ER (< ~0.3)** — choppy, mean-reverting conditions.
- The strategy maps ER onto zones at `0.5 ± limit_delta`.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_ER_UPZONE` / `c_ER_DOWNZONE` | state | ER above / below the zone thresholds (trending / choppy market) |
| `f_ER_UPDOWNZONE_CSLS` | count | signed bars since the regime flipped |
| `c_ER_UPZONE_BULL` / `_BEAR` | event | ER crossed up into / down out of the trending zone |
| `c_ER_DOWNZONE_BULL` / `_BEAR` | event | ER crossed up out of / down into the choppy zone |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 13 | 5, 10, 15, 20 | efficiency window |
| `limit_delta` | 0.1 | 0.25, 0.3, 0.35, 0.4 | zone half-width around 0.5 |

Default signal: `c_ER_UPZONE_BULL`.

## How to use in stock investing

- **Regime switch, not direction**: ER tells you *which playbook* applies —
  trend-following signals (`macd`, `vidya`, `trix`) when `c_ER_UPZONE` is 1;
  mean-reversion signals (`rsi`, `stoch`, `cg`) when `c_ER_DOWNZONE` is 1.
- `c_ER_UPZONE_BULL` = a trend is *igniting* — breakout entries have the wind at
  their back for the next stretch.
- Feed it to position sizing: scale exposure with ER so chop gets less capital.

## Example

```python
import cio.stock as s

sig = s.run_strategy("WMT", "er", length=13, limit_delta=0.3)
t = sig.iloc[-1]
playbook = "trend-following" if t["c_ER_UPZONE"] == 1 else "mean-reversion"
print("Active playbook:", playbook)
```

## References

- https://www.whselfinvest.com/en-be/trading-platform/free-trading-strategies/tradingsystem/33-kaufman-efficiency-ratio
- Perry Kaufman, *Trading Systems and Methods*
