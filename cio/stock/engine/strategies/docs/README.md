# Strategy Documentation Index

One document per strategy in `cio/stock/engine/strategies/`. Every strategy is run
through the engine the same way:

```python
import cio.stock as s

signals = s.run_strategy("AAPL", "<key>")          # fetch history (cached) + run
signals = s.run_strategy(df, "<key>", length=9)    # or run on your own OHLCV DataFrame
```

`signals` is a DataFrame aligned to the input index, one row per bar.

## Column naming conventions

| Prefix | Meaning |
|---|---|
| `c_` | Binary condition column — `1` on the bar where the condition holds, else `0`. |
| `f_` | Numeric feature column — oscillator value, count, or signed trend measure. |
| `*_BULL` / `*_BEAR` | Bullish / bearish event (crossover, exit from a zone, divergence). |
| `*_CSLS` | "Candles Since Last Signal/Swing" — bar count since the state last flipped, signed by direction (positive = bullish state, negative = bearish state). |

Rows inside the indicator warm-up window are `NaN`.

## Strategy catalogue

| Key | Indicator | Type | Inputs | Doc |
|---|---|---|---|---|
| `aberration` | Aberration volatility bands | Volatility/trend | H, L, C | [aberration.md](aberration.md) |
| `adobv` | Accum/Dist + On-Balance Volume combo | Volume divergence | H, L, C, V | [adobv.md](adobv.md) |
| `adosc` | Accum/Dist Oscillator (Chaikin) | Volume momentum | H, L, C, V | [adosc.md](adosc.md) |
| `awesome` | Awesome Oscillator saucers | Momentum pattern | H, L | [awesome.md](awesome.md) |
| `bop` | Balance of Power | Buy/sell pressure | O, H, L, C | [bop.md](bop.md) |
| `cci` | Commodity Channel Index | Momentum oscillator | H, L, C | [cci.md](cci.md) |
| `cfo` | Chande Forecast Oscillator | Forecast deviation | C | [cfo.md](cfo.md) |
| `cg` | Center of Gravity Oscillator | Low-lag oscillator | C | [cg.md](cg.md) |
| `cmf` | Chaikin Money Flow | Volume flow | H, L, C, V | [cmf.md](cmf.md) |
| `cmo` | Chande Momentum Oscillator | Momentum oscillator | C | [cmo.md](cmo.md) |
| `coppock` | Coppock Curve | Long-term momentum | C | [coppock.md](coppock.md) |
| `cti` | Correlation Trend Indicator | Trend strength | C | [cti.md](cti.md) |
| `efi` | Elder Force Index | Volume-weighted momentum | H, L, C, V | [efi.md](efi.md) |
| `er` | Kaufman Efficiency Ratio | Trend efficiency | C | [er.md](er.md) |
| `eri` | Elder Ray Index | Bull/bear power | H, L, C | [eri.md](eri.md) |
| `fisher` | Fisher Transform | Price-extreme oscillator | H, L | [fisher.md](fisher.md) |
| `inertia` | Dorsey Inertia | Trend confirmation | H, L, C | [inertia.md](inertia.md) |
| `kdj` | KDJ (stochastic + J line) | Momentum crossover | H, L, C | [kdj.md](kdj.md) |
| `kst` | Know Sure Thing | Multi-ROC momentum | C | [kst.md](kst.md) |
| `kvo` | Klinger Volume Oscillator | Volume trend | H, L, C, V | [kvo.md](kvo.md) |
| `macd` | MACD | Trend/momentum | C | [macd.md](macd.md) |
| `pgo` | Pretty Good Oscillator | Breakout oscillator | H, L, C | [pgo.md](pgo.md) |
| `ppo` | Percentage Price Oscillator | Trend/momentum (%) | C | [ppo.md](ppo.md) |
| `psl` | Psychological Line | Sentiment ratio | C | [psl.md](psl.md) |
| `pvo` | Percentage Volume Oscillator | Volume momentum | C, V | [pvo.md](pvo.md) |
| `pvt` | Price Volume Trend | Cumulative volume flow | C, V | [pvt.md](pvt.md) |
| `qqe` | Quantitative Qualitative Estimation | Smoothed-RSI system | C | [qqe.md](qqe.md) |
| `rsi` | Relative Strength Index | Momentum oscillator | C | [rsi.md](rsi.md) |
| `rsx` | Jurik RSX | Smoothed RSI | C | [rsx.md](rsx.md) |
| `rvgi` | Relative Vigor Index | Momentum crossover | O, H, L, C | [rvgi.md](rvgi.md) |
| `rvi` | Relative Volatility Index | Volatility direction | H, L, C | [rvi.md](rvi.md) |
| `squeeze` | TTM Squeeze (Pro) | Volatility compression | H, L, C | [squeeze.md](squeeze.md) |
| `stc` | Schaff Trend Cycle | Cycle-tuned MACD | C | [stc.md](stc.md) |
| `stoch` | Stochastic Oscillator | Momentum oscillator | H, L, C | [stoch.md](stoch.md) |
| `tdseq` | TD Sequential (setup count) | Exhaustion count | C | [tdseq.md](tdseq.md) |
| `trix` | TRIX | Triple-smoothed ROC | C | [trix.md](trix.md) |
| `tsi` | True Strength Index | Double-smoothed momentum | C | [tsi.md](tsi.md) |
| `uo` | Ultimate Oscillator | Multi-timeframe momentum | H, L, C | [uo.md](uo.md) |
| `vidya` | Variable Index Dynamic Average | Adaptive moving average | C | [vidya.md](vidya.md) |
| `willr` | Williams %R | Momentum oscillator | H, L, C | [willr.md](willr.md) |

## Shared building blocks (`ta_util.py`)

- **`over_bought_sold_signal`** — flags `OVERBOUGHT`/`OVERSOLD` zones plus the four
  zone-edge crossing events (`*_BULL` = crossing up through a threshold, `*_BEAR` =
  crossing down), optionally a central-line cross.
- **`crossover_signal`** — line-vs-signal-line cross events plus signed bar count
  since the cross (`CROSSOVER_CSLS`), optionally zero-line crossings.
- **`detect_divergence`** — swing-based comparison of a price EMA against the
  oscillator; flags price/indicator disagreement (`DIVERGENCE_BULL`/`BEAR`).
- **`detect_consolidation`** — Bollinger-inside-Keltner test used by `adosc`/`efi`
  to suppress signals during low-volatility chop.

## Parameter grids

Every strategy module exposes `<key>_grid_of_parameter`, an iterable of parameter
dicts for hyperparameter search, and most expose `default_<key>_signal`, the
column the module's author considered the primary signal.

## Error behavior

If input data is too short or degenerate (e.g. constant prices) for the underlying
indicator, the engine raises `ValueError` naming the strategy and row count.
`StrategyEngine.run_all()` maps such failures to the exception object per strategy.
