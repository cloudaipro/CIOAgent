# Technical Report — Consensus / Aggregate-Signal Tool

**Status:** DESIGN ONLY — not implemented. This report specifies the design so it can be
scheduled as a future build step or rejected.
**Author:** Arch · **Date:** 2026-05-31 · **Component:** `cfo/stock`

---

## 1. Purpose & Scope

The stock subsystem exposes 38 technical-analysis strategies via
`StrategyEngine.run_all(df)`. Each strategy emits its own DataFrame of boolean signal columns.
That is high-resolution but unsynthesised: a user asking "what do the strategies say about AAPL?"
gets dozens of frames, not an answer.

**Goal:** collapse all 38 strategies into a single directional verdict per symbol —
`BUY` / `SELL` / `HOLD` — with a transparent score and the supporting breakdown.

**Out of scope:** position sizing, price targets, backtesting, portfolio-level signals,
machine-learning weighting. (Weighting is noted as a future extension in §9.)

---

## 2. Background — the current signal model

`StrategyEngine.run_all(df)` → `{strategy_name: signals_DataFrame}` (38 entries).
Each `*_strategy.create_signals(df, ...)` returns a frame indexed like the input OHLCV, with
columns named by the convention `<{c|f}>_<INDICATOR>_<EVENT>[_<DIRECTION>]`:

- `c_` prefix → **condition/crossover** signal, boolean `0/1` (the event fired on that bar).
- `f_` prefix → **factor** column, a continuous value (e.g. `*_CSLS` = candles-since-last-signal,
  a bar count). **Not directional.**

### 2.1 Direction is carried by the suffix (audit)

A scan of all 38 strategy modules yields these signal tokens (count = column occurrences):

| Token family            | Direction | Examples (count)                                              |
|-------------------------|-----------|--------------------------------------------------------------|
| `…_BULL`                | bullish   | CROSSOVER_BULL (24), OVERBOUGHT_BULL (23), OVERSOLD_BULL (12), DIVERGENCE_BULL (12), ZEROCROSS_BULL (9), CENTRALLINE_BULL (7), SUPPORT_BULL (4), DOWN_BULL (2) |
| `…_BEAR`                | bearish   | CROSSOVER_BEAR (15), OVERBOUGHT_BEAR (13), OVERSOLD_BEAR (12), DIVERGENCE_BEAR (10), ZEROCROSS_BEAR (7), CENTRALLINE_BEAR (6), UP_BEAR (1) |
| `…_UP` / `…_DOWN`       | bull/bear | TREND_UP (3), TREND_DOWN (2)                                  |
| `…_CSLS` (`f_`)         | none      | OVERBOUGHTSOLD_CSLS (11), CROSSOVER_CSLS (6), HISTOGRAM_CSLS (3) — bar counts |
| bare state (no suffix)  | ambiguous | OVERBOUGHT (12), OVERSOLD (12), SUPPORT (2), RESISTANCE (2)   |

**Key insight:** the trailing `_BULL`/`_BEAR` is authoritative and prefix-independent —
`OVERBOUGHT_BULL` means *bullish* (a bullish setup detected in the overbought zone), not bearish.
A classifier keyed on the **suffix** is therefore robust; one keyed on the prefix is wrong.

---

## 3. Proposed Design

### 3.1 Direction classifier

Map one signal **column name** → `+1 / -1 / 0`:

```
ends_with _BULL  ->  +1
ends_with _BEAR  ->  -1
ends_with _UP    ->  +1
ends_with _DOWN  ->  -1
prefix f_  OR  ends_with _CSLS   ->  0   (skip: factor/count, not a vote)
otherwise (bare OVERBOUGHT/OVERSOLD/SUPPORT/RESISTANCE/…)  ->  0   (skip in v1; see §4)
```

This is a pure-naming rule — no per-strategy table to maintain — and the audit shows it covers
every directional column in the engine.

### 3.2 Per-strategy vote reduction

For each strategy's latest signal row (or a recency window, §3.5):

```
bull_hits = count of +1 columns that fired (== 1) this bar
bear_hits = count of -1 columns that fired (== 1) this bar
strategy_vote =  +1 if bull_hits > bear_hits
                 -1 if bear_hits > bull_hits
                  0 otherwise (no signal, or balanced)
```

One strategy → one vote. This prevents strategies that happen to emit many columns from
dominating the tally.

### 3.3 Aggregation & verdict

```
score   = sum(strategy_vote)            # range roughly [-38, +38]
n_bull  = count(strategy_vote == +1)
n_bear  = count(strategy_vote == -1)
verdict = BUY   if score >=  +T
          SELL  if score <=  -T
          HOLD  otherwise
```

`T` (default proposed: **3**) is the dead-zone threshold separating a real lean from noise.

### 3.4 API surface

**Facade** (`cfo/stock/__init__.py`):
```
def consensus(symbol_or_df, window=1, threshold=3, start=None, end=None) -> dict
```
Returns:
```json
{
  "symbol": "AAPL",
  "as_of": "2026-05-29",
  "verdict": "BUY",
  "score": 7,
  "n_bull": 11,
  "n_bear": 4,
  "n_neutral": 23,
  "bull_strategies": ["rsi", "macd", "stoch", ...],
  "bear_strategies": ["willr", ...],
  "window": 1,
  "threshold": 3
}
```

**Agent tool** (`cfo/agent.py`): `stock_consensus(symbol)` → lazy-import, call `consensus`,
return `_text(json.dumps(...))`. Registered in `CFO_TOOLS`.

### 3.5 Recency window

`window=1` = "firing on the latest bar only" (strict, may be mostly HOLD).
`window=N` = "fired within the last N bars" (treat a column as active if it == 1 anywhere in the
trailing N rows). Mirrors the `signals_active_last_60d` logic already in `run_stock_strategy`.
Proposed default `window=1`, exposed as a parameter.

---

## 4. Design Decisions & Open Questions

1. **Bare state columns (OVERBOUGHT/OVERSOLD/SUPPORT/RESISTANCE).** v1 skips them (direction
   ambiguous: "overbought" is a *state*, tradeable either as exhaustion-sell or momentum-continue).
   Option: map OVERBOUGHT→−1, OVERSOLD→+1, SUPPORT→+1, RESISTANCE→−1. **Decision needed** — recommend
   skip in v1, revisit if verdicts feel under-powered.
2. **Threshold `T`.** Default 3. Tunable; could be made relative (`|score| >= 0.15 * n_voting`).
3. **Equal weighting.** All strategies weighted equally. Precision-weighting is §9 (future).
4. **Window default.** 1 bar vs N. Recommend 1, parameterised.
5. **Tie / all-neutral** → `HOLD`, `score 0`.

---

## 5. Data Flow (illustrative pseudocode — NOT the implementation)

```
df = get_history(symbol, last ~400d)            # reuse existing fetch+cache
frames = StrategyEngine().run_all(df)           # {name: signals_df | Exception}
votes = {}
for name, sig in frames.items():
    if isinstance(sig, Exception):  continue    # run_all captures per-strategy errors
    window_rows = sig.tail(window)
    bull = bear = 0
    for col in sig.columns:
        d = classify(col)                        # +1 / -1 / 0  (§3.1)
        if d == 0:  continue
        fired = (window_rows[col] == 1).any()
        if fired and d > 0:  bull += 1
        if fired and d < 0:  bear += 1
    votes[name] = +1 if bull > bear else -1 if bear > bull else 0
score = sum(votes.values()); ... -> verdict
```

---

## 6. Edge Cases

- **Failing strategy:** `run_all` returns an `Exception` object (not raised) — skip it; report
  `n_voting` so the user sees coverage.
- **NaN signal cells:** `== 1` is False for NaN — handled implicitly.
- **Short history:** strategies needing long lookbacks emit all-NaN → contribute neutral votes.
  Consider a `min_rows` guard (e.g. 200) returning a clear "insufficient data".
- **Symbol with no data:** `get_history` returns None → raise `ValueError` (consistent with
  `run_strategy`).

---

## 7. Validation Plan

- **Offline unit test:** synthetic OHLCV → `consensus(df)` returns a dict with the documented keys;
  `n_bull + n_bear + n_neutral == n_voting`; `verdict` consistent with `score` vs `T`.
- **Classifier test:** assert `classify` on a fixed list of real column names yields the expected
  signs (lock the suffix rule against regressions).
- **Determinism:** same df → same verdict.
- **No network** in tests (monkeypatch fetch), same pattern as `tests/test_stock.py`.

---

## 8. Risks & Caveats

- **Heuristic, not validated.** Equal-weight majority of TA signals is *not* a backtested edge.
  Output MUST be labelled "technical signal summary, not investment advice."
- **Naming dependency.** If future strategies break the `_BULL`/`_BEAR` suffix convention, the
  classifier silently under-counts. Mitigation: the classifier test in §7 + a one-line log of any
  column it could not classify.
- **Correlated strategies.** Many strategies share families (RSI/RSX, PPO/PVO/MACD); equal votes
  overweight correlated indicators. Acknowledged; precision-weighting (§9) is the principled fix.

---

## 9. Future Extension — precision-weighted consensus

SPP ships parameter-optimization / f1 infrastructure (`parameter_grid.py`, `f1_*` scripts,
`default_*_signal`). A v2 could weight each strategy's vote by its historical precision/F1 on the
symbol, turning the flat majority into a confidence-weighted score. Heavier (needs labelled
forward-return windows + a scoring pass); out of scope for v1.

---

## 10. Effort Estimate

~1 builder step: `classify()` + `consensus()` facade fn + `stock_consensus` agent tool +
2 unit tests. No new dependencies (reuses `run_all`, fetch/cache). Risk: low (additive, no changes
to existing strategies or fetch path).
