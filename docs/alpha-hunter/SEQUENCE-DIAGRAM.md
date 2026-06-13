# Alpha Hunter — Sequence Diagrams

Time-ordered interaction between participants for each trigger path. Complements the
control-flow doc (static call graph) with the dynamic ordering of messages.

## 1. Telegram `/alpha` (operator-triggered run + publish)

```mermaid
sequenceDiagram
    actor User
    participant Bot as cio.bot (cmd_alpha)
    participant Pkg as alpha.run_and_save
    participant Eng as engine.run
    participant Data as stock.data / finnhub
    participant Store as store.save_run
    participant WL as watchlist
    participant DB as SQLite

    User->>Bot: /alpha
    Bot-->>User: "Running Alpha Hunter… (may take a minute)"
    Bot->>Pkg: to_thread(run_and_save)
    Pkg->>Eng: run()
    Eng->>Data: QQQ OHLCV
    Data-->>Eng: close series
    Eng->>Eng: classify regime + qqq returns
    Eng->>Data: sector ETF closes
    Data-->>Eng: ranked sectors
    loop each ticker in universe
        Eng->>Data: OHLCV + fundamentals + surprises
        Data-->>Eng: frames / dicts (or None)
        Eng->>Eng: quality, earnings, momentum, scoring
    end
    Eng-->>Pkg: AlphaResult (ranked)
    Pkg->>Store: save_run(result)
    Store->>WL: find_by_name / create Alpha-<date>
    Store->>WL: set_symbols(Final >= threshold) + set_active
    WL->>DB: upsert watchlist_items
    Store->>DB: insert alpha_runs + alpha_candidates
    Store-->>Pkg: {run_id, watchlist_id, watchlist_name}
    Pkg-->>Bot: (result, meta)
    Bot->>Bot: report.format_telegram
    Bot-->>User: regime + Top candidates + "published Alpha-<date> (active)"
```

## 2. Dashboard "Run Alpha Hunter" button

```mermaid
sequenceDiagram
    actor Operator
    participant Browser
    participant Srv as dashboard.server
    participant Pkg as alpha.run_and_save
    participant Views as views.render_alpha
    participant Store as store
    participant DB as SQLite

    Operator->>Browser: click "Run Alpha Hunter"
    Browser->>Srv: POST /alpha (action=run_hunter)
    Srv->>Pkg: run_and_save()
    Pkg-->>Srv: (result, meta)
    Srv-->>Browser: 303 redirect /alpha?msg=...
    Browser->>Srv: GET /alpha
    Srv->>Store: latest_run() + list_runs()
    Store->>DB: SELECT runs + candidates
    DB-->>Store: rows
    Store-->>Srv: latest, runs
    Srv->>Views: render_alpha(latest, runs)
    Views-->>Browser: HTML (regime light, sectors, candidates, history)
```

## 3. Conversational: "run alpha hunter then add TSLA"

Shows the agent tools that let Telegram **operate** on the published list, not just
read it.

```mermaid
sequenceDiagram
    actor User
    participant Bot as cio.bot (agent)
    participant Tool1 as tool run_alpha_hunter
    participant Tool2 as tool watchlist_add
    participant Pkg as alpha.run_and_save
    participant WL as watchlist
    participant DB as SQLite

    User->>Bot: "run alpha hunter, then add TSLA to it"
    Bot->>Tool1: run_alpha_hunter()
    Tool1->>Pkg: to_thread(run_and_save)
    Pkg->>WL: publish Alpha-<date> (active)
    WL->>DB: write items
    Pkg-->>Tool1: (result, meta)
    Tool1-->>Bot: regime + selected names + published name
    Bot->>Tool2: watchlist_add(symbol="TSLA")
    Tool2->>WL: resolve active list -> add_symbol
    WL->>DB: insert TSLA
    Tool2-->>Bot: "Added TSLA on 'Alpha-<date>'."
    Bot-->>User: summary + confirmation
```

## 4. CLI run

```mermaid
sequenceDiagram
    actor Dev
    participant Main as __main__.main
    participant Eng as engine.run
    participant Store as store.save_run

    Dev->>Main: python -m cio.alpha [--no-publish] [--json]
    Main->>Eng: run(universe_path)
    Eng-->>Main: AlphaResult
    alt --no-publish
        Main->>Main: meta = nulls
    else publish (default)
        Main->>Store: save_run(result)
        Store-->>Main: meta
    end
    Main-->>Dev: table or JSON (+ published watchlist line)
```

## 5. Same-day re-run (idempotency)

```mermaid
sequenceDiagram
    participant Store as store.publish_watchlist
    participant WL as watchlist
    participant DB as SQLite

    Note over Store: second run on the same date
    Store->>WL: find_by_name("Alpha-2026-06-12")
    WL->>DB: SELECT by name
    DB-->>WL: existing row (id=7)
    WL-->>Store: found id=7
    Store->>WL: set_symbols(id=7, new selection)
    WL->>DB: DELETE old items, INSERT new (^IXIC first)
    Store->>WL: set_active(id=7)
    Note over Store,DB: same list refreshed in place — no duplicate Alpha-<date>
```
