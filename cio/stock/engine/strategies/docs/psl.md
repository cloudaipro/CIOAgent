# PSL — Psychological Line

**Engine key**: `psl` · **Module**: `psl_strategy.py` · **Inputs**: Close

## Definition

`PSL = 100 × (number of up bars) / length` — the percentage of the last `length`
bars that closed higher. A pure sentiment ratio: 100 = every bar was up, 0 = every
bar was down. Popular in Japanese and Chinese technical practice.

## Interpretation

- **PSL > 50 + limit_delta (default 75)** — crowd persistently buying: overbought
  sentiment, advances getting one-sided.
- **PSL < 50 − limit_delta (default 25)** — persistent selling: capitulation zone.
- 50 = balanced tape.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_PSL_OVERBOUGHT` / `c_PSL_OVERSOLD` | state | PSL inside the extreme zone |
| `f_PSL_OVERBOUGHTSOLD_CSLS` | count | signed bars since the zone flipped |
| `c_PSL_OVERBOUGHT_BULL` / `_BEAR` | event | crossed up into / down out of overbought |
| `c_PSL_OVERSOLD_BULL` / `_BEAR` | event | crossed up out of / down into oversold |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 12 | 5, 9, 12, 15, 20 | counting window |
| `limit_delta` | 25 | 10, 20, 25, 30 | zone half-width around 50 |
| `drift` | 1 | 1–5 | up-bar comparison lag |

Default signal: `c_PSL_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Contrarian timing**: `c_PSL_OVERSOLD_BULL` (recovery from a stretch where
  almost every bar fell) marks washed-out sentiment — strongest in fundamentally
  sound names after news-driven dumps.
- PSL ignores *magnitude* — a stock can drift down 12 small bars and read the
  same as a crash. Pair with a volatility or volume check before sizing.
- In strong uptrends PSL pins high for long periods; use
  `f_PSL_OVERBOUGHTSOLD_CSLS` to distinguish fresh extremes from persistent ones.

## Example

```python
import cio.stock as s

sig = s.run_strategy("NKE", "psl", length=12, limit_delta=25)
if sig.iloc[-1]["c_PSL_OVERSOLD_BULL"] == 1:
    print("Sentiment washout recovering — contrarian buy window")
```

## References

- https://help.tradestation.com/10_00/eng/tradestationhelp/elanalysis/indicator/psychological_line_indicator_.htm
