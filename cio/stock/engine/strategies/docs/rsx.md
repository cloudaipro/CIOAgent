# RSX — Jurik RSX (smoothed RSI)

**Engine key**: `rsx` · **Module**: `rsx_strategy.py` · **Inputs**: Close

## Definition

RSX is Mark Jurik's noise-reduced reformulation of RSI: same 0–100 momentum scale,
but computed through a multi-stage recursive filter that removes most of RSI's
jitter **without adding the lag** that ordinary smoothing would.

## Interpretation

Identical playbook to RSI (overbought/oversold around 50 ± limit_delta, 50 line as
regime boundary, divergence as exhaustion warning) — but turns are smoother and
cleaner, with fewer one-bar fakeouts. The cost: very fast V-bottoms register one
or two bars later than raw RSI.

## Output columns

Same structure as `rsi` with the `RSX` token:

| Column | Type | Meaning |
|---|---|---|
| `c_RSX_OVERBOUGHT` / `c_RSX_OVERSOLD` | state | RSX inside the zone (default 75/25) |
| `f_RSX_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_RSX_OVERBOUGHT_BULL` / `_BEAR` | event | up into / down out of overbought |
| `c_RSX_OVERSOLD_BULL` / `_BEAR` | event | up out of / down into oversold |
| `c_RSX_CENTRALLINE_BULL` / `_BEAR` | event | 50-line cross |
| `c_RSX_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs RSX swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | RSX lookback |
| `limit_delta` | 25 | 10, 20, 25, 30 | zone half-width around 50 |
| `ema_length` | 9 | — | divergence price-EMA |

Default signal: `c_RSX_OVERBOUGHT_BULL`.

## How to use in stock investing

- Use wherever you would use RSI but are getting whipsawed — RSX zone exits
  (`c_RSX_OVERSOLD_BULL`) carry fewer false signals on noisy small caps.
- Because RSX is smooth, its **slope** is meaningful: a 50-line cross with RSX
  still accelerating is stronger than a flat drift across.
- Default zone is tighter (75/25) than the RSI default (80/20) — RSX reaches
  extremes less often, so the tighter zone keeps signal frequency comparable.

## Example

```python
import cio.stock as s

sig = s.run_strategy("PLTR", "rsx", length=14, limit_delta=25)
if sig.iloc[-1]["c_RSX_OVERSOLD_BULL"] == 1:
    print("RSX oversold exit — smoother confirmation than raw RSI")
```

## References

- http://jurikres.com/catalog1/ms_rsx.htm
- https://www.tradingview.com/script/fBIe1SWr-STRATEGY-Jurik-RSX/
