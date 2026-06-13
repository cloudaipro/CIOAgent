# Alpha Hunter — Test Plan

Goal: prove the funnel is correct, bounded, offline-safe, and side-effect-correct
(watchlist publish + DB persistence) without any network. All tests run on synthetic
OHLCV/fundamentals injected through the engine's fetcher hooks. No LLM, no Yahoo, no
finnhub. Files: `tests/test_alpha.py` (units) + `tests/test_alpha_integration.py`
(integration/edge/security).

## 1. Scope & strategy

| Layer | Module | What we assert |
|-------|--------|----------------|
| Math  | `metrics` | SMA/return/slope/scale boundaries + None-safety |
| L0    | `regime`  | GREEN / YELLOW / RED / UNKNOWN classification |
| L1    | `sectors` | RS = 0.5·3M+0.5·6M ranking, missing-ETF drop, sector tag |
| L2    | `quality` | each gate fails closed; PASS only when all met |
| L2.5  | `earnings`| fwd component, gap-up revision, beat-ratio, combine |
| L3    | `momentum`| RS both-windows, trend template fractions, score scaling |
| L4    | `scoring` | weighted final, revenue/volume sub-scores, 0..100 bound |
| orch  | `engine`  | ranking, quality drop, offline degrade, partial data |
| io    | `store`   | persist, publish, idempotency, top-N cap, no-publish |
| io    | `watchlist` | find_by_name, set_symbols dedupe/index-floor/sanitize |
| fmt   | `report`/CLI | telegram/CLI rendering never raises |

Test types: **unit** (pure functions), **integration** (engine→store→watchlist→
read-back), **edge** (boundaries, empty, short series), **security** (symbol
injection), **regression** (CIO_TOOLS count guard).

## 2. Unit cases

### metrics
- U1 `sma` None when series shorter than window; correct mean otherwise.
- U2 `ret_pct` None when too short; positive on uptrend; None on zero base.
- U3 `slope_up` True rising / False flat / False too-short.
- U4 `scale` None→0, ≤floor→0, ≥full_at→100, midpoint→50, clamp >100.

### regime (L0)
- R1 GREEN: price>50MA>200MA, slope up.
- R2 RED: price below 200MA.
- R3 YELLOW: above 200MA, below 50MA (no full uptrend).
- R4 UNKNOWN: <200 bars.
- R5 `evaluate` offline (fetch raises/None) → UNKNOWN, no raise.

### sectors (L1)
- S1 ranking order by RS desc.
- S2 ETF that fails to fetch is dropped, others still ranked.
- S3 `sector_of` maps known semis/software/cyber; unknown → "QQQ".

### quality (L2)
- Q1 PASS when all minimums met.
- Q2 missing fundamentals → FAIL, reasons non-empty (fail-closed).
- Q3 each single gate below threshold → FAIL with the matching reason token
  (cap / $vol / rev / fwdEPS / fcf).
- Q4 `forward_eps_growth` None when trailing eps ≤ 0; correct % otherwise.
- Q5 `avg_dollar_volume` None when < window bars.

### earnings (L2.5)
- E1 `fwd_eps_component` scales (None→0, 50%+→100).
- E2 `revision_signal` 100 on >5% gap-up that stays unfilled; 0 with no gap.
- E3 gap exactly 5% → 0 (strict `>`).
- E4 `surprise_score` 4/3/2/1/0 beats → 100/75/50/25/0; None→0.
- E5 `evaluate` combines weights, score in 0..100.

### momentum (L3)
- M1 rs_pass True only when BOTH 3M and 6M beat QQQ.
- M2 rs_pass False when one window lags.
- M3 trend_score 100 full template; partial → 33/67; 0 when MAs unavailable.
- M4 momentum_score ~50 at parity, >50 outperforming, clamped 0..100.

### scoring (L4)
- C1 weighting: known sub-scores → expected final.
- C2 all-max inputs → final ≤ 100 (weights sum to 1).
- C3 `volume_expansion` 0 at 1× avg, rises toward 100 at ≥2×.

## 3. Integration cases

- I1 `engine.run` (injected fetchers): GREEN regime, STRONG out-ranks WEAK, ranks 1..n.
- I2 quality-FAIL names excluded from `candidates`.
- I3 offline (`fetch` returns None) → UNKNOWN regime, empty candidates, no raise.
- I4 partial: QQQ None but tickers present → no crash, momentum degrades (rs False),
  quality-PASS names still ranked by final.
- I5 `run_and_save` end-to-end → run row + candidate rows + active Alpha-<date> list.
- I6 `store.save_run(publish=False)` → run persisted, no watchlist, name None.
- I7 top-N cap: >20 candidates → exactly 20 published + persisted.
- I8 empty candidates + publish → watchlist holds only `^IXIC`, candidate_count 0.
- I9 same-day re-run refreshes the one dated list (same id, new symbols).
- I10 `latest_run` / `list_runs` shapes round-trip sectors + candidates.

## 4. Edge cases

- X1 universe file missing/unreadable → built-in fallback (non-empty).
- X2 universe parse strips comments/blanks/whitespace, de-dupes, upper-cases.
- X3 naming rule format is exactly `Alpha-YYYY-MM-DD`.
- X4 `set_symbols` keeps `^IXIC` first even if absent from input; de-dupes; drops
  invalid tokens; replaces (not appends) prior contents.
- X5 candidate with all-None metrics doesn't crash scoring/persist.

## 5. Security cases

- Z1 hostile symbol in the universe file (path traversal `../`, separators) is
  sanitized before any cache/DB use (universe + watchlist both sanitize).
- Z2 `^IXIC` cannot be removed from a published list (benchmark floor invariant).

## 6. Regression

- G1 `CIO_TOOLS` count == 40 (guards the 5 new agent tools) — existing tests.
- G2 full suite stays green (no schema/import regressions from the new tables/tools).

## 7. Exit criteria

All cases above pass offline; `python -m pytest tests/test_alpha*.py` green; full
suite green; no unhandled exception path in any layer when data is missing.
