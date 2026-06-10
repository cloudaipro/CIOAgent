# WILLR — Williams %R

**Engine key**: `willr` · **Module**: `willr_strategy.py` · **Inputs**: High, Low, Close

## Definition

`%R = −100 × (HighestHigh(length) − Close) / (HighestHigh(length) − LowestLow(length))`.
Scale runs 0 to −100: 0 = close at the period high, −100 = close at the period low.
It is the stochastic %K flipped to a negative scale, unsmoothed.

## Interpretation

- **Above −50 + limit_delta (default −20)** — overbought.
- **Below −50 − limit_delta (default −80)** — oversold.
- **−50 midline** — bull/bear boundary; crossing it signals control change.
- Being unsmoothed, %R reacts one bar faster than slow stochastic but whipsaws more.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_WILLR_OVERBOUGHT` / `c_WILLR_OVERSOLD` | state | %R inside the zone |
| `f_WILLR_OVERBOUGHTSOLD_CSLS` | count | signed bars since the zone state flipped |
| `c_WILLR_OVERBOUGHT_BULL` / `_BEAR` | event | crossed up into / down out of overbought |
| `c_WILLR_OVERSOLD_BULL` / `_BEAR` | event | crossed up out of / down into oversold |
| `c_WILLR_CENTRALLINE_BULL` / `_BEAR` | event | crossed the −50 midline up / down |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | lookback window |
| `limit_delta` | 30 | 10, 20, 25, 30 | zone half-width around −50 |

Default signal: `c_WILLR_OVERBOUGHT_BULL`.

## How to use in stock investing

- Same playbook as stochastic: zone exits in ranges, pullback timing in trends.
- Larry Williams' own usage: in an uptrend, buy when %R dips below −80 and turns
  back up (`c_WILLR_OVERSOLD_BULL`); the midline cross confirms.
- Because it is fast and noisy, demand confluence — e.g. only act when a trend
  indicator (`vidya`, `macd` zero-line) agrees with the direction.

## Example

```python
import cio.stock as s

sig = s.run_strategy("AMD", "willr", length=14, limit_delta=30)
t = sig.iloc[-1]
if t["c_WILLR_OVERSOLD_BULL"] == 1 and t["c_WILLR_CENTRALLINE_BULL"] != 1:
    print("Early oversold exit — watch for -50 cross to confirm")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:williams_r
- Larry Williams, *How I Made One Million Dollars Trading Commodities* (1973)
