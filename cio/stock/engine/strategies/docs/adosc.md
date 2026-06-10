# ADOSC — Chaikin Accumulation/Distribution Oscillator

**Engine key**: `adosc` · **Module**: `adosc_strategy.py` · **Inputs**: High, Low, Close, Volume

## Definition

`ADOSC = EMA(AD, fast) − EMA(AD, slow)` — the Chaikin Oscillator: MACD logic
applied to the cumulative Accumulation/Distribution line. Positive = money flow
accelerating in; negative = accelerating out.

## Interpretation

- **Zero cross up** — money-flow momentum turned positive (accumulation
  accelerating).
- **Zero cross down** — distribution accelerating.
- This strategy only emits crosses that occur **outside consolidation**
  (Bollinger-inside-Keltner filter) — flow crosses inside a squeeze are noise.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_ADOSC_ZEROCROSS_BULL` | event | ADOSC crossed above zero, market not consolidating |
| `c_ADOSC_ZEROCROSS_BEAR` | event | ADOSC crossed below zero, market not consolidating |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `fast` | 3 | 3 | fast EMA (Chaikin standard) |
| `slow` | 10 | 5, 7, 10 | slow EMA |
| `std` / `scalar` | 2.0 / 1.2 | — | consolidation-filter settings |

Default signal: `c_ADOSC_ZEROCROSS_BULL`.

## How to use in stock investing

- **Pre-breakout confirmation**: institutions accumulate before price moves —
  `c_ADOSC_ZEROCROSS_BULL` ahead of or at a resistance test raises breakout odds.
- Use as the volume leg of a two-factor entry: price signal (e.g. `cci` breakout)
  + ADOSC positive.
- Fast (3/10) settings make it twitchy on its own — its value is confirmation,
  not standalone trading.

## Example

```python
import cio.stock as s

sig = s.run_strategy("BAC", "adosc")
if sig.iloc[-1]["c_ADOSC_ZEROCROSS_BULL"] == 1:
    print("Chaikin oscillator positive outside consolidation")
```

## References

- Marc Chaikin, Chaikin Oscillator
- https://school.stockcharts.com/doku.php?id=technical_indicators:chaikin_oscillator
