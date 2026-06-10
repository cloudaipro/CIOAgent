# ERI — Elder Ray Index (Bull/Bear Power)

**Engine key**: `eri` · **Module**: `eri_strategy.py` · **Inputs**: High, Low, Close

## Definition

`BullPower = High − EMA(Close, length)`; `BearPower = Low − EMA(Close, length)`.
Elder's "X-ray" of the trend: how far can bulls push the high above consensus
value (the EMA), and how far can bears drag the low below it.

## Interpretation

- **Bear power negative but rising while the EMA rises** — bears weakening inside
  an uptrend: Elder's classic **buy** setup.
- **Bull power positive but falling while price still rises** — bulls weakening:
  **sell/short** warning.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_ERI_DIVERGENCE_BULL` | event | EMA rising, bear power < 0 but rising, bull power rising, close down — bears exhausting on a dip |
| `c_ERI_DIVERGENCE_BEAR` | event | EMA rising, bull power > 0 but falling, bear power falling, close up — bulls exhausting into strength |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 13 | 5, 9, 13, 20 | EMA window (Elder's default 13) |

Default signal: `c_ERI_DIVERGENCE_BULL`.

## How to use in stock investing

- Both signals are **pullback/exhaustion timing inside an uptrend** (the EMA-up
  condition is built in): the bull signal buys weakness that is running out of
  sellers; the bear signal warns longs that buying pressure is fading despite new
  highs.
- Elder's full rule adds the higher-timeframe screen: only buy when the weekly
  trend is also up.
- Stops: Elder places buy-stops above the prior bar's high after the bull setup —
  entry only triggers if strength confirms.

## Example

```python
import cio.stock as s

sig = s.run_strategy("HD", "eri", length=13)
t = sig.iloc[-1]
if t["c_ERI_DIVERGENCE_BULL"] == 1:
    print("Elder Ray buy: bears weakening within uptrend")
```

## References

- https://www.whselfinvest.com/en-be/trading-platform/free-trading-signals/15-dr-alexander-elder-ray-bull-power-bear-power
- Alexander Elder, *Trading for a Living* (1993)
