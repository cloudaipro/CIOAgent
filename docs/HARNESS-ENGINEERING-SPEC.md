# Harness Engineering — Implementation Spec (`cio/harness/`)

> Concrete spec for the four capabilities in `HARNESS-ENGINEERING-EVALUATION.md`:
> V1 consistency validator, V2 fetch-before-cite, V3 event-study tool, and the
> self-authoring skill registry (the meta-capability). Status: implemented.

## Design principles (inherited from TIRF / source_policy)

1. **Deterministic, zero-LLM-cost.** Every check is pure Python. No model call in
   the hot path. (TIRF precedent; token-optimization mandate.)
2. **Fail-closed.** Unknown/unreachable ⇒ the unsafe-to-assert verdict, never the
   permissive one. (source_policy precedent: unlisted domain ⇒ Tier 3.)
3. **Never-raises core.** Validators return verdict objects; they do not throw on
   bad input. I/O (HTTP, DB) is injected so the core is offline-testable.
4. **Single source of truth.** V2 reuses `cio/data/source_policy.py`; it does not
   re-implement tiering.
5. **Audited admission.** No self-authored skill becomes active without passing a
   verifier suite *and* an explicit owner approval. Both recorded.

## Package layout

```
cio/harness/
  __init__.py        public API
  models.py          enums + dataclasses (Severity, Finding, CheckResult, ...)
  consistency.py     V1 — TradePlan rule-consistency gate
  citation.py        V2 — fetch-before-cite / URL liveness (reuses source_policy)
  event_study.py     V3 — post-catalyst return distribution (never a point)
  registry.py        meta — skill registry + PROPOSED→VERIFIED→APPROVED→ACTIVE gate
  tools.py           Anthropic tool specs + dispatch (wiring surface)
```

## V1 — `consistency.py`

**Contract:** `check_trade_plan(plan: TradePlan, cfg=DEFAULT_CFG) -> CheckResult`.

`TradePlan` (all optional, dataclass): `symbol, side('long'), entry_kind
('pullback'|'limit'|'breakout'|'market'), entry_price, current_price,
stop_price, target_price, market_bias('up'|'flat'|'down'), structure_low,
pct_today, at_upper_band(bool), entry_date, earnings_date, min_hold_days,
forced_exit_date, rel_strength_pct_2c`.

**Rules (each emits a `Finding`):**

| Code | Severity | Condition | Fix |
|---|---|---|---|
| `R1_REL_WEAKNESS` | BLOCK if implied underperf ≥ `rel_strength_pct_2c`, else WARN | `entry_kind∈{pullback,limit}` and `entry_price<current_price` and `market_bias∈{up,flat}` — a fill requires the stock to fall while the market does not ⇒ relative weakness ⇒ trips Rule 2c | Condition the pullback on the market also pulling back (RS maintained), or switch to a breakout/confirmation entry |
| `R2_PLAN_COHERENCE` | BLOCK | long with `stop≥entry` or `target≤entry` | Incoherent plan; stop below entry, target above entry |
| `R3_RR_FLOOR` | WARN | R:R = `(target−entry)/(entry−stop)` < `min_rr` | Tighten stop to structure or raise target; do not enter below the R:R floor |
| `R4_EARNINGS_WINDOW` | WARN | `entry_date + min_hold_days > earnings_date − blackout_days` (forced exit before a real swing) | Window too short pre-earnings; wait for post-earnings setup |
| `R5_CHASE` | WARN | `entry_kind∈{market,breakout}` and `pct_today ≥ extended_pct` and `at_upper_band` | Chasing an extended day at the upper band; wait for confirmation/pullback-with-RS |

This is the V1 fix: the MCHP plan trips `R1_REL_WEAKNESS` (BLOCK) deterministically.
`CheckResult.blocked` is true iff any BLOCK finding present.

## V2 — `citation.py`

**Contract:** `verify_citations(citations, resolver=http_resolver,
issuer_domains=None) -> CitationReport`.

`Citation`: `url, claim_class(optional ClaimClass), backs_material(bool)`.

**Steps per citation:**
1. Parse host. `tier = source_policy.classify(host, issuer_domains)`.
2. `status = resolver(url)` (HTTP status int, or None = unreachable). Injected.
3. `live = status is not None and 200 ≤ status < 400`.
4. Verdict: a **dead** URL ⇒ `BLOCK` (fabrication / stale). This catches the
   CNBC 404.

**Material-fact corroboration:** for citations with `backs_material=True`, only
**live** sources contribute tiers; apply `source_policy.is_verified(live_tiers)`.
If material claims are present but not verified by live sources ⇒ `BLOCK`
(`C_MATERIAL_UNVERIFIED`). A fabricated Tier-2-looking URL cannot launder a
material fact, because dead ⇒ excluded.

`http_resolver`: stdlib `urllib` HEAD (GET fallback), 4s timeout, no third-party
deps; returns status or None. Fully replaceable in tests.

## V3 — `event_study.py`

**Contract:** `study(event_type: EventType, horizon_days=20,
samples=None) -> EventStudyResult`. Never returns a point estimate.

- `samples` = list of realized forward returns for historical analogs (supplied
  by an injected provider, e.g. from the `prices` table). If
  `len(samples) ≥ MIN_SAMPLES (8)` ⇒ compute empirical `mean, median, p25, p75,
  hit_rate`, `sample='historical'`.
- Else ⇒ `REFERENCE_DIST[event_type]` (coarse priors from published event-study
  magnitudes), `sample='reference'`, with an explicit honesty note.
- `EventStudyResult` always carries `note`: "distribution, not a point forecast;
  magnitude bounded by market efficiency."

`wave2_estimate(wave1_pct, event_type)` returns a *band* (low/high) + caveat — the
grounded replacement for the hallucinated "30–60%". Reference bands are labelled
reference, not fitted.

`REFERENCE_DIST` (20-day, abnormal, reference priors):
analyst_action +3.5% (−2.25% down-rev), product_milestone +4%, strategic_customer
+6%, gov_announcement +5% (mean-reverting), earnings ±5%, mna +8%, other +2%.
All with wide p25/p75 reflecting low predictability.

## Meta — `registry.py` (the self-authoring loop)

**State machine (admission gate):**

```
PROPOSED --verify(cases)--> VERIFIED --approve(owner)--> APPROVED --activate--> ACTIVE
    |                          |                                                    |
    +------ REJECTED <---------+ (any case fails)                      retire() --> RETIRED
```

**Invariants (enforced, the whole point):**
- `approve()` on a non-`VERIFIED` skill ⇒ refused (returns error verdict, no state
  change). Human approval cannot precede external verification.
- `verify()` requires **100%** pass on the must-pass case set (configurable
  `pass_threshold`, default 1.0). Any failure ⇒ `REJECTED`. This is the
  external-verification gate (Voyager: execution-validate before admit).
- `activate()` only from `APPROVED`.
- Every transition appends to `skill.audit` with timestamp, actor, detail.
- `origin='self_authored'` skills get **no** fast-path: identical gate as any
  other. Built-ins (V1/V2/V3) also pass their own verifier suites (dogfood).

**`HarnessSkill`:** `id, name, version, kind('validator'|'resolver'|'analytic'),
trigger, origin, status, check, created_at, approved_by, audit[]`. `check` is a
callable `(case_input) -> verdict`; verification compares to `case.expected`.

**Persistence:** JSON via injected path (`to_json`/`from_json`); default in-memory.
Deliberately does **not** migrate `cfo.db` (least blast radius).

`run_active(skill_id, input)` dispatches only `ACTIVE` skills; calling a
non-active skill is refused.

## `tools.py` — wiring surface

`TOOL_SPECS`: Anthropic tool-schema list for `harness_check_trade_plan`,
`harness_verify_citations`, `harness_event_study`. `dispatch(name, args) -> dict`.

## Wiring (implemented 2026-06-18)

V1/V2/V3 are now live SDK tools in `cio/agent.py` (`CIO_TOOLS`), reusing
`TOOL_SPECS` schemas + `dispatch` so the tool surface can't drift from the tested
capability. V2 resolves URLs over the real network in the (non-sandboxed) bot
process. A `SYSTEM_PROMPT` bullet tells the model when to call each.

The Meta loop is wired asymmetrically:
- the agent gets ONE write tool, `harness_propose_skill`, which appends a PROPOSED
  record via `store.py` — it cannot verify/approve/activate;
- the owner drives the gate with `python -m cio.harness.admin`
  (`list/show/verify/approve/activate/reject/retire`). `approve` is refused unless
  `VERIFIED`; `activate` unless `APPROVED` (same ordering as the in-memory registry).
- `verify` runs an owner-committed `(check, cases)` from `candidates.py`; a skill
  with no committed implementation cannot pass except by an explicit owner
  `--manual` attestation. Model-authored text never executes.

Persistence: `data/harness_skills.json` (gitignored). cfo.db is never migrated.

## Acceptance criteria

1. MCHP plan (records 326–329) ⇒ V1 emits `R1_REL_WEAKNESS` BLOCK.
2. INTC citation set with a dead CNBC URL ⇒ V2 BLOCKs that citation; the live
   Reuters/Yahoo Tier-2 pair verifies the Apple material fact (records 349).
3. V3 never returns a point; `wave2_estimate` returns a band + caveat.
4. Registry refuses approve-before-verify and rejects a skill failing any case.
5. All deterministic; no network or live DB needed in tests.
