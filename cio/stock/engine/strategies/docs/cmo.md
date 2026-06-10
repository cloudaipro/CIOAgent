# CMO — Chande Momentum Oscillator

**Engine key**: `cmo` · **Module**: `cmo_strategy.py` · **Inputs**: Close

## Definition

`CMO = 100 × (ΣUp − ΣDown) / (ΣUp + ΣDown)` over `length` bars, where ΣUp/ΣDown are
the sums of up-bar and down-bar changes. Range −100 to +100. Unlike RSI it is not
smoothed, so it reaches extremes faster and shows raw momentum balance.

## Interpretation

- **Above +limit_delta (default +50)** — overbought: up-moves dominate strongly.
- **Below −limit_delta (default −50)** — oversold.
- **CMO vs its SMA crossover** — momentum turning relative to its own recent average.
- High |CMO| also marks a *trending* market (Chande used it for trend strength).

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CMO_OVERBOUGHT` / `c_CMO_OVERSOLD` | state | CMO beyond ±limit_delta |
| `f_CMO_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_CMO_OVERBOUGHT_BULL` / `_BEAR` | event | crossed up into / down out of overbought |
| `c_CMO_OVERSOLD_BULL` / `_BEAR` | event | crossed up out of / down into oversold |
| `c_CMO_CROSSOVER_BULL` / `_BEAR` | event | CMO crossed its SMA up / down |
| `f_CMO_CROSSOVER_CSLS` | count | signed bars since the CMO/SMA cross |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | CMO lookback |
| `sma_length` | 10 | 3, 5, 7, 10, 15, 20 | smoothing line for crossover signals |
| `limit_delta` | 50 | 50, 60, 75 | zone threshold (±50 standard) |
| `drift` | 1 | 1–5 | bar difference period |

Default signal: `c_CMO_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Reversal timing**: `c_CMO_OVERSOLD_BULL` (recovery from below −50) is the
  classic buy; require the CMO/SMA crossover to agree for fewer false starts.
- **Trend strength filter**: |CMO| > 50 means trending — favor breakout entries;
  |CMO| < 20 means choppy — favor range tactics or stand aside.
- Chande's own pairing: use CMO to set VIDYA's adaptive speed (see `vidya` doc) —
  the two are designed to work together.

## Example

```python
import cio.stock as s

sig = s.run_strategy("INTC", "cmo", length=14, limit_delta=50)
t = sig.iloc[-1]
if t["c_CMO_OVERSOLD_BULL"] == 1 and t["c_CMO_CROSSOVER_BULL"] == 1:
    print("CMO oversold recovery confirmed by SMA crossover")
```

## References

- https://www.investopedia.com/terms/c/chandemomentumoscillator.asp
- https://trendspider.com/learning-center/chande-momentum-oscillator/
- Tushar Chande & Stanley Kroll, *The New Technical Trader* (1994)
