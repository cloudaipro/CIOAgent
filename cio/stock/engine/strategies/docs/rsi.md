# RSI — Relative Strength Index

**Engine key**: `rsi` · **Module**: `rsi_strategy.py` · **Inputs**: Close

## Definition

RSI measures the speed and magnitude of recent price changes on a 0–100 scale:
`RSI = 100 − 100 / (1 + avg_gain / avg_loss)` over `length` bars (Wilder smoothing).
High values mean recent gains dominate; low values mean recent losses dominate.

## Interpretation

- **Above 50 + limit_delta (default 80)** — overbought: the advance is stretched.
- **Below 50 − limit_delta (default 20)** — oversold: the decline is stretched.
- **50 line** — the bull/bear momentum boundary; crossing it signals a momentum
  regime change.
- **Divergence** — price makes a new extreme but RSI does not: the move is losing
  internal strength.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_RSI_OVERBOUGHT` / `c_RSI_OVERSOLD` | state | RSI currently inside the zone |
| `f_RSI_OVERBOUGHTSOLD_CSLS` | count | signed bars since the zone state flipped |
| `c_RSI_OVERBOUGHT_BULL` | event | RSI crossed **up into** overbought (strong momentum burst) |
| `c_RSI_OVERBOUGHT_BEAR` | event | RSI crossed **down out of** overbought (classic sell trigger) |
| `c_RSI_OVERSOLD_BULL` | event | RSI crossed **up out of** oversold (classic buy trigger) |
| `c_RSI_OVERSOLD_BEAR` | event | RSI crossed **down into** oversold |
| `c_RSI_CENTRALLINE_BULL` / `_BEAR` | event | RSI crossed the 50 line up / down |
| `c_RSI_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs RSI swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 14 | 5, 9, 14, 20 | RSI lookback |
| `limit_delta` | 30 | 10, 20, 25, 30 | zone half-width around 50 (30 → 80/20) |
| `ema_length` | 9 | — | EMA used as the price leg of divergence detection |

Default signal: `c_RSI_OVERBOUGHT_BULL`.

## How to use in stock investing

- **Mean-reversion (range-bound stocks)**: buy on `c_RSI_OVERSOLD_BULL` (exit from
  oversold), sell/trim on `c_RSI_OVERBOUGHT_BEAR`. Works poorly in strong trends —
  RSI can stay overbought for weeks in a real uptrend.
- **Trend filter**: only take long setups while RSI > 50 (`c_RSI_CENTRALLINE_BULL`
  marks the regime change).
- **Divergence warning**: `c_RSI_DIVERGENCE_BEAR` near a high suggests trimming or
  tightening stops rather than outright shorting.
- Shorter `length` (5–9) reacts faster but whipsaws more; pair with a smaller
  `limit_delta` only if you accept more signals.

## Example

```python
import cio.stock as s

sig = s.run_strategy("AAPL", "rsi", length=14, limit_delta=30)
today = sig.iloc[-1]

if today["c_RSI_OVERSOLD_BULL"] == 1:
    print("RSI exited oversold — candidate mean-reversion buy")
if today["c_RSI_DIVERGENCE_BEAR"] == 1:
    print("Bearish divergence — momentum fading, consider trimming")

# How long has the current overbought/oversold state lasted?
print(sig["f_RSI_OVERBOUGHTSOLD_CSLS"].tail())
```

## References

- https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/
- J. Welles Wilder, *New Concepts in Technical Trading Systems* (1978)
