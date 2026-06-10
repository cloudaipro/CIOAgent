# FISHER — Fisher Transform

**Engine key**: `fisher` · **Module**: `fisher_strategy.py` · **Inputs**: High, Low

## Definition

Ehlers' Fisher Transform maps the median price's position in its recent range onto
a Gaussian-like scale: `Fisher = 0.5 × ln((1 + x) / (1 − x))` where x is the
normalized price location in the `length`-bar range. The transform makes turning
points appear as sharp, well-defined peaks instead of the mushy extremes raw
prices produce.

## Interpretation

- Sharp peak in Fisher → price extreme with statistically unusual displacement —
  reversals cluster at these peaks.
- This strategy distills the line into one feature: the **signed age of the
  current Fisher swing**.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_FISHER_CSLS` | count | bars since the Fisher line's swing direction last flipped, signed (+ = upswing, − = downswing); `NaN` during warm-up |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 9 | — | range-normalization window |
| `signal` | 1 | — | signal-line shift |

No parameter grid; no default signal column (feature-only strategy).

## How to use in stock investing

- Use `f_FISHER_CSLS` as a **swing-age feature**: a value of +1 marks the first
  bar of a fresh upswing in the transform — the highest-probability turn bar; a
  large value means the swing is mature.
- As a model feature it pairs naturally with zone indicators: e.g. only act on
  `rsi` oversold exits when Fisher has just flipped positive (CSLS = +1..+3).
- Fisher is fast and aggressive — never trade its turns against a strong trend
  without a filter.

## Example

```python
import cio.stock as s

sig = s.run_strategy("NFLX", "fisher")
csls = sig["f_FISHER_CSLS"].iloc[-1]
if 1 <= csls <= 3:
    print(f"Fresh Fisher upswing ({int(csls)} bars old) — reversal window")
```

## References

- https://www.tradingview.com/support/solutions/43000589141-fisher-transform/
- John Ehlers, *Cybernetic Analysis for Stocks and Futures* (2004)
