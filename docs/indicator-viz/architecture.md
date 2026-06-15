# Indicator Visualization — Architecture

Technical report for the `cio/stock/viz` package (指標視覺化). The module closes
the gap recorded in `conv_turns#210`: the stock panel showed price + fundamentals
but no indicator overlay, so users were sent to TradingView to apply RSI / MACD /
KDJ and read divergence by hand. The viz package renders those indicators — with
divergence, swing, and squeeze markers — as an image for chat + PDF, and as an
interactive HTML page for the dashboard.

## Design principle — one core, two adapters

The package follows the old AI4StockMarket `AutoPlot` contract but refactored for
KISS/DRY: a single backend-agnostic **core** builds a `ChartSpec` from a generic,
typed `indicators` dict; two thin **render adapters** turn that spec into a PNG
(matplotlib) or interactive HTML (bokeh). No indicator is special-cased in the
engine — RSI/MACD/KDJ/Squeeze/etc. are just entries in the dict, produced by a
registry. The two backends share everything upstream of drawing; only the final
draw layer differs (inherent to wanting both a static image and an interactive
page).

```mermaid
flowchart TB
    subgraph Callers["Caller surfaces"]
        A1["Agent tool<br/>stock_indicators"]
        A2["stock_panel<br/>with_indicators"]
        A3["Committee PDF<br/>delivery.py"]
        A4["Dashboard<br/>/indicators route"]
    end

    subgraph Public["Public API"]
        P["stock.render_indicators<br/>symbol, profile, html?"]
    end

    subgraph Core["Shared core — spec.py"]
        B["build_spec"]
        R["_REGISTRY<br/>name to series builder"]
        D["default_indicator_dict<br/>profile preset"]
        T["_spec_from_dict<br/>generic translator"]
        DIV["_strategy_divergence<br/>+ divergence_markers"]
        SW["_swing_anchors"]
        CS["ChartSpec<br/>backend-agnostic"]
    end

    subgraph Adapters["Render adapters"]
        M["mpl_plot.render<br/>to PNG"]
        H["bokeh_plot.render_html<br/>to HTML"]
        S["style.py<br/>tokens + candlestick"]
    end

    subgraph Data["Data + signal sources"]
        DS["data.load_or_download_stock_data"]
        PR["profiles.PROFILES<br/>+ profile_signals"]
        EN["engine strategies<br/>get_engine"]
        TA["pandas_ta<br/>df.ta.*"]
    end

    A1 --> P
    A2 --> P
    A3 --> P
    A4 --> P
    P --> B
    B --> DS
    B --> PR
    B --> D
    D --> R
    R --> TA
    B --> T
    B --> DIV
    DIV --> EN
    B --> SW
    T --> CS
    B --> CS
    CS --> M
    CS --> H
    M --> S
    H --> S
    M --> OUT["PNG file"]
    H --> HTML["HTML file"]
```

## Component responsibilities

| Component | File | Responsibility |
|---|---|---|
| Public facade | `stock/__init__.py` `render_indicators` | PNG by default, HTML when `html=True`; documents the dict contract |
| Package facade | `viz/__init__.py` | `render_indicator_png` / `render_indicator_html` / `build_spec` / `bokeh_available` |
| Shared core | `viz/spec.py` | Resolve data + indicators dict, compute series, divergence, swings, trim, emit `ChartSpec` |
| Indicator registry | `viz/spec.py` `_REGISTRY` | Map each strategy name to a pandas_ta series builder expressed in the dict contract |
| Translator | `viz/spec.py` `_spec_from_dict` | Generic dict to overlays / bands / panels / flags, honoring `over_cap` / `below_cap` |
| PNG adapter | `viz/mpl_plot.py` | Draw `ChartSpec` with matplotlib (Agg, headless) |
| HTML adapter | `viz/bokeh_plot.py` | Draw `ChartSpec` with bokeh (interactive, optional dep) |
| Style | `viz/style.py` | Design tokens (mirrors `panel.py`) + candlestick helper |

## Data model — ChartSpec

`ChartSpec` is the contract between core and adapters. Both adapters read it; neither
recomputes anything.

```mermaid
classDiagram
    class ChartSpec {
        +str symbol
        +DataFrame df
        +list~Band~ price_bands
        +list~Line~ price_overlays
        +list~Marker~ price_markers
        +list~Flag~ price_flags
        +list swings
        +list~Panel~ panels
        +dict verdicts
        +str composite
        +str profile
        +str asof
        +int n
    }
    class Panel {
        +str name
        +list~Line~ lines
        +tuple hist
        +list~str~ hist_colors
        +list dots
        +list~HLine~ hlines
        +list~Marker~ markers
        +list~Flag~ flags
        +tuple ylim
        +str verdict
    }
    class Line {
        +str label
        +ndarray values
        +str color
        +float width
    }
    class Band {
        +str label
        +ndarray upper
        +ndarray lower
        +ndarray mid
        +str color
        +str style
        +float fill_alpha
    }
    class Marker {
        +int x0
        +int x1
        +float y0
        +float y1
        +str kind
        +str label
    }
    class Flag {
        +int x
        +str kind
        +str label
    }
    ChartSpec "1" o-- "many" Panel
    ChartSpec "1" o-- "many" Line
    ChartSpec "1" o-- "many" Band
    ChartSpec "1" o-- "many" Marker
    ChartSpec "1" o-- "many" Flag
    Panel "1" o-- "many" Line
```

## The indicators dict contract

Placement is decided by an entry's `type`:

| Group | Types | Drawn |
|---|---|---|
| Over-chart | `over`, `MA`, `bands`, `Swings`, `flags` | On the price panel |
| Below-chart | `below`, `RSI`, `MACD`, `multi`, `Crossover`, `threshold`, `squeeze` | In a stacked sub-panel |

`default_indicator_dict` produces this dict from a profile's strategy list; callers
may also pass any dict of their own (full AutoPlot-style flexibility).

## Dependencies

Required: `matplotlib`, `pandas`, `pandas_ta`. Optional: `bokeh` (HTML only — no
selenium; PNG export is matplotlib's job). The engine strategies and `profiles`
are reused for divergence flags and verdicts.
