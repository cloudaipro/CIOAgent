# Indicator Visualization — Control Flow

Function-level control flow through `build_spec` and the render adapters. Where the
activity diagram shows *what happens*, this shows *which function calls which*, with
the branch conditions and the guards that keep a render from failing.

## Call graph

```mermaid
flowchart TD
    RI["stock.render_indicators(symbol, profile, html)"] --> CHK{"html?"}
    CHK -->|"no"| RPNG["viz.render_indicator_png"]
    CHK -->|"yes"| RHTML["viz.render_indicator_html"]
    RPNG --> MR["mpl_plot.render"]
    RHTML --> IMPORT{"import bokeh_plot"}
    IMPORT -->|"ImportError"| RAISE["raise friendly ImportError<br/>install bokeh"]
    IMPORT -->|"ok"| BR["bokeh_plot.render_html"]

    MR --> BS["build_spec"]
    BR --> BS

    BS --> C1["_coerce_ohlc"]
    BS --> C2["resolve indicators dict"]
    C2 --> C2a["default_indicator_dict"]
    C2a --> REG["_REGISTRY[name] or _generic_entry"]
    REG --> TA["df.ta.<indicator>()"]
    BS --> C3["profile_signals (try)"]
    BS --> C4["_spec_from_dict"]
    C4 --> BP["_build_panel / _squeeze_panel"]
    C4 --> AL["_align / _bool_positions"]
    BS --> C5["_strategy_divergence (try)"]
    C5 --> ENG["get_engine().run(name)"]
    BS --> C6["_swing_anchors"]
    C6 --> PIV["_pivots"]
    BS --> C7["divergence_markers"]
    C7 --> PIV
    BS --> SPEC["return ChartSpec"]

    MR --> RP["_render_price"]
    MR --> RPan["_render_panel (per panel)"]
    RP --> CAN["style.candlestick"]
    RP --> DF["_draw_flags / _draw_markers"]
    RPan --> DF
```

## Branch + guard table

| Location | Condition | True branch | False branch |
|---|---|---|---|
| `render_indicators` | `html` | `render_indicator_html` | `render_indicator_png` |
| `render_indicator_html` | `import bokeh_plot` fails | raise friendly `ImportError` | call `render_html` |
| `build_spec` | `isinstance(symbol_or_df, DataFrame)` | use frame directly | `load_or_download_stock_data` |
| `_coerce_ohlc` | OHLC columns missing / empty | `raise ValueError` | continue |
| `build_spec` | `indicators is None` | profile preset from `PROFILES` | next branch |
| `build_spec` | `isinstance(indicators, dict)` | verbatim, `auto_divergence=False` | names-list preset |
| `default_indicator_dict` | name in `_REGISTRY` | registry builder | `_generic_entry` fallback |
| `default_indicator_dict` | `squeeze` in names | add Bollinger + Keltner | skip |
| `_spec_from_dict` | `type in _OVER_TYPES` | overlay / band / flag / swing | sub-panel |
| `_spec_from_dict` | `n_over/n_below >= cap` | skip entry | render entry |
| `build_spec` | `auto_divergence and not user dict` | run strategy + geometric divergence | skip both |
| `_render_panel` | `panel.lines` empty | no legend (histogram-only) | draw legend |

## Failure isolation

The renderer must never crash a chat turn or a committee PDF. Failure handling is
layered:

```mermaid
flowchart LR
    subgraph Hard["Hard failures — raise"]
        H1["missing OHLC columns"]
        H2["empty price frame"]
        H3["bokeh requested but absent"]
    end
    subgraph Soft["Soft failures — degrade silently"]
        S1["profile_signals error -> no verdicts"]
        S2["a single indicator builder error -> skip that entry"]
        S3["engine divergence error -> no flags"]
        S4["swing detection error -> no anchors"]
        S5["geometric divergence error -> no markers"]
    end
    subgraph Caller["Caller-level guards"]
        G1["agent tool try/except -> error text"]
        G2["delivery appendix try/except -> PDF without chart"]
        G3["dashboard try/except -> form with error message"]
    end
    Hard --> Caller
    Soft --> SPEC2["still returns a valid ChartSpec"]
```

Only the three hard failures propagate; every signal-layer step is wrapped so a bad
indicator degrades the chart rather than breaking it. Each caller surface adds its
own try/except so even a hard failure becomes a graceful message, not a crash.
