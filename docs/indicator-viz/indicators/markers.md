# Markers: divergence + swings — chart reading

These annotations are not indicators of their own; they are overlaid on the price
panel and the oscillator panels to highlight structure and momentum/price conflicts.

## Divergence markers (▼ / ▲)

**How they are drawn**

- **Red ▼** — bearish divergence.
- **Green ▲** — bullish divergence.

They appear both on the relevant oscillator panel (MACD, RSI, …) and projected onto
the price panel at the same bar.

**Two sources**

1. **Committee/strategy flags** (default presets) — sourced from the same engine
   columns the committee uses (`c_<IND>_DIVERGENCE_BULL/BEAR`), so the chart matches
   the written analysis. This is what fires for RSI/MACD on LRCX in `conv_turns#210`.
2. **Geometric fallback** — a deterministic pivot detector compares the last two
   price highs/lows against the oscillator's pivots over the display window.

**How to read**

- **Bearish (▼)** — price makes a **higher high** while the oscillator makes a
  **lower high**: "價創新高、動能未跟上" (new price high, momentum did not follow). A
  warning the current up-leg is tiring — not an immediate sell, but a reason to
  tighten risk.
- **Bullish (▲)** — price makes a **lower low** while the oscillator makes a
  **higher low**: selling is exhausting; a turn up may be near.
- Divergence is a **warning**, not a trigger. Confirm with a cross or a level break
  before acting.

## Swing anchors (HH / HL / LH / LL)

**How they are drawn**

Small text labels at recent swing-high and swing-low pivots on the price panel,
capped to the last four highs and four lows in the window:

- **HH** — higher high, **HL** — higher low (up-structure)
- **LH** — lower high, **LL** — lower low (down-structure)

Pivots are found with the same deterministic local-extrema detector used for
geometric divergence.

**How to read**

- **HH + HL sequence** — classic up-trend structure (rising peaks and troughs); buy
  pullbacks to the HL.
- **LH + LL sequence** — down-trend structure; sell rallies to the LH.
- **Structure break** — the first **LH** after a run of HHs (or the first **HH** after
  LLs) is an early trend-change cue; pair it with a divergence ▼/▲ at the same area
  for a higher-conviction read.
- **Anchors as levels** — recent HH/LL prices double as the nearest
  resistance/support to watch for breakouts or rejections.

## Reference

- Divergence — concept covered in the RSI/MACD references (e.g. TradingView RSI:
  <https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/>).
- Market structure (higher highs / lower lows) — StockCharts ChartSchool, Trend
  analysis:
  <https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies>
