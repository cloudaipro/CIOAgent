# Pass-2 Integration Wiring — ARCHITECT-BRIEF (Bob)

Pass-1 shipped the swing-upgrade *logic* (coverage, four-layer gate, trade ledger,
expectancy, hold). Pass-2 wires those modules into live orchestration. Anchor (the
committee gate **compute+persist**) is already done by Arch in `tirf/builder.py`
(`report.review["four_layer_gate"]`). Four wires remain.

## Invariants (do not violate)
- **Zero new LLM calls** anywhere in pass-2 (TIRF/alpha are deterministic layers).
- **Never-raises** on read/render/sync paths — degrade to empty/None, log.debug.
- **No persistence drift** (the bug Richard caught): if you add a dict key that gets
  stored, confirm the store actually writes it. New columns → follow the
  `_migrate_alpha_coverage` pattern in `cio/db.py` (guarded `ALTER TABLE ADD COLUMN`
  + add to `SCHEMA` + extend the INSERT).
- Add/extend tests in `tests/test_swing_upgrades.py`. Run with
  `python3 -m pytest tests/test_swing_upgrades.py tests/test_alpha.py tests/test_tirf.py -q`.
  NOTE: `pandas_ta` is not installed in this env — `test_strategy_engine.py` and
  `test_viz.py` fail at collection regardless; that is pre-existing, ignore it.

## Module APIs you will call
```
cio.alpha.trades:      open_trade(ticker, entry_date, entry_px, *, stop_px, qty, style,
                         regime_at_entry, layer_scores, note, db_path) -> id
                       close_trade(id, exit_date, exit_px, *, note, db_path) -> row|None
                       record_closed(...) -> id   # one-shot already-closed
                       list_closed(ticker=None, db_path) -> [dict]   # never raises
                       list_open(db_path) -> [dict]
cio.alpha.expectancy:  summary(trades, *, avg_hold_days=None, deployment=1.0) -> dict
                       expectancy(trades, key="pct") -> dict
                       oos_check(in_sample, out_sample, key="pct") -> dict
cio.alpha.hold:        hold_decision(layer_scores, regime_status, *, in_profit=True)
                         -> {action: hold|trim|exit, style, stop_mode, reason}
cio.alpha.regime:      evaluate(fetch=None) -> {status: GREEN|YELLOW|RED|UNKNOWN, ...}
                       position_style(status) -> {style, hold, stop_mode}
cio.committee.tirf.gate: gate_evidence(items) -> {pass, blocked_by, missing, scores,
                         layer_scores, thresholds}
```

## Wire 1 — Surface the four-layer gate (finish the anchor)
Arch computes+persists it; you make it visible.
- `cio/committee/tirf/dossier.py` — in `render_dossier` / `tirf_appendix`, read
  `report.review.get("four_layer_gate")` and render a line: per-layer scores +
  `blocked_by`. If `pass` is False, show **⚠ gate: blocked by {layers}**.
- `cio/committee/report.py` — if it renders a TIRF summary, add the gate verdict
  there too. Keep it one compact block; this is advisory, never blocks the run.
- Test: build a `ResearchReport` with specialist evidence across layers, assert the
  rendered dossier text contains the gate verdict.

## Wire 2 — Dashboard expectancy panel
- `cio/dashboard/views.py` — add `render_expectancy(closed_trades, summary)` (mirror
  the `.stat .v` panel style already in the file). **Headline = expectancy** (pct and
  R), with profit_factor, SQN, payoff_ratio, n. **Win-rate shown small/demoted** —
  it is a sub-stat, not the headline (the whole point of upgrade #3).
- `cio/dashboard/server.py` — `do_GET`: add an `/expectancy` route (pattern at
  line ~202 `/alpha`), pulling `trades.list_closed()` → `expectancy.summary(...)`.
  Add a nav link next to the alpha tab. `avg_hold_days` can come from mean
  (exit_date − entry_date) over closed trades; deployment default 1.0.
- Empty-ledger case: render "no closed trades yet", never crash.

## Wire 3 — watchlist_monitor hold call
- `cio/watchlist_monitor/agent.py` — the daily pass uses `profile="monitor"` (line
  ~171). For each held position, call `hold.hold_decision(layer_scores, regime_status)`
  and surface `action`/`reason` in the monitor report.
- **OPEN DECISION (flag to Arch, don't guess silently):** the monitor does not have
  full four-layer scores. Minimum viable mapping: `execution` = monitor composite
  mapped to 0..100; `catalyst` = presence of a near-term earnings/news catalyst from
  the bundle (finnhub `earnings_calendar`/news) if available, else omit. `regime_status`
  from `regime.evaluate()`. If catalyst layer is unavailable, hold_decision still works
  (it only EXITs when catalyst is present AND <= 45) — document that a missing catalyst
  layer means the catalyst-break guard is inactive for that name.
- Test: feed a synthetic layer_scores + regime, assert the monitor surfaces the
  expected action (exit on catalyst-break, hold on green).

## Wire 4 — IBKR trades auto-log
- `cio/data/ibkr.py` — currently connects readonly per call and reads positions
  (`PortfolioItem` → position dicts). **Safety: stay readonly, never place orders.**
- Add a function that, on sync, records realized trades into the ledger: either read
  `ib.fills()`/executions, or diff the current positions snapshot against the previous
  one to infer opens (`trades.open_trade`) and closes (`trades.close_trade`). Capture
  `regime_at_entry` (from `regime.evaluate().status`) and `style`
  (`regime.position_style`) at entry; `layer_scores` may be None on backfill.
- Idempotency: do not double-log a fill already in the ledger (key on
  exec id / fill timestamp). Gate behind the existing IBKR-enabled flag.
- Test: with a fake fills/positions list (no live IB), assert the ledger gets the
  expected open/close rows; assert re-running does not duplicate.

## Definition of done
All four wires + tests green; existing alpha/tirf suites still pass; no new
persistence drift; zero new LLM calls; IBKR path readonly. Return a caveman receipt:
file:change list + test counts + any OPEN DECISIONS bounced back to Arch.
