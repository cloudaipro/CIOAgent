# RVI — Relative Volatility Index

**Engine key**: `rvi` · **Module**: `rvi_strategy.py` · **Inputs**: High, Low, Close

## Definition

Donald Dorsey's RVI is RSI computed on **standard deviation instead of price
change**: it asks whether volatility is being generated on up days or down days.
0–100 scale; above 50 = volatility concentrated on the upside.

## Interpretation

- **RVI > 50** — up-moves are the volatile ones: bullish energy.
- **RVI < 50** — volatility on the downside.
- Zones at 50 ± limit_delta (default 80/20) mark one-sided volatility extremes.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_RVI_OVERBOUGHT` / `c_RVI_OVERSOLD` | state | RVI inside the zone |
| `f_RVI_OVERBOUGHTSOLD_CSLS` | count | signed bars since the zone flipped |
| `c_RVI_OVERBOUGHT_BULL` / `_BEAR` | event | crossed up into / down out of overbought |
| `c_RVI_OVERSOLD_BULL` / `_BEAR` | event | crossed up out of / down into oversold |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | std/smoothing window |
| `drift` | 1 | 1–5 | difference period |
| `limit_delta` | 30 | 10, 20, 25, 30 | zone half-width |

Default signal: `c_RVI_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Dorsey's rule**: use RVI to *confirm* other signals — take a buy signal from
  another system only if RVI > 50; take sells only if RVI < 50. He found this
  filter improved most systems he tested.
- As a standalone: `c_RVI_OVERSOLD_BULL` = downside-volatility climax easing —
  similar role to an RSI oversold exit but keyed to volatility, so it reacts to
  *panic structure* rather than price extent.
- Complements price-based oscillators precisely because its input is different.

## Example

```python
import cio.stock as s

sig = s.run_strategy("GS", "rvi", length=14)
t = sig.iloc[-1]
rvi_bullish = t["c_RVI_OVERBOUGHT"] == 1 or t["c_RVI_OVERSOLD_BULL"] == 1
print("RVI confirms long bias:", bool(rvi_bullish))
```

## References

- https://www.tradingsim.com/blog/relative-volatility-index
- Donald Dorsey, "The Relative Volatility Index", *Stocks & Commodities* (1993)
