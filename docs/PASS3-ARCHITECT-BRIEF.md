# Pass-3 — ARCHITECT-BRIEF (Bob)

Three deferred items from pass-1/2. Arch already wired the **anchor** (institutional-%
blend logic in `cio/alpha/coverage.py` + `engine.run(institutional_fn=...)` auto-wiring
via `getattr(edgar, "institutional_ownership_pct", None)`). Three builds remain.

## Invariants (unchanged from pass-2)
- Zero new LLM calls. Never-raises on read/sync/render paths.
- **No persistence drift**: any stored dict key / DB column must actually be written;
  new columns follow the `_migrate_alpha_coverage` guarded-ALTER pattern in `cio/db.py`.
- Tests in `tests/test_swing_upgrades.py`. Verify:
  `python3 -m pytest tests/test_swing_upgrades.py tests/test_alpha.py tests/test_tirf.py -q`
  `pandas_ta` is NOT installed — `test_strategy_engine.py`/`test_viz.py`/`test_stock.py`/
  `test_profiles.py` fail without it; pre-existing, ignore. Do not install it.
- IBKR stays **readonly** — never place/cancel orders.

## Item 1 — EDGAR 13F institutional ownership % (the data half of recommendation #1)
Build `cio/data/edgar.py::institutional_ownership_pct(symbol) -> float | None`
(engine.run already auto-wires it via getattr — just define it, no engine change needed).
- Aggregate 13F-HR holdings of the symbol across filers ÷ shares outstanding → %.
- This is genuinely heavy (EDGAR 13F is filer-side, not issuer-side). If a clean
  aggregation isn't feasible from the existing edgar.py access, return None and BOUNCE
  an OPEN DECISION recommending a data source (the coverage blend already treats None as
  "no signal", so None is safe). **Do not fabricate a number.**
- The blend itself is done: `coverage.coverage_score(recs, market_cap, institutional_pct)`.
- If you persist the raw pct on candidates, add an `institutional_pct` column +
  migration + INSERT (drift rule). Otherwise it already rides inside `coverage_edge`.

## Item 2 — IBKR entry-cost snapshot + orphan reconcile (fixes pass-2 OD-2 backfill)
`cio/data/ibkr.py` — positions already expose `avg_cost` (`_normalize_positions`, the
`averageCost` field).
- On `sync_trades`, BEFORE processing fills, seed the ledger with currently-open
  positions using `avg_cost` as `entry_px` (only if no open ledger trade exists for that
  symbol — idempotent). Then a later SLD fill matches an open → `close_trade` computes a
  REAL pct, so no orphan is created for positions IBKR actually holds.
- Add `cio/alpha/trades.py::reconcile_orphan(orphan_id, entry_px, entry_date, db_path)`:
  convert an existing `status='orphan'` row into `status='closed'` with computed
  pct/r_multiple once a cost basis is known. Keep `list_orphans` for the residue that is
  genuinely un-reconstructable (exits predating any snapshot).
- Tests: synthetic positions+fills (no live IB) → seeded opens, SLD closes at real pct,
  orphan reconciled, re-run does not double-seed/double-log.

## Item 3 — Monitor real-layer gate (fixes pass-2 OD-1 stub)
`cio/watchlist_monitor/agent.py::_hold_decision_for_assessment` currently maps
`execution ← conviction_score` (an LLM composite). Replace with REAL signals:
- `execution` ← the monitor TA composite: `profiles.profile_signals(df, "monitor")`
  → map `composite` bull/bear/neutral to 0..100 (bull≈75, neutral≈50, bear≈25), or use
  the bull/bear vote counts for a finer score.
- `momentum` ← relative strength vs QQQ from the bundle price (reuse
  `cio.alpha.metrics.ret_pct` / `cio.alpha.momentum`) if price history is in the bundle.
- `catalyst` ← bundle filings (`edgar`) + earnings calendar presence (as today).
- `behavior` ← analyst-rec trend delta (`finnhub.analyst_recs`) if cheaply available,
  else omit (document it).
- Feed the assembled `layer_scores` to `hold.hold_decision(...)` (unchanged). Document
  which layers are real vs still omitted. The monitor must stay CHEAP (it is the daily
  pass) — reuse bundle data already fetched; do not add new network round-trips per
  symbol beyond what the bundle already pulls.

## Definition of done
All three + tests green; alpha/tirf suites still pass; no persistence drift; zero LLM
calls; IBKR readonly. Return a caveman receipt: file:change list, test counts, OPEN
DECISIONS bounced to Arch (esp. item 1 if 13F aggregation isn't feasible).
