# QQE — Quantitative Qualitative Estimation

**Engine key**: `qqe` · **Module**: `qqe_strategy.py` · **Inputs**: Close

## Definition

QQE builds a trading system around a smoothed RSI ("RSIMA" = EMA of RSI) plus two
trailing levels derived from the RSI's own ATR-like volatility: a **fast line**
(factor 2.618) and a **slow line** (factor 4.236). The trailing lines act like a
SuperTrend stop drawn in RSI space.

## Interpretation

- **RSIMA above 50** — bullish momentum regime; below 50 — bearish.
- **RSIMA crossing the slow trailing line** — momentum trend change in RSI space;
  crossings *below* 50 mark oversold reversals, *above* 50 overbought reversals.
- Overbought/oversold zones at 50 ± limit_delta (default 70/30).

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_QQE_RSIMA` / `f_QQE_FAST` / `f_QQE_SLOW` | value | smoothed RSI and the two trailing lines |
| `c_QQE_TREND_UP` / `c_QQE_TREND_DOWN` | state | RSIMA above / below 50 |
| `c_QQE_OVERBOUGHT` / `c_QQE_OVERSOLD` | state | RSIMA beyond the zone limits |
| `f_QQE_OVERBOUGHTSOLD_CSLS` | count | signed bars since zone flip |
| `c_QQE_OVERBOUGHT_BULL` / `_BEAR`, `c_QQE_OVERSOLD_BULL` / `_BEAR` | event | zone-edge crossings |
| `c_QQE_RSIMA_BULL` | event | RSIMA crossed up through the slow line, all lines < 50 (oversold reversal setup) |
| `c_QQE_RSIMA_BEAR` | event | mirror: crossed down, all lines > 50 |
| `c_QQE_RSIMACONFIRM_BULL` / `_BEAR` | event | the setup above **plus** price above (bull) / below (bear) its EMA — confirmed entry |
| `c_QQE_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs RSIMA swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | RSI lookback |
| `smooth` | 5 | 3, 5, 7 | RSI smoothing into RSIMA |
| `ema_length` | 9 | — | confirmation / divergence price-EMA |
| `drift` | 1 | 1, 2, 3 | difference period |
| `limit_delta` | 20 | 10, 20, 25, 30 | zone half-width |

Fast/slow factors fixed at 2.618 / 4.236. Default signal: `c_QQE_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Primary entry**: `c_QQE_RSIMACONFIRM_BULL` — oversold momentum reversal with
  price already back above its EMA. Unconfirmed `c_QQE_RSIMA_BULL` is earlier but
  catches more falling knives.
- **Regime filter**: hold longs only while `c_QQE_TREND_UP` is 1; QQE's smoothing
  makes this less flip-flappy than raw RSI > 50.
- Works best on daily/weekly bars of liquid stocks; the trailing-line logic needs
  reasonably continuous prices.

## Example

```python
import cio.stock as s

sig = s.run_strategy("GOOG", "qqe")
t = sig.iloc[-1]
if t["c_QQE_RSIMACONFIRM_BULL"] == 1:
    print("QQE confirmed oversold reversal — long entry")
elif t["c_QQE_RSIMA_BULL"] == 1:
    print("QQE setup fired, waiting for price > EMA confirmation")
```

## References

- https://howtotrade.com/indicators/qqe-indicator/
- https://fxcodebase.com/code/viewtopic.php?f=38&t=63956&p=108514
