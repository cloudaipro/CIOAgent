# COPPOCK — Coppock Curve

**Engine key**: `coppock` · **Module**: `coppock_strategy.py` · **Inputs**: Close

## Definition

`Coppock = WMA(ROC(slow) + ROC(fast), length)` — originally a *monthly* indicator
(WMA-10 of ROC-14 + ROC-11) commissioned by a church to find major market bottoms;
the engine's defaults (7/5/10) adapt it to shorter bars. Edwin Coppock modeled the
windows on grief periods: markets, like people, need time to mourn losses.

## Interpretation

The single classic signal: **the curve turns upward from below zero** — long-term
downside momentum has stopped getting worse. It is a buy-only indicator; Coppock
never published a sell rule.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_COPPOCK_BULL` | event | curve below zero **and** turned up this bar (prior bar was still falling) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 7 | 3, 5, 9 | WMA smoothing |
| `fast` | 5 | 3, 5, 7 | short ROC |
| `slow` | 10 | 6, 10, 14 | long ROC |

Grid constrained to `slow > fast`. Default signal: `c_COPPOCK_BULL`.

## How to use in stock investing

- **Major-bottom detector**: most meaningful on weekly/monthly bars of indices and
  large caps. On monthly S&P data the classic settings flagged the starts of most
  post-war bull markets with very few false positives.
- Treat a `c_COPPOCK_BULL` on an index as a *risk-on regime* input for the whole
  portfolio rather than a single-stock trade.
- No sell signal: pair with a trend-following exit (`trix`, `vidya`) or a fixed
  risk rule.

## Example

```python
import cio.stock as s

df = s.get_history("SPY", "2015-01-01", "2026-06-01").resample("W").agg(
    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
)
sig = s.run_strategy(df, "coppock")
recent = sig[sig["c_COPPOCK_BULL"] == 1].tail()
print("recent Coppock buy signals:\n", recent.index.tolist())
```

## References

- https://en.wikipedia.org/wiki/Coppock_curve
- E.S.C. Coppock, *Barron's* (1962)
