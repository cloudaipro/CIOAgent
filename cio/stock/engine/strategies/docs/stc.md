# STC — Schaff Trend Cycle

**Engine key**: `stc` · **Module**: `stc_strategy.py` · **Inputs**: Close

## Definition

STC runs a MACD through a double stochastic calculation over `tclength` bars,
producing a 0–100 oscillator that cycles faster than MACD itself. Designed by
Doug Schaff for FX, it aims to detect trend turns earlier than MACD by exploiting
the tendency of trends to cycle.

## Interpretation

- **STC rising through 25** — new uptrend cycle starting (classic buy).
- **STC falling through 75** — uptrend cycle ending (classic sell/short).
- STC spends long stretches saturated at 0 or 100 during strong trends — the
  *transition* is the signal, saturation is just trend persistence.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_STC_STC` / `f_STC_STOCH` | value | STC and its intermediate stochastic (scaled 0–1) |
| `c_STC_OVERBOUGHT` / `c_STC_OVERSOLD` | state | STC beyond 50 ± limit_delta (default 75/25) |
| `f_STC_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_STC_OVERBOUGHT_BULL` / `_BEAR` | event | up into / down out of the 75 zone |
| `c_STC_OVERSOLD_BULL` / `_BEAR` | event | up out of / down into the 25 zone |
| `c_STC_ZEROCROSS_BULL` / `_BEAR` | event | mid-band trend flip (swing trend through the 25–75 band turned up / down) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `tclength` | 10 | 5, 10 | stochastic cycle window |
| `fast` | 12 | 3, 8, 12 | MACD fast EMA |
| `slow` | 26 | 10, 17, 21, 26 | MACD slow EMA |
| `factor` | 0.5 | — | smoothing factor |
| `limit_delta` | 25 | 10, 20, 25, 30 | zone half-width |

Grid constrained to `slow > fast` and `slow > tclength`.
Default signal: `c_STC_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Swing entries**: `c_STC_OVERSOLD_BULL` (rising through 25) buys the start of
  an up-cycle; exit or trim on `c_STC_OVERBOUGHT_BEAR` (falling through 75).
- STC turns ~1–2 bars before MACD on the same settings — useful where MACD is
  chronically late, but expect a few more false cycles in flat markets.
- Saturation tip: a long stretch of `c_STC_OVERBOUGHT` (check
  `f_STC_OVERBOUGHTSOLD_CSLS`) signals a strong trend — don't short it just
  because STC reads high.

## Example

```python
import cio.stock as s

sig = s.run_strategy("META", "stc")
t = sig.iloc[-1]
if t["c_STC_OVERSOLD_BULL"] == 1:
    print("STC rising through lower band — new up-cycle")
```

## References

- https://howtotrade.com/indicators/schaff-trend-cycle/
- Doug Schaff, "Schaff Trend Cycle" (1990s, FX markets)
