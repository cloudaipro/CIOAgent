# VIDYA — Variable Index Dynamic Average

**Engine key**: `vidya` · **Module**: `vidya_strategy.py` · **Inputs**: Close

## Definition

Chande's VIDYA is an EMA whose smoothing speed adapts to momentum: the smoothing
constant is scaled by |CMO|, so the average **speeds up in trends and flattens in
chop**. The strategy then reads the *swing structure* of the VIDYA line itself:
confirmed swing lows (support) and highs (resistance).

## Interpretation

- VIDYA hugging price = trending market; VIDYA flat while price oscillates = chop.
- A confirmed swing low in VIDYA = the adaptive trend line has turned up.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_LEVEL` | count | signed bars since the VIDYA swing flipped (+ = upswing age, − = downswing age) |
| `c_VIDYA_SUPPORT_BULL` | event | VIDYA swing-low confirmed this bar (level = +1) |
| `c_VIDYA_SUPPORT_BEAR` | event | VIDYA swing-high confirmed this bar (level = −1) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 12, 15, 20 | VIDYA / CMO window |
| `drift` | 1 | 1–5 | difference period |

Default signal: `c_VIDYA_SUPPORT_BULL`.

## How to use in stock investing

- **Adaptive trend entry**: `c_VIDYA_SUPPORT_BULL` fires on the first bar of a
  confirmed VIDYA upswing — a trend-turn entry that self-adjusts to each stock's
  volatility character (no per-ticker tuning of the MA speed).
- `f_LEVEL` doubles as trend age: small positive = young uptrend (favor entries);
  large = mature (favor holding, not adding).
- Chande's design pairs VIDYA with CMO (`cmo` strategy) — CMO supplies the trend
  strength reading, VIDYA the adaptive trail.

## Example

```python
import cio.stock as s

sig = s.run_strategy("ADBE", "vidya", length=14)
t = sig.iloc[-1]
if t["c_VIDYA_SUPPORT_BULL"] == 1:
    print("VIDYA adaptive trend turned up")
print("trend age (bars):", int(sig["f_LEVEL"].iloc[-1]))
```

## References

- https://www.tradingview.com/script/hdrf0fXV-Variable-Index-Dynamic-Average-VIDYA/
- https://www.perfecttrendsystem.com/blog_mt4_2/en/vidya-indicator-for-mt4
- Tushar Chande, "VIDYA", *Stocks & Commodities* (1992)
