# Indicator Visualization — Sequence Diagram

Runtime interaction between participants for the two primary scenarios: the agent
tool rendering a PNG (and returning it to both the user and the model), and the
dashboard rendering interactive HTML.

## Scenario A — agent tool renders a PNG and the model sees it

This is the path fixed in `conv_turns#229-232`: before the fix the tool returned
only success text, so the model was blind to its own chart. Now it returns the
image to the model AND queues it for Telegram.

```mermaid
sequenceDiagram
    actor User
    participant Bot as Telegram bot
    participant Agent as CIO agent
    participant Tool as stock_indicators
    participant API as render_indicators
    participant Core as build_spec
    participant Data as data + profiles + engine
    participant Mpl as mpl_plot
    participant Model as Claude model

    User->>Bot: "show LRCX swing indicators"
    Bot->>Agent: dispatch turn
    Agent->>Tool: call(symbol=LRCX, profile=swing)
    Tool->>API: render_indicators(LRCX, swing)
    API->>Core: build_spec(LRCX, swing)
    Core->>Data: load_or_download_stock_data
    Data-->>Core: OHLCV frame
    Core->>Data: PROFILES[swing].strategies
    Core->>Data: pandas_ta series per indicator
    Core->>Data: profile_signals -> verdicts
    Core->>Data: engine.run -> divergence flags
    Data-->>Core: series + verdicts + flags
    Core-->>API: ChartSpec
    API->>Mpl: render(ChartSpec)
    Mpl-->>API: PNG path
    API-->>Tool: PNG path
    Tool->>Tool: _emit_image_seen
    Tool->>Bot: queue path in _PENDING
    Tool-->>Agent: tool_result [text + image block]
    Agent->>Model: tool_result with image
    Model-->>Agent: reads chart, writes interpretation
    Agent-->>Bot: analysis text
    Bot-->>User: PNG photo + interpretation
```

## Scenario B — dashboard renders interactive HTML

```mermaid
sequenceDiagram
    actor Operator
    participant Dash as dashboard server
    participant Views as views.render_indicators_form
    participant API as render_indicators
    participant Core as build_spec
    participant Bokeh as bokeh_plot

    Operator->>Dash: GET /indicators?symbol=LRCX&profile=swing
    alt no symbol
        Dash->>Views: render_indicators_form
        Views-->>Dash: HTML form
        Dash-->>Operator: entry form
    else symbol present
        Dash->>API: render_indicators(LRCX, swing, html=True)
        API->>Core: build_spec
        Core-->>API: ChartSpec
        API->>Bokeh: render_html(ChartSpec)
        Bokeh-->>API: standalone HTML path
        API-->>Dash: HTML path
        Dash->>Dash: read file
        Dash-->>Operator: interactive bokeh page
    end
```

## Scenario C — committee PDF appendix

```mermaid
sequenceDiagram
    participant Deliv as delivery.produce_report
    participant API as render_indicators
    participant PDF as render_pdf.markdown_to_pdf
    participant Fig as _figures_html

    Deliv->>API: render_indicators(symbol, committee)
    API-->>Deliv: PNG path (best-effort)
    Deliv->>PDF: markdown_to_pdf(md, appendix_images=[(caption, png)])
    PDF->>Fig: _figures_html
    Fig->>Fig: base64-embed image
    Fig-->>PDF: figure HTML section
    PDF-->>Deliv: PDF with 技術指標 appendix
```

## Participant notes

- **build_spec** is the only component that talks to data/profiles/engine; the
  adapters are pure functions of `ChartSpec`.
- **`_emit_image_seen`** is what makes the model able to interpret its own output:
  the SDK forwards the MCP image block as a vision tool-result.
- **bokeh** is imported lazily; if absent, Scenario B's `render_html` raises a
  friendly `ImportError` while Scenario A (PNG) is unaffected.
