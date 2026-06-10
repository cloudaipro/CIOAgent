# INERTIA — Dorsey Inertia

**Engine key**: `inertia` · **Module**: `inertia_strategy.py` · **Inputs**: High, Low, Close

## Definition

Donald Dorsey's Inertia is the Relative Volatility Index (RVI) smoothed by linear
regression over `length` bars. RVI measures whether volatility is concentrated on
up-moves or down-moves; smoothing it with regression yields a slow trend gauge:
**inertia** in the physics sense — a trend in motion tends to stay in motion.

## Interpretation

- **Inertia > 50** — positive (upward) inertia: the trend backdrop is bullish.
- **Inertia < 50** — negative inertia.
- Crossings of 50 are infrequent regime changes, not trade-by-trade signals.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_INERTIA_CENTRALLINE_BULL` | event | inertia crossed above 50 (bullish regime begins) |
| `c_INERTIA_CENTRALLINE_BEAR` | event | inertia crossed below 50 |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 20 | 5, 10, 15, 20, 30 | regression smoothing window |
| `rvi_length` | 14 | 5, 9, 14, 20 | underlying RVI window |

Default signal: `c_INERTIA_CENTRALLINE_BULL`.

## How to use in stock investing

- **Regime/confirmation layer** (Dorsey's intent): hold or initiate longs only
  while inertia > 50; treat `c_INERTIA_CENTRALLINE_BEAR` as a de-risking cue for
  positions opened on faster signals.
- Because it is volatility-based rather than price-based, it confirms trends from
  an independent angle — good complement to EMA-based filters.
- Slow by design; never use as a standalone entry timer.

## Example

```python
import cio.stock as s

sig = s.run_strategy("UNH", "inertia", length=20, rvi_length=14)
t = sig.iloc[-1]
if t["c_INERTIA_CENTRALLINE_BULL"] == 1:
    print("Inertia crossed 50 — volatility now favors the upside")
```

## References

- https://stonehillforex.com/2024/02/dorsey-inertia-as-a-confirmation-indicator/
- Donald Dorsey, "The Relative Volatility Index", *Stocks & Commodities* (1993/1995)
