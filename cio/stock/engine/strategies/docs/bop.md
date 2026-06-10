# BOP — Balance of Power

**Engine key**: `bop` · **Module**: `bop_strategy.py` · **Inputs**: Open, High, Low, Close

## Definition

`BOP = (Close − Open) / (High − Low)` — where the bar closed relative to where it
opened, scaled by the bar's range. Range −1 to +1 per bar: +1 = opened at the low,
closed at the high (buyers ran the whole session). Igor Livshin's measure of which
side controlled each bar.

## Interpretation

- **BOP > 0** — buyers won the bar; **< 0** — sellers won.
- Noisy bar-to-bar; meaningful when combined with trend context. This strategy
  gates it with an SMA trend filter.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_BOP_TREND_UP` | state | close above SMA **and** BOP > 0 — uptrend with buyer-controlled bar |
| `c_BOP_TREND_DOWN` | state | close below SMA **and** BOP < 0 — downtrend with seller-controlled bar |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `sma_length` | 10 | 3, 5, 7, 10, 15, 20 | trend-filter SMA |

Default signal: `c_BOP_TREND_UP`.

## How to use in stock investing

- **Trend-quality check**: a healthy uptrend shows `c_BOP_TREND_UP` on most bars;
  an uptrend where BOP keeps flipping negative is being sold into — caution.
- Use the *proportion* of recent bars with the signal as a conviction feature
  (e.g. `sig["c_BOP_TREND_UP"].tail(20).mean()`).
- As an entry trigger alone it is weak; treat it as a confirmation/filter layer
  for other signals.

## Example

```python
import cio.stock as s

sig = s.run_strategy("V", "bop", sma_length=10)
conviction = sig["c_BOP_TREND_UP"].tail(20).mean()
print(f"buyer-controlled uptrend bars over last 20: {conviction:.0%}")
```

## References

- https://medium.com/@mkrt.crypto.arsenal/該買-該賣-來問技術指標-14-bop-能量均衡指標指標-c3ca5192c552
- Igor Livshin, "Balance of Power", *Stocks & Commodities* (2001)
