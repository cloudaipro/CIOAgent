# TRIX — Triple-Smoothed Exponential ROC

**Engine key**: `trix` · **Module**: `trix_strategy.py` · **Inputs**: Close

## Definition

TRIX is the 1-bar rate of change of a triple-smoothed EMA:
`TRIX = %change(EMA(EMA(EMA(C, n), n), n))`, scaled ×100, plus a signal line
(EMA of TRIX). Triple smoothing filters out cycles shorter than the window,
leaving only the dominant trend's momentum.

## Interpretation

- **Sign** — positive TRIX = the smoothed trend is rising.
- **TRIX vs signal line** — earlier turn detection inside the smoothed trend.
- TRIX is deliberately slow: it ignores noise but lags at sharp turns.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_TRIX_CROSSOVER_BULL` / `_BEAR` | event | TRIX crossed its signal line up / down |
| `c_TRIX_ZEROCROSSING_BULL` / `_BEAR` | event | TRIX crossed zero up / down (trend direction change) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 18 | 6, 10, 14, 18, 20 | EMA window (applied 3×) |
| `signal` | 9 | 3, 5, 9 | signal-line EMA |
| `drift` | 1 | — | ROC period |
| `scalar` | 100 | — | output scaling |

Grid constrained to `length > signal`. Default signal: `c_TRIX_CROSSOVER_BULL`.

## How to use in stock investing

- **Position-trading trend filter**: hold longs only while TRIX > 0; enter on
  `c_TRIX_ZEROCROSSING_BULL`, exit on `_BEAR`. Few trades, large average holding
  period — good for tax- and commission-sensitive accounts.
- **Earlier entries**: `c_TRIX_CROSSOVER_BULL` below zero anticipates the zero
  cross — higher risk, earlier price.
- Shorter lengths (6–10) adapt it to swing trading but give up most of the noise
  immunity that justifies TRIX in the first place.

## Example

```python
import cio.stock as s

sig = s.run_strategy("JNJ", "trix", length=18, signal=9)
t = sig.iloc[-1]
if t["c_TRIX_ZEROCROSSING_BULL"] == 1:
    print("TRIX above zero — smoothed trend turned up")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:trix
- https://www.tradingview.com/support/solutions/43000502331-trix/
- Jack Hutson, *Technical Analysis of Stocks & Commodities* (1980s)
