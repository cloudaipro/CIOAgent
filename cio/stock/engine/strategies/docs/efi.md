# EFI — Elder Force Index

**Engine key**: `efi` · **Module**: `efi_strategy.py` · **Inputs**: High, Low, Close, Volume

## Definition

`Force = (Close_t − Close_{t−drift}) × Volume`, smoothed with an EMA of `length`.
Alexander Elder's "force" combines direction, extent, and volume of a move into
one number: big price change on big volume = strong force.

## Interpretation

- **EFI > 0** — bulls in control of the smoothed horizon; **< 0** — bears.
- **Zero cross** — control changing hands.
- This strategy suppresses zero-cross signals during detected consolidation
  (Bollinger-inside-Keltner squeeze), where crosses are mostly noise.

## Output columns

| Column | Type | Meaning |
|---|---|---|
| `c_EFI_ZEROCROSS_BULL` | event | EFI crossed above zero outside consolidation |
| `c_EFI_ZEROCROSS_BEAR` | event | EFI crossed below zero outside consolidation |

## Parameters

| Name | Default | Grid | Notes |
|---|---|---|---|
| `length` | 13 | 5, 9, 13, 20 | EMA smoothing (Elder's default 13) |
| `drift` | 1 | 1–5 | price-change lookback |
| `consolidation_len` | 10 | 3, 5, 7, 10, 15, 20 | window for the squeeze filter |
| `std` / `scalar` | 2.0 / 1.2 | — | Bollinger std and Keltner scalar for the filter |

Default signal: `c_EFI_ZEROCROSS_BULL`.

## How to use in stock investing

- **Elder's own system (Triple Screen)**: use the 13-EMA Force Index zero cross
  in the direction of the higher-timeframe trend — long on `c_EFI_ZEROCROSS_BULL`
  only when the weekly trend is up.
- The built-in consolidation filter already removes the lowest-quality signals;
  remaining crosses during expansion phases carry real information.
- A 2-bar Force Index (set `length=2` mentally — here use small `length`) is
  Elder's pullback-entry timing tool inside trends.

## Example

```python
import cio.stock as s

sig = s.run_strategy("JPM", "efi", length=13)
if sig.iloc[-1]["c_EFI_ZEROCROSS_BULL"] == 1:
    print("Force Index positive outside consolidation — bulls took control")
```

## References

- https://blog.xcaldata.com/enhance-your-approach-using-the-elder-force-index-efi-to-understand-market-trends/
- Alexander Elder, *Trading for a Living* (1993)
