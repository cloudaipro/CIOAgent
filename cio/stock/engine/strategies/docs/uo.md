# UO — Ultimate Oscillator

**Engine key**: `uo` · **Module**: `uo_strategy.py` · **Inputs**: High, Low, Close

## Definition

Larry Williams' Ultimate Oscillator blends buying pressure over three timeframes:
`UO = 100 × (4×BP7 + 2×BP14 + 1×BP28) / (4×TR7 + 2×TR14 + 1×TR28)` where
BP = buying pressure (close − true low) and TR = true range, averaged over fast /
medium / slow windows. The weighting reduces the false divergences a single-period
oscillator produces.

## Interpretation

- **Above 50 + limit_delta (default 70)** — overbought across timeframes.
- **Below 50 − limit_delta (default 30)** — oversold across timeframes.
- Because three horizons must agree, UO extremes are rarer and more reliable than
  single-window oscillators.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_UO_OVERBOUGHT` / `c_UO_OVERSOLD` | state | UO inside the zone |
| `f_UO_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_UO_OVERBOUGHT_BULL` / `_BEAR` | event | up into / down out of overbought |
| `c_UO_OVERSOLD_BULL` / `_BEAR` | event | up out of / down into oversold |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 7 | 3, 5, 7, 9 | short window |
| `medium` | 14 | 6, 10, 14, 18 | mid window |
| `slow` | 28 | 12, 20, 28, 36 | long window |
| `fast_w` / `medium_w` / `slow_w` | 4 / 2 / 1 | — | blend weights |
| `limit_delta` | 20 | 10, 20, 25, 30 | zone half-width |
| `drift` | 1 | 1–4 | difference period |

Grid keeps the canonical 1:2:4 window ratio. Default signal: `c_UO_OVERBOUGHT_BULL`.

## How to use in stock investing

- Williams' original rule buys **bullish divergence + oversold**: UO under 30,
  price makes a lower low but UO doesn't, then UO breaks its recent high. The
  engine's `c_UO_OVERSOLD_BULL` (recovery out of oversold) is the simplified
  trigger for that pattern.
- Because UO needs all three horizons stretched, signals are infrequent — suited
  to position entries on daily bars rather than active trading.
- Exits: `c_UO_OVERBOUGHT_BEAR` or a fixed profit/stop framework.

## Example

```python
import cio.stock as s

sig = s.run_strategy("KO", "uo")
if sig.iloc[-1]["c_UO_OVERSOLD_BULL"] == 1:
    print("Ultimate Oscillator recovering from multi-timeframe oversold")
```

## References

- https://school.stockcharts.com/doku.php?id=technical_indicators:ultimate_oscillator
- Larry Williams, "The Ultimate Oscillator", *Technical Analysis of Stocks & Commodities* (1985)
