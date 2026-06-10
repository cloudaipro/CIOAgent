# CG — Center of Gravity Oscillator

**Engine key**: `cg` · **Module**: `cg_strategy.py` · **Inputs**: Close

## Definition

John Ehlers' CG computes the "balance point" of the last `length` prices, weighting
each price by its recency: `CG = −Σ(i × price[i]) / Σ(price[i])`. By construction
it has essentially **zero lag** for cycles inside the window — Ehlers derived it
from the physics of a balance beam.

## Interpretation

- CG oscillates with the price cycle; **turns in CG coincide with turns in price**
  rather than lagging them.
- The strategy pairs CG with a zero-lag moving average (ZLMA) of itself as the
  signal line: CG crossing its ZLMA = cycle turn.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CG_CROSSOVER_BULL` / `_BEAR` | event | CG crossed its zero-lag MA up / down |
| `f_CG_CROSSOVER_CSLS` | count | signed bars since the cross |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 10 | 3, 5, 7, 10, 14, 20 | CG window — match to the half-cycle you trade |

Default signal: `c_CG_CROSSOVER_BULL`.

## How to use in stock investing

- **Cycle trading in ranges**: CG excels where price oscillates in a channel — buy
  `c_CG_CROSSOVER_BULL` near channel bottoms, exit on `_BEAR`.
- It is *not* a trend tool: in a strong trend CG keeps calling counter-trend
  turns. Gate it behind a trend filter (`er` low efficiency, or price inside
  Bollinger/Keltner consolidation) before acting on it.
- `length` should approximate half the dominant cycle; Ehlers recommends tuning
  per instrument (the parameter grid covers the useful daily range).

## Example

```python
import cio.stock as s

sig = s.run_strategy("PG", "cg", length=10)
if sig.iloc[-1]["c_CG_CROSSOVER_BULL"] == 1:
    print("CG cycle turn up — range-trade entry if market is sideways")
```

## References

- https://www.mesasoftware.com/papers/TheCGOscillator.pdf
- John Ehlers, "The CG Oscillator", *Stocks & Commodities*
