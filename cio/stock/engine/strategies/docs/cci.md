# CCI — Commodity Channel Index

**Engine key**: `cci` · **Module**: `cci_strategy.py` · **Inputs**: High, Low, Close

## Definition

`CCI = (TypicalPrice − SMA(TypicalPrice, length)) / (0.015 × MeanDeviation)` where
`TypicalPrice = (H + L + C) / 3`. It measures how far price has strayed from its
statistical average, normalized so ~70–80% of values land between −100 and +100.

## Interpretation

- **Above +100** — unusually strong upward deviation: either breakout strength or
  overbought, depending on context.
- **Below −100** — unusually weak: capitulation or emerging downtrend.
- **Zero line** — price at its moving average.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CCI_OVERBOUGHT` / `c_CCI_OVERSOLD` | state | CCI beyond +100 / below −100 |
| `f_CCI_CSLS` | count | signed bars since the zone state flipped |
| `c_CCI_OVERBOUGHT_BULL` | event | CCI crossed up through +100 (breakout-style entry) |
| `c_CCI_OVERBOUGHT_BEAR` | event | CCI fell back below +100 (momentum exit) |
| `c_CCI_OVERSOLD_BULL` | event | CCI recovered above −100 (reversal buy) |
| `c_CCI_OVERSOLD_BEAR` | event | CCI broke below −100 |
| `c_CCI_CENTRALLINE_BULL` / `_BEAR` | event | zero-line cross up / down |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 3, 5, 7, 9, 14, 20 | SMA / deviation lookback |

Thresholds fixed at ±100 (standard). Default signal: `c_CCI_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Momentum/breakout style (Lambert's original)**: long when CCI crosses above
  +100 (`c_CCI_OVERBOUGHT_BULL`), exit when it drops back below
  (`c_CCI_OVERBOUGHT_BEAR`). Treats the zone as strength, not a reversal warning.
- **Mean-reversion style**: buy `c_CCI_OVERSOLD_BULL` recoveries in stocks with a
  stable long-term uptrend.
- Pick one style per ticker — the two interpretations give opposite trades on the
  same signal. `f_CCI_CSLS` tells you how mature the current zone visit is.

## Example

```python
import cio.stock as s

sig = s.run_strategy("XOM", "cci", length=14)
t = sig.iloc[-1]
if t["c_CCI_OVERBOUGHT_BULL"] == 1:
    print("CCI breakout above +100 — momentum entry (Lambert style)")
if t["c_CCI_OVERBOUGHT_BEAR"] == 1:
    print("CCI lost +100 — momentum exit")
```

## References

- https://www.oanda.com/bvi-ft/lab-education/technical_analysis/commodity-channel-index/
- Donald Lambert, "Commodity Channel Index", *Commodities* magazine (1980)
