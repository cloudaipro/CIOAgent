# RVGI — Relative Vigor Index

**Engine key**: `rvgi` · **Module**: `rvgi_strategy.py` · **Inputs**: Open, High, Low, Close

## Definition

`RVGI = SWMA(Close − Open) / SWMA(High − Low)` averaged over `length` bars, with a
4-bar SWMA signal line. The premise: in uptrends prices tend to **close higher
than they open** — RVGI measures that closing "vigor" normalized by range.

## Interpretation

- **RVGI above its signal line** — closes consistently strong relative to opens:
  bullish vigor.
- **Crossovers** are the trade events; vigor leads price at cycle turns in
  range-bound conditions.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_RVGI_CROSSOVER_BULL` / `_BEAR` | event | RVGI crossed its signal line up / down |
| `f_RVGI_CROSSOVER_CSLS` | count | signed bars since the cross |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 7, 14, 20 | vigor averaging window |
| `swma_length` | 4 | 2, 3, 4, 7 | symmetric weighted MA for the signal line |

Grid constrained to `length > swma_length`. Default signal: `c_RVGI_CROSSOVER_BULL`.

## How to use in stock investing

- Best as a **cycle-turn timer in sideways or gently trending markets** — like
  `cg`, it calls counter-trend turns relentlessly in strong trends, so gate with
  a trend filter.
- Crossovers **far from zero** (deep negative for bull crosses) carry more edge
  than crossings near zero.
- `f_RVGI_CROSSOVER_CSLS` provides swing age for staleness checks in periodic
  scans.

## Example

```python
import cio.stock as s

sig = s.run_strategy("MCD", "rvgi", length=14)
if sig.iloc[-1]["c_RVGI_CROSSOVER_BULL"] == 1:
    print("RVGI bullish cross — closing vigor turning up")
```

## References

- https://www.investopedia.com/terms/r/relative_vigor_index.asp
- John Ehlers, "Relative Vigor Index", *Stocks & Commodities* (2002)
