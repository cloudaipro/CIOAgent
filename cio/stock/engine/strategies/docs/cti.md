# CTI — Correlation Trend Indicator

**Engine key**: `cti` · **Module**: `cti_strategy.py` · **Inputs**: Close

## Definition

Ehlers' CTI is the Pearson correlation between price and a straight rising line
over `length` bars. Range −1 to +1: **+1 = price tracking a perfect uptrend
line**, −1 = perfect downtrend, 0 = no linear trend. It converts "how trendy is
this?" into a bounded statistic.

## Interpretation

- **|CTI| near 1** — strongly trending stretch.
- **CTI near 0** — chop.
- This strategy runs a fast and a slow CTI and trades their crossings **only at
  extremes**: a bullish cross while both are below −0.5 (downtrend exhausting),
  bearish cross while both above +0.5 (uptrend exhausting).

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CTI_CROSSOVER_BULL` | event | fast CTI crossed above slow CTI while both < −0.5 (downtrend losing its grip) |
| `c_CTI_CROSSOVER_BEAR` | event | fast crossed below slow while both > +0.5 (uptrend losing its grip) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 5 | 3, 5, 7 | fast CTI window |
| `slow` | 10 | 6, 10, 14 | slow CTI window |

Grid constrained to `slow > fast`. Default signal: `c_CTI_CROSSOVER_BULL`.

## How to use in stock investing

- These are **early reversal warnings**, not confirmations: the bull signal fires
  while the downtrend is still intact (correlation deeply negative) but the
  short-window trend has started curling up.
- Use them to start watching / scaling in small, then add on a confirming signal
  (e.g. `macd` zero cross or `vidya` support) — not to take a full position.
- Also valuable inverted: absence of any signal with CTI pinned near +1 simply
  means "trend healthy, leave it alone".

## Example

```python
import cio.stock as s

sig = s.run_strategy("DIS", "cti", fast=5, slow=10)
if sig.iloc[-1]["c_CTI_CROSSOVER_BULL"] == 1:
    print("Downtrend correlation breaking — early reversal watch")
```

## References

- https://tlc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/C-D/CorrelationTrendIndicator
- https://financial-hacker.com/petra-on-programming-a-unique-trend-indicator/
- John Ehlers, "Correlation as a Trend Indicator", *Stocks & Commodities* (2020)
