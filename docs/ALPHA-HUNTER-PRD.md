# Alpha Hunter — PRD

Derived from `Alpha_Hunter_Proposal.md`. This document is the build contract; the
proposal stays the product-vision source. Owner: solo operator. Author: Arch.

## 1. Summary

Alpha Hunter is a **repeatable, deterministic NASDAQ swing-selection engine**. It
walks a fixed funnel — Market → Sector → Quality → Earnings → Momentum → Ranking —
over a configurable NASDAQ universe and emits a **Top-20 watchlist** of names the
market is re-pricing higher.

It does **not** predict, trade, or call an LLM. It is a compute layer over data we
already fetch (`cio.stock.data` for OHLCV/fundamentals, `cio.data.finnhub` for
earnings). Zero model tokens per run — same cost discipline as TIRF.

## 2. Scope

In: the five analysis layers (FR-001…FR-005), the watchlist generator (FR-006),
a dashboard tab, a Telegram command, and agent tools so Telegram can operate on the
resulting list. Out (per proposal §3): auto-ordering, real-time trading, options,
earnings-summary generation, and any AI agent inside the funnel.

## 3. Architecture

```
Layer 0  Market Regime   (regime.py)   QQQ 50/200MA + slope -> GREEN/YELLOW/RED
Layer 1  Sector Ranking  (sectors.py)  QQQ SMH IGV HACK BOTZ -> RS score
Layer 2  Quality Filter  (quality.py)  cap / $vol / rev / fwdEPS / FCF -> PASS/FAIL
Layer 2.5 Earnings       (earnings.py) fwd EPS growth + revision + surprise
Layer 3  Momentum        (momentum.py) RS vs QQQ + trend template
Layer 4  Ranking         (scoring.py)  weighted final score -> Top 20
         Watchlist        (store.py)    publish "Alpha-yyyy-mm-dd"
```

`engine.py` orchestrates; `universe.py` supplies the candidate tickers;
`store.py` persists runs + publishes the watchlist; `__main__.py` is the CLI.

All functions are **offline-safe**: missing data degrades a ticker to a failed/
low-scored candidate, never an exception. A run with no network (no yfinance) still
returns a structured result (regime UNKNOWN, empty candidates).

## 4. Functional requirements

### FR-001 Market Regime (Layer 0)
Input QQQ daily. Indicators 50MA, 200MA, 50MA slope (10-bar).
- GREEN: QQQ > 50MA AND 50MA > 200MA AND slope up.
- RED: QQQ < 200MA.
- YELLOW: otherwise (below 50MA but above 200MA / mixed).
Output `{status, qqq, ma50, ma200, slope_up, detail}`.

### FR-002 Sector Ranking (Layer 1)
Universe QQQ SMH IGV HACK BOTZ. `RS = 0.5*ret_3m + 0.5*ret_6m`. Output ranked list
`[{ticker, ret_3m, ret_6m, rs}]`. A stock's sector RS percentile feeds nothing
hard-gated in v1 — it is reported context (sector tag on each candidate).

### FR-003 Quality Filter (Layer 2)
Minimums (ALL required to PASS): market cap > $2B; avg daily $-volume > $50M
(20-day, from OHLCV); revenue growth > 15%; forward EPS growth > 15%; free cash
flow > 0. A field that can't be fetched fails closed (FAIL) — we never pass on
missing data. Output PASS/FAIL + the measured values.

### FR-003A Earnings Engine (Layer 2.5)
`Earnings Score = 0.40*fwd_eps_component + 0.40*revision_signal + 0.20*surprise`.
- Forward EPS growth (40%): scaled 0..100, full credit at >=15% rising with growth.
- EPS revision (40%, Lite mode): **earnings gap-up > 5% not filled within 10
  trading days** = 100, else 0. (Pro mode = analyst consensus revision; deferred.)
- Surprise (20%): last-4-quarter beat ratio -> 100/75/50/25/0. From finnhub
  earnings surprises; 0 when finnhub disabled (fails soft, not a hard block).
Output `{earnings_score, fwd_eps_growth, revision_signal, surprise_score}`.

### FR-004 Momentum Engine (Layer 3)
Relative strength: 3M return > QQQ AND 6M return > QQQ.
Trend template: price > 50MA, 50MA > 150MA, 150MA > 200MA.
`Momentum Score` from RS magnitude; `Trend Score` = fraction of trend conditions met
(0/33/66/100). Output `{momentum_score, trend_score, rs_pass, ret_3m, ret_6m}`.

### FR-005 Candidate Ranking (Layer 4)
`Final = 0.30*Momentum + 0.20*Trend + 0.30*Earnings + 0.10*RevenueGrowth(scaled)
+ 0.10*VolumeExpansion`. Volume expansion = today vs 20-day avg volume, scaled.
Only Quality-PASS names are ranked. Sorted desc by Final.

### FR-006 Watchlist Generator
Candidates with **Final Score ≥ threshold** (operator-configurable in the dashboard,
default **80**, persisted in `meta.alpha_threshold`, clamped 0–100), in ranked order.
Fields: ticker, sector, regime, momentum, trend, earnings, revenue growth, forward EPS
growth, surprise, final. Published as a watchlist.

## 5. Watchlist naming + Telegram operability

- On generation, Alpha Hunter publishes a watchlist named **`Alpha-yyyy-mm-dd`**
  (run date). Re-running the same day **refreshes** that list in place (clears its
  symbols, repopulates) — no duplicate dated lists.
- The published list is set **active** so Telegram `/watchlist` shows it immediately.
  `^IXIC` is seeded by `watchlist.create` (benchmark floor) and kept.
- Telegram can **operate** on it via `cio.bot`: existing `/watchlist` (prices) plus
  new agent tools `list_watchlists`, `watchlist_add`, `watchlist_remove`,
  `watchlist_activate`, and `run_alpha_hunter`. So "show / add / drop / switch" all
  work conversationally, and `/alpha` triggers a fresh run.

## 6. Persistence

Two tables (added to `cio/db.py` SCHEMA):
- `alpha_runs(id, run_date, regime, regime_detail, sectors_json, candidate_count,
  watchlist_id, watchlist_name, universe_size, created_at)`
- `alpha_candidates(run_id, rank, ticker, sector, momentum, trend, earnings,
  revenue_growth, fwd_eps_growth, surprise, volume_expansion, final, quality_pass)`

The latest run drives the dashboard tab; figures are stored snapshots (a run is a
point-in-time scan), distinct from the figures firewall (live prices stay live in
the watchlist price path).

## 7. Dashboard

New tab **Alpha Hunter** (`/alpha`), placed after Watchlist in `_NAV`:
- Regime light (GREEN/YELLOW/RED) + QQQ vs MAs.
- Sector ranking table.
- Top candidates table (sortable columns rendered server-side, ranked order).
- "Run Alpha Hunter" button (POST `action=run_hunter`) — synchronous, bounded by
  universe size; on success links to the published `Alpha-yyyy-mm-dd` watchlist.
- Run history (recent runs with date/regime/count/watchlist).

## 8. CLI

`python -m cio.alpha [--universe FILE] [--no-publish] [--json]` — run the funnel,
print regime + Top-20, publish the watchlist unless `--no-publish`.

## 9. Universe

Default: a curated ~40-name liquid NASDAQ swing universe in
`config/alpha_universe.txt` (one ticker per line, `#` comments). Override with
`CIO_ALPHA_UNIVERSE` (path) or the CLI flag. Universe size bounds run time
(~2 yfinance fetches/name; cached).

## 10. Acceptance criteria

1. `python -m cio.alpha --no-publish --json` returns a regime + ranked candidates
   without raising, online or offline.
2. Each layer is independently unit-tested with synthetic OHLCV/fundamentals (no
   network) — regime classification, quality gating, earnings/momentum/final math.
3. Running publishes/refreshes `Alpha-yyyy-mm-dd`, sets it active, keeps `^IXIC`.
4. Dashboard `/alpha` renders latest run, sector + candidate tables, run button.
5. Telegram `/alpha` runs the funnel and reports regime + Top names; `watchlist_*`
   tools let the operator add/remove/activate from chat.
6. Zero LLM calls in the funnel. Offline test suite stays green.

## 11. Out of scope / deferred

Analyst-consensus revision (Earnings Pro mode), position sizing / risk rules
(proposal §6-7 are operator discipline, not engine outputs in v1), post-trade
review metrics (§9), and intraday/auto-ordering. The funnel emits research only.
