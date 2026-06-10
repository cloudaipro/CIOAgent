# MACD — Moving Average Convergence Divergence

**Engine key**: `macd` · **Module**: `macd_strategy.py` · **Inputs**: Close

## Definition

`MACD = EMA(fast) − EMA(slow)`; `signal = EMA(MACD, signal)`; `histogram = MACD − signal`.
MACD expresses the distance between a fast and a slow trend estimate — positive and
rising means the short-term trend is pulling away upward from the long-term trend.

## Interpretation

- **MACD crosses above signal line** — short-term momentum turning up (bullish).
- **MACD crosses above zero** — fast EMA above slow EMA: trend itself has turned up.
- **Histogram shrinking** while price still rises — the advance is decelerating.
- **Divergence** between price and MACD — trend exhaustion warning.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_MACD_HISTOGRAM_CSLS` | count | signed bars since the histogram swing flipped (trend age of momentum) |
| `c_MACD_CROSSOVER_BULL` / `_BEAR` | event | MACD crossed the signal line up / down |
| `c_MACD_ZEROCROSSING_BULL` / `_BEAR` | event | MACD crossed the zero line up / down |
| `c_MACD_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs MACD swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 12 | 3, 8, 12 | fast EMA length |
| `slow` | 26 | 17, 21, 26 | slow EMA length |
| `signal` | 9 | 5, 9 | signal-line EMA length; also the divergence price-EMA |

Grid restricted to the classic combinations (12/26/9, 8/21/5, 3/17/5).
Default signal: `c_MACD_ZEROCROSSING_BULL`.

## How to use in stock investing

- **Trend entry**: `c_MACD_ZEROCROSSING_BULL` is the conservative trend-following
  entry — slower but filters chop. `c_MACD_CROSSOVER_BULL` is earlier but noisier;
  many traders require the crossover to happen *below* zero for longs (buying a
  dip inside a developing uptrend).
- **Exit/trim**: `c_MACD_CROSSOVER_BEAR` after an extended run, or histogram CSLS
  turning negative while you are long.
- **Faster settings** (8/21/5, 3/17/5) suit swing trading; defaults suit position
  trading on daily bars.
- MACD lags by construction — combine with a volume or volatility signal (e.g.
  `pvo`, `squeeze`) to avoid late entries in fast markets.

## Example

```python
import cio.stock as s

sig = s.run_strategy("MSFT", "macd", fast=12, slow=26, signal=9)
today = sig.iloc[-1]

if today["c_MACD_ZEROCROSSING_BULL"] == 1:
    print("MACD crossed above zero — uptrend confirmation")
elif today["c_MACD_CROSSOVER_BULL"] == 1 and sig.iloc[-1]["f_MACD_HISTOGRAM_CSLS"] > 0:
    print("Early bullish crossover with fresh histogram upswing")
```

## References

- https://www.oanda.com/us-en/learn/indicators-oscillators/determining-entry-and-exit-points-with-macd/
- https://www.dynotrading.com/10-best-macd-settings-for-effective-trading/
- Gerald Appel, *Technical Analysis: Power Tools for Active Investors*
