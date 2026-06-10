# AWESOME — Awesome Oscillator Saucers

**Engine key**: `awesome` · **Module**: `awesome_strategy.py` · **Inputs**: High, Low

## Definition

Bill Williams' Awesome Oscillator: `AO = SMA(midprice, fast) − SMA(midprice, slow)`
with midprice = (H+L)/2, defaults 5/34. This strategy detects the **saucer**
pattern: a three-bar dip-and-recover in AO while it stays on one side of zero.

## Interpretation

- **Bullish saucer**: AO above zero, two falling (red) bars then a rising (green)
  bar — the uptrend's momentum dipped and resumed: continuation buy.
- **Bearish saucer**: mirror image below zero.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_SAUCERS_TWINPEAK_BULL` | event | bullish saucer completed this bar (AO > 0, red-red-green) |
| `c_SAUCERS_TWINPEAK_BEAR` | event | bearish saucer completed this bar (AO < 0, green-green-red) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 5 | 3, 5 | fast midprice SMA |
| `slow` | 34 | 17, 34 | slow midprice SMA |

Grid restricted to 5/34 and 3/17. Default signal: `c_SAUCERS_TWINPEAK_BULL`.

## How to use in stock investing

- **Trend continuation entry**: the saucer is Williams' add-on/re-entry signal —
  by requiring AO already above zero, you are buying a momentum dip inside an
  established uptrend, not picking a bottom.
- Entry per Williams: buy stop above the high of the saucer's green bar, so the
  trade only triggers on follow-through.
- Avoid in choppy tape: AO oscillating around zero produces saucers with no
  trend behind them — check `er` or AO's distance from zero first.

## Example

```python
import cio.stock as s

sig = s.run_strategy("LMT", "awesome", fast=5, slow=34)
if sig.iloc[-1]["c_SAUCERS_TWINPEAK_BULL"] == 1:
    print("AO bullish saucer — momentum dip resolved upward")
```

## References

- https://www.ig.com/en/trading-strategies/a-traders-guide-to-using-the-awesome-oscillator-200130
- Bill Williams, *Trading Chaos*
