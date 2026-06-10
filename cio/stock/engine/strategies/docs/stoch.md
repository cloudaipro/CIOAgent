# STOCH — Stochastic Oscillator

**Engine key**: `stoch` · **Module**: `stoch_strategy.py` · **Inputs**: High, Low, Close

## Definition

`%K = 100 × (Close − LowestLow(k)) / (HighestHigh(k) − LowestLow(k))`, smoothed by
`smooth_k`; `%D = SMA(%K, d)`. It locates the close inside the recent high–low
range: near 100 = closing at the top of the range, near 0 = at the bottom.

## Interpretation

- **Above 50 + limit_delta (default 80)** — overbought: closes pinned to range top.
- **Below 50 − limit_delta (default 20)** — oversold: closes pinned to range bottom.
- Exiting a zone is the actionable event, not being in it — strong trends pin the
  oscillator in a zone for long stretches.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_STOCH_OVERBOUGHT` / `c_STOCH_OVERSOLD` | state | %K inside the zone |
| `f_STOCH_OVERBOUGHTSOLD_CSLS` | count | signed bars since the zone state flipped |
| `c_STOCH_OVERBOUGHT_BULL` | event | %K crossed up into overbought |
| `c_STOCH_OVERBOUGHT_BEAR` | event | %K crossed down out of overbought |
| `c_STOCH_OVERSOLD_BULL` | event | %K crossed up out of oversold (classic buy) |
| `c_STOCH_OVERSOLD_BEAR` | event | %K crossed down into oversold |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `k` | 14 | 5, 9, 14 | %K lookback |
| `d` | 3 | — | %D smoothing |
| `smooth_k` | 3 | — | extra %K smoothing ("slow" stochastic) |
| `limit_delta` | 30 | 10, 20, 25, 30 | zone half-width around 50 |

Default signal: `c_STOCH_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Range trading**: buy `c_STOCH_OVERSOLD_BULL`, sell `c_STOCH_OVERBOUGHT_BEAR` in
  sideways stocks; verify the range first (e.g. low `er` efficiency ratio).
- **Trend pullbacks**: in an uptrend, oversold exits mark pullback-entry points;
  ignore overbought signals (trend keeps it pinned).
- Fast settings (`k=5`) for short swings; defaults for position timing.
- Whipsaw-prone in low-volatility chop — the consolidation filter from `squeeze`
  or `adosc` helps.

## Example

```python
import cio.stock as s

sig = s.run_strategy("NVDA", "stoch", k=14, limit_delta=30)
if sig.iloc[-1]["c_STOCH_OVERSOLD_BULL"] == 1:
    print("Stochastic exited oversold — pullback entry candidate")
```

## References

- https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/
- George Lane, stochastic oscillator (1950s)
