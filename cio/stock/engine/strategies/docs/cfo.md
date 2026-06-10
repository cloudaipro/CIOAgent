# CFO — Chande Forecast Oscillator

**Engine key**: `cfo` · **Module**: `cfo_strategy.py` · **Inputs**: Close

## Definition

`CFO = 100 × (Close − Forecast) / Close`, where Forecast is the value predicted by
a `length`-bar linear regression projected to the current bar. Positive CFO =
price running **above** its own regression forecast; negative = below.

(Note: in this codebase "cfo" always means this indicator, not the finance role.)

## Interpretation

- **CFO > 0** — price stronger than its recent trajectory implied.
- **CFO < 0** — price weaker than trajectory.
- **CFO crossing its SMA** — the deviation itself is trending — early turn signal.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CFO_CROSSOVER_BULL` / `_BEAR` | event | CFO crossed its SMA up / down |
| `c_CFO_ZEROCROSSING_BULL` / `_BEAR` | event | CFO crossed zero up / down (price moved above/below forecast) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 9 | 3, 5, 7, 9, 14, 20 | regression window; SMA uses the same length |

Default signal: `c_CFO_CROSSOVER_BULL`.

## How to use in stock investing

- **Trend-acceleration tell**: `c_CFO_ZEROCROSSING_BULL` says price just beat its
  own regression path — often the first bar of a steeper leg. Useful for adding
  to winners rather than initiating contrarian trades.
- **Fade extremes cautiously**: large positive CFO that crosses down through its
  SMA (`c_CFO_CROSSOVER_BEAR`) flags a stretched move reverting toward trend.
- Short windows (3–5) behave like a noise detector; 14–20 track swing structure.

## Example

```python
import cio.stock as s

sig = s.run_strategy("AVGO", "cfo", length=9)
t = sig.iloc[-1]
if t["c_CFO_ZEROCROSSING_BULL"] == 1:
    print("Price above its regression forecast — trend accelerating")
```

## References

- https://www.fmlabs.com/reference/default.htm?url=ForecastOscillator.htm
- Tushar Chande, *The New Technical Trader* (1994)
