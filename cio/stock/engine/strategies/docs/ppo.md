# PPO — Percentage Price Oscillator

**Engine key**: `ppo` · **Module**: `ppo_strategy.py` · **Inputs**: Close

## Definition

`PPO = 100 × (EMA(fast) − EMA(slow)) / EMA(slow)` — MACD expressed as a percentage
of price, plus the usual signal line and histogram. Because it is normalized, PPO
values are comparable across stocks of different prices and across time.

## Interpretation

Identical logic to MACD (line vs signal, zero line, histogram momentum) with one
advantage: a PPO of 2 means the fast EMA is 2% above the slow EMA on *any* stock —
so thresholds and screens transfer across the watchlist.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `f_PPO_HISTOGRAM_CSLS` | count | signed bars since the histogram swing flipped |
| `c_PPO_CROSSOVER_BULL` / `_BEAR` | event | PPO crossed its signal line up / down |
| `f_PPO_CROSSOVER_CSLS` | count | signed bars since the line cross |
| `c_PPO_ZEROCROSS_BULL` / `_BEAR` | event | PPO crossed zero up / down |
| `c_PPO_DIVERGENCE_BULL` / `_BEAR` | event | price-EMA vs PPO swing divergence |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 12 | 3, 8, 12 | fast EMA |
| `slow` | 26 | 17, 21, 26 | slow EMA |
| `signal` | 9 | 5, 9 | signal EMA; also divergence price-EMA |

Grid restricted to classic combos (12/26/9, 8/21/5, 3/17/5).
Default signal: `c_PPO_CROSSOVER_BULL`.

## How to use in stock investing

- Use exactly like MACD for timing; prefer PPO when **comparing or ranking many
  tickers** (the CIO screening use case) since values are scale-free.
- Cross-sectional screen example: long candidates = PPO > 0 with fresh
  `c_PPO_CROSSOVER_BULL`, ranked by PPO magnitude.
- `f_PPO_CROSSOVER_CSLS` gives signal age — useful to skip stale crossovers when
  a scan runs weekly.

## Example

```python
import cio.stock as s

for sym in ["AAPL", "MSFT", "NVDA"]:
    sig = s.run_strategy(sym, "ppo")
    t = sig.iloc[-1]
    if t["c_PPO_ZEROCROSS_BULL"] == 1:
        print(sym, "PPO turned positive — comparable across all three")
```

## References

- https://trendspider.com/learning-center/the-percentage-price-oscillator-ppo-an-overview/
