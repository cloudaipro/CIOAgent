# TDSEQ — TD Sequential (Setup Count)

**Engine key**: `tdseq` · **Module**: `tdseq_strategy.py` · **Inputs**: Close

## Definition

Tom DeMark's Sequential *setup* phase: count consecutive bars whose close is
higher (sell setup) or lower (buy setup) than the close **4 bars earlier**. A
completed count of **9** flags trend exhaustion. (The engine implements the count
locally — `_td_seq` — since pandas_ta 0.4 removed `td_seq`; the full Sequential
"countdown" phase is not implemented.)

## Interpretation

- **9 consecutive closes below close[−4]** — buy setup complete: the decline is
  statistically stretched, bounce odds elevated.
- **9 consecutive closes above close[−4]** — sell setup complete.
- DeMark counts are *anticipatory*: they fire into strength/weakness, not after
  confirmation.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_TDSEQ_DOWN_BULL` | event | down-count reached 9 (buy setup completed this bar) |
| `c_TDSEQ_UP_BEAR` | event | up-count reached 9 (sell setup completed this bar) |

## Parameters

None exposed (the 4-bar lag and 9-count are DeMark's fixed definition).
Default signal: `c_TDSEQ_DOWN_BULL`.

## How to use in stock investing

- **Exhaustion fade**: a 9 buy-setup in a stock at support is a countertrend
  long candidate — DeMark practitioners require price to hold the setup bar's
  low ("TDST level") as validation.
- **Profit-taking timer**: a 9 sell-setup in a winner you hold is a rational
  trim point even if you don't fade trends.
- Counts work on any timeframe; weekly 9s carry more weight than daily.
- Respect the limitation: this is the setup phase only — classic Sequential
  waits for a further 13-bar countdown before full reversal signals.

## Example

```python
import cio.stock as s

sig = s.run_strategy("TSM", "tdseq")
t = sig.iloc[-1]
if t["c_TDSEQ_DOWN_BULL"] == 1:
    print("TD buy setup 9 completed — exhaustion bounce candidate")
if t["c_TDSEQ_UP_BEAR"] == 1:
    print("TD sell setup 9 — consider trimming")
```

## References

- https://demark.com/sequential-indicator/
- https://trendspider.com/learning-center/td-sequential-a-comprehensive-guide-for-traders/
- https://tradingcenter.org/index.php/learn/technical-analysis/328-how-to-trade-td-sequential
- Tom DeMark, *The New Science of Technical Analysis* (1994)
