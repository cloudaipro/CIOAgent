# ABERRATION — Aberration Volatility Bands

**Engine key**: `aberration` · **Module**: `aberration_strategy.py` · **Inputs**: High, Low, Close

## Definition

A volatility-band trend system built from an SMA of typical price (ZG midline)
with upper (SG) and lower (XG) bands offset by ATR. Trend state: close above the
upper band = buyers dominant, close below the lower band = sellers dominant,
inside the bands = neutral.

## Interpretation

- **Close > SG (upper band)** — price escaped the volatility envelope upward:
  trend up.
- **Close < XG (lower band)** — trend down.
- Inside the bands — no committed trend.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_ABERRATION_TREND` | state | +1 above upper band, −1 below lower band, 0 inside |
| `f_ABERRATION_TREND_CSLS` | count | signed bars since the trend state flipped |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 5 | 3, 5, 7 | midline SMA window |
| `atr_length` | 15 | 3, 5, 7, 10, 15, 20 | ATR window for band offset |

No default signal (feature-style output).

## How to use in stock investing

- **Classic Aberration system** (Keith Fitschen's futures system): enter long when
  the trend flips +1, exit when price re-crosses the midline; symmetric short
  side. Here the flip is visible as `f_ABERRATION_TREND` changing value with
  `f_ABERRATION_TREND_CSLS` resetting to ±1.
- Suits *position* trading on liquid names — the system's edge came from catching
  long trends and tolerating whipsaws between them.
- `f_ABERRATION_TREND_CSLS` magnitude = trend age; large values mean the move is
  mature and risk/reward for fresh entries is worse.

## Example

```python
import cio.stock as s

sig = s.run_strategy("CAT", "aberration")
trend = sig["f_ABERRATION_TREND"].iloc[-1]
age = sig["f_ABERRATION_TREND_CSLS"].iloc[-1]
print({1: "uptrend", -1: "downtrend", 0: "neutral"}[int(trend)], f"for {abs(int(age))} bars")
```

## References

- https://blog.xcaldata.com/exploring-aberration-unraveling-volatility-indicators/
- Keith Fitschen, Aberration trading system (1986, Futures Truth top-ranked)
