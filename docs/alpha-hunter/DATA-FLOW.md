# Alpha Hunter — Data Flow

How data moves and transforms from external sources to a persisted, published
watchlist. This tracks **values** (the control-flow doc tracks calls).

## End-to-end data flow

```mermaid
flowchart LR
    subgraph Ext["External sources"]
        YF["Yahoo Finance<br/>(yfinance)"]
        FH["Finnhub<br/>(earnings surprises)"]
        CFG["config/alpha_universe.txt<br/>+ CIO_ALPHA_UNIVERSE"]
    end

    subgraph Fetch["Fetch + cache (cio.stock.data, cio.data)"]
        OHLCV["OHLCV frame<br/>Date,O,H,L,C,Volume"]
        FUND["fundamentals dict<br/>cap, fwd_eps, eps, fcf,<br/>rev_growth%"]
        SURP["surprises list<br/>[{period, beat}]"]
        UNI["ticker list[str]"]
    end

    YF --> OHLCV
    YF --> FUND
    FH --> SURP
    CFG --> UNI

    subgraph Transform["Pure transforms (per ticker)"]
        Q["quality.evaluate<br/>-> pass, fwd_eps_growth, rev, $vol"]
        E["earnings.evaluate<br/>-> earnings_score"]
        M["momentum.evaluate<br/>-> momentum_score, trend_score"]
        S["scoring.final_score<br/>-> final, revenue_score, volume_expansion"]
    end

    OHLCV --> Q
    OHLCV --> E
    OHLCV --> M
    OHLCV --> S
    FUND --> Q
    Q -->|"fwd_eps_growth"| E
    SURP --> E
    Q -->|"revenue_growth"| S
    M -->|"momentum, trend"| S
    E -->|"earnings"| S

    S --> CAND["candidate dict"]
    CAND --> AR["AlphaResult<br/>regime + sectors + candidates"]

    subgraph Persist["store.save_run"]
        AR --> TOP["select Final >= threshold<br/>(default 80)"]
        TOP --> WL["watchlist Alpha-yyyy-mm-dd<br/>(symbols)"]
        TOP --> RUNS["alpha_runs row"]
        TOP --> CANDS["alpha_candidates rows"]
    end

    WL --> DB[("SQLite cio.db")]
    RUNS --> DB
    CANDS --> DB
    DB --> READ["dashboard render_alpha<br/>/ telegram /watchlist"]
```

## Market-level vs ticker-level inputs

```mermaid
flowchart TB
    subgraph Market["Computed once per run"]
        QQQc["QQQ Close ~400d"] --> Regime["regime: GREEN/YELLOW/RED"]
        QQQc --> QR["qqq_ret_3m, qqq_ret_6m"]
        ETF["SMH/IGV/HACK/BOTZ closes"] --> SectRank["sector RS ranking"]
    end
    subgraph Ticker["Computed per ticker (uses market values)"]
        QR --> MomRS["momentum RS pass<br/>(ticker 3M/6M > QQQ)"]
        Regime --> Tag["candidate.regime tag"]
        SectRank --> SectTag["candidate.sector tag"]
    end
```

The two QQQ return values flow **out** of the market layer **into** every ticker's
momentum evaluation — that is the only cross-ticker dependency.

## Field-level lineage of a candidate

```mermaid
flowchart LR
    subgraph Sources
        d1["OHLCV.Close"]
        d2["OHLCV.Volume"]
        d3["fund.market_cap"]
        d4["fund.forward_eps, eps"]
        d5["fund.free_cash_flow"]
        d6["fund.revenue_growth_pct"]
        d7["surprises[].beat"]
        d8["QQQ returns"]
    end

    d1 --> mom["momentum_score"]
    d8 --> mom
    d1 --> trend["trend_score (50/150/200 MA)"]
    d4 --> fwdg["fwd_eps_growth"]
    fwdg --> earn["earnings_score"]
    d1 --> rev1["revision_signal (gap-up)"]
    rev1 --> earn
    d7 --> sur["surprise_score"]
    sur --> earn
    d6 --> revsc["revenue_score"]
    d2 --> volx["volume_expansion"]

    mom --> final["final = 0.30 mom + 0.20 trend +<br/>0.30 earn + 0.10 rev + 0.10 vol"]
    trend --> final
    earn --> final
    revsc --> final
    volx --> final

    d3 --> gate["quality gate (PASS/FAIL)"]
    d2 --> gate
    d6 --> gate
    fwdg --> gate
    d5 --> gate
    gate -->|"PASS only"| final
```

## Persisted schema (data at rest)

```mermaid
erDiagram
    alpha_runs ||--o{ alpha_candidates : "has"
    alpha_runs ||--o| watchlists : "publishes"
    watchlists ||--o{ watchlist_items : "contains"

    alpha_runs {
        int id PK
        text run_date
        text regime
        text regime_detail
        text sectors_json
        int candidate_count
        int universe_size
        int watchlist_id FK
        text watchlist_name
        text created_at
    }
    alpha_candidates {
        int run_id FK
        int rank
        text ticker
        text sector
        real momentum
        real trend
        real earnings
        real revenue_growth
        real fwd_eps_growth
        real surprise
        real volume_expansion
        real final
        int quality_pass
    }
    watchlists {
        int id PK
        text name
        int is_active
    }
    watchlist_items {
        int watchlist_id FK
        text symbol
        int position
    }
```

## Figures-firewall note

`alpha_runs` / `alpha_candidates` store a **point-in-time snapshot** of one scan
(scores, growth rates as measured at run time). This is intentional and distinct from
the live-prices firewall: the **published watchlist holds only symbols**, and its
prices are always fetched live by the watchlist price path — never read back from
these snapshot tables.
