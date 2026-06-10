# SQUEEZE — TTM Squeeze (Pro)

**Engine key**: `squeeze` · **Module**: `squeeze_strategy.py` · **Inputs**: High, Low, Close

## Definition

John Carter's TTM Squeeze detects volatility compression: a **squeeze is ON** when
the Bollinger Bands fit *inside* the Keltner Channels — volatility unusually low
relative to average range. The Pro variant grades compression at three Keltner
widths (wide 2.0 / normal 1.5 / narrow 1.0). A momentum histogram indicates the
likely breakout direction when the squeeze releases.

## Interpretation

- **Squeeze ON** — energy building; the market is coiling.
- **Squeeze OFF after ON** — release: expect a directional expansion.
- **Momentum histogram sign and slope at release** — the expected direction.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_SQZ_ON_WIDE` / `_NORMAL` / `_NARROW` | state | compression at each grade (narrow = tightest coil) |
| `c_SQZ_OFF` | state | no squeeze |
| `f_SQZ_ON_CSLS` / `f_SQZ_OFF_CSLS` | count | signed duration of the current squeeze/no-squeeze phase |
| `f_BANDWIDTH` / `f_BBAND_PERCENT` | value | Bollinger bandwidth and %B |
| `c_SQZ_ZEROCROSS_BULL` / `_BEAR` | event | momentum histogram crossed zero up / down |
| `c_SQZ_HISTMOMPOS_UP` (+ `_CSLS`) | state | histogram positive and rising (bullish acceleration) |
| `c_SQZ_HISTMOMNEG_UP` (+ `_CSLS`) | state | histogram positive but falling (bullish deceleration) |
| `c_SQZ_HISTMOMPOS_DOWN` (+ `_CSLS`) | state | histogram negative but rising (bearish deceleration) |
| `c_SQZ_HISTMOMNEG_DOWN` (+ `_CSLS`) | state | histogram negative and falling (bearish acceleration) |

## Parameters

| Name | Default | Notes |
|---|---|---|
| `bb_length` / `bb_std` | 20 / 2.0 | Bollinger settings |
| `kc_length` | 20 | Keltner window |
| `kc_scalar_wide` / `_normal` / `_narrow` | 2.0 / 1.5 / 1.0 | Keltner widths per grade |
| `mom_length` / `mom_smooth` | 12 / 6 | momentum histogram |

No meaningful grid; no default signal (multi-column system).

## How to use in stock investing

- **Carter's play**: wait for several bars of squeeze ON (use `f_SQZ_ON_CSLS` ≥ 5),
  then enter in the histogram's direction on the first OFF bar; exit when the
  histogram decelerates (`c_SQZ_HISTMOMNEG_UP` for longs).
- Narrow-grade squeezes (`c_SQZ_ON_NARROW`) are the tightest coils → biggest
  expected expansions.
- Squeeze tells you *when*, momentum tells you *which way* — never trade an OFF
  transition with a flat histogram.

## Example

```python
import cio.stock as s

sig = s.run_strategy("AMZN", "squeeze")
t = sig.iloc[-1]
coiled = t["c_SQZ_ON_NARROW"] == 1 and sig["f_SQZ_ON_CSLS"].iloc[-1] >= 5
if coiled and t["c_SQZ_ZEROCROSS_BULL"] == 1:
    print("Tight squeeze with bullish momentum — breakout watch")
```

## References

- https://tlc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/T-U/TTM-Squeeze
- John Carter, *Mastering the Trade*
