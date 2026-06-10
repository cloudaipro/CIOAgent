# CMF — Chaikin Money Flow

**Engine key**: `cmf` · **Module**: `cmf_strategy.py` · **Inputs**: High, Low, Close, Volume

## Definition

`CMF = Σ(MFV, length) / Σ(Volume, length)` where Money Flow Volume
`MFV = Volume × ((C − L) − (H − C)) / (H − L)`. It measures whether closes land
near the top (accumulation) or bottom (distribution) of each bar's range,
volume-weighted, over `length` bars. Range −1 to +1.

## Interpretation

- **CMF > +0.05 sustained** — buyers absorbing supply (accumulation).
- **CMF < −0.05 sustained** — distribution.
- The ±0.05 buffer (used by this strategy) filters the noise band around zero.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_CMF_ZEROCROSS_BULL` | event | CMF crossed up through +0.05 (accumulation begins) |
| `c_CMF_ZEROCROSS_BEAR` | event | CMF crossed down through −0.05 (distribution begins) |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 20 | 3, 5, 10, 15, 20, 30, 50 | flow accumulation window |

Default signal: `c_CMF_ZEROCROSS_BULL`.

## How to use in stock investing

- **Confirmation layer**: a price breakout with CMF already positive (and ideally
  a fresh `c_CMF_ZEROCROSS_BULL`) is institutionally supported; a breakout with
  CMF negative is suspect.
- **Divergence by inspection**: price grinding higher while CMF deteriorates
  toward zero often precedes breakdowns — check the raw trend of recent signal
  flips.
- Longer windows (30–50) describe the position-trade backdrop; 20 is the
  Chaikin standard.

## Example

```python
import cio.stock as s

sig = s.run_strategy("COST", "cmf", length=20)
if sig.iloc[-1]["c_CMF_ZEROCROSS_BULL"] == 1:
    print("Money flow turned positive — accumulation starting")
```

## References

- https://zhuanlan.zhihu.com/p/38045262
- Marc Chaikin, Chaikin Money Flow (1980s)
