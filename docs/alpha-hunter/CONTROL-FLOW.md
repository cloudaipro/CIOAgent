# Alpha Hunter — Control Flow

Where control goes (function calls, loops, branches) during a run. This is the
caller→callee view, complementing the data-flow doc (which tracks the values).

## Call graph (who calls whom)

```mermaid
flowchart TB
    subgraph Triggers
        T1["CLI __main__.main()"]
        T2["dashboard _alpha_post()"]
        T3["bot.cmd_alpha()"]
        T4["agent tool t_run_alpha_hunter()"]
    end

    RAS["alpha.run_and_save()"]
    RUN["engine.run()"]
    SAVE["store.save_run()"]

    T1 --> RUN
    T1 --> SAVE
    T2 --> RAS
    T3 --> RAS
    T4 --> RAS
    RAS --> RUN
    RAS --> SAVE

    RUN --> U["universe.load()"]
    RUN --> QC["regime._qqq_close()"]
    RUN --> RC["regime.classify()"]
    RUN --> SR["sectors.rank()"]
    RUN --> ET["_evaluate_ticker() (loop)"]

    ET --> OH["_ohlcv()"]
    ET --> FN["fundamentals_fn()"]
    ET --> QV["quality.evaluate()"]
    ET --> SF["surprises_fn()"]
    ET --> EV["earnings.evaluate()"]
    ET --> MV["momentum.evaluate()"]
    ET --> SC["scoring.final_score()"]

    SAVE --> PW["publish_watchlist()"]
    PW --> WF["watchlist.find_by_name()"]
    PW --> WC["watchlist.create()"]
    PW --> WS["watchlist.set_symbols()"]
    PW --> WA["watchlist.set_active()"]
    SAVE --> DBW["DB insert (alpha_runs/candidates)"]
```

## engine.run() control flow

```mermaid
flowchart TB
    Start(["engine.run(universe_path, fetch, fundamentals_fn, surprises_fn)"]) --> Def["Default any None fetcher to the live data layer"]
    Def --> Date["run_date = today"]
    Date --> QQQ["qqq_close = regime._qqq_close(fetch)"]
    QQQ --> Reg["reg = regime.classify(qqq_close)"]
    Reg --> Ret["qqq_r3 / qqq_r6 = ret_pct(qqq_close) if present else None"]
    Ret --> Sect["sect = sectors.rank(fetch)"]
    Sect --> Syms["syms = universe.load(...)"]
    Syms --> Loop{"for sym in syms"}
    Loop -- next --> Eval["cand = _evaluate_ticker(...)"]
    Eval --> Null{"cand is None?<br/>(no OHLCV)"}
    Null -- yes --> Loop
    Null -- no --> Append["candidates.append(cand)"]
    Append --> Loop
    Loop -- done --> Rank["ranked = sorted(passing, key=final, desc)"]
    Rank --> Assign["assign rank 1..n"]
    Assign --> Return(["return AlphaResult"])
```

## Exception-handling boundaries

Every external call is wrapped so the funnel never raises. Control always continues
to the next step with a degraded value.

```mermaid
flowchart TB
    subgraph Guarded["try/except -> safe default"]
        G1["_qqq_close() -> None"]
        G2["sectors.rank(): per-ETF -> skip"]
        G3["_ohlcv() -> None (ticker dropped)"]
        G4["fundamentals_fn() -> {} (quality fails closed)"]
        G5["surprises_fn() -> None (surprise score 0)"]
        G6["surface handlers (dashboard/bot) -> flash/reply error, no 500/crash"]
    end
    Note["Result: a run with no network still returns<br/>regime=UNKNOWN, candidates=[]"]
    Guarded --> Note
```

## Loop & complexity

```mermaid
flowchart LR
    N["universe size N<br/>(default ~40)"] --> Cost["per ticker:<br/>1 OHLCV + 1 fundamentals + 1 surprises"]
    Cost --> Market["+ regime QQQ (1)<br/>+ sectors (5 ETFs)"]
    Market --> Total["O(N) network fetches,<br/>cached after first run"]
    Total --> Bound["Run time bounded by N<br/>(universe is the throttle)"]
```

- The per-ticker loop is sequential and deterministic (stable rank ordering).
- Fetches are cached per symbol (`cio.stock.data`), so repeat runs are fast.
- The dashboard runs this synchronously; the bot offloads it to a worker thread
  (`asyncio.to_thread`) so the event loop stays responsive.
