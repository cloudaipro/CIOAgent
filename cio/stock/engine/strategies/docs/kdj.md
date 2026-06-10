# KDJ — Stochastic with J Line

**Engine key**: `kdj` · **Module**: `kdj_strategy.py` · **Inputs**: High, Low, Close

## Definition

KDJ extends the stochastic oscillator with a third line:
`J = 3K − 2D`. K and D are the smoothed stochastic lines; J amplifies their spread
and overshoots beyond 0–100, making it the early-warning line. Very popular in
Asian retail markets.

## Interpretation

- **Golden cross**: K crosses above D with J leading above — bullish.
- **Death cross**: K crosses below D with J leading below — bearish.
- J beyond 100 / below 0 marks extreme conditions earlier than K or D.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_KDJ_CROSSOVER_BULL` | event | K crossed above D **and** J crossed above D (golden cross, J-confirmed) |
| `c_KDJ_CROSSOVER_BEAR` | event | K crossed below D **and** J crossed below D (death cross, J-confirmed) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 9 | 5, 9, 13, 20 | stochastic lookback |
| `signal` | 3 | 2, 3, 5, 7 | K/D smoothing |

Grid constrained to `length > signal`. Default signal: `c_KDJ_CROSSOVER_BULL`.

## How to use in stock investing

- Requiring the J line to confirm the K/D cross (as this strategy does) filters
  out shallow crosses where momentum isn't actually rotating.
- **Best used with location**: a golden cross while the lines are low (after a
  pullback) is a buy; the same cross high in the range is usually noise. Combine
  with `stoch`'s zone columns or check the price trend separately.
- Short `length` (5) for swing trades; 13–20 for position timing.

## Example

```python
import cio.stock as s

sig = s.run_strategy("BABA", "kdj", length=9, signal=3)
if sig.iloc[-1]["c_KDJ_CROSSOVER_BULL"] == 1:
    print("KDJ golden cross (J-line confirmed)")
```

## References

- https://market-bulls.com/kdj-indicator/
