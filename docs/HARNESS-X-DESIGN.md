# HarnessX adoption — design spec (items 1–4 + optional)

> What we took from *HarnessX: A Composable, Adaptive, and Evolvable Agent Harness
> Foundry* (2606.14249), and what we deliberately did not. The paper's flagship
> mechanism — AEGIS automatic trace-driven evolution + cross-harness GRPO
> co-training — is mismatched to CIOAgent (no market verifier, low decision volume,
> API model we can't fine-tune, real-money overfit risk). See
> `HARNESS-ENGINEERING-EVALUATION.md`. What transfers is the *structure and
> discipline*: typed composition, a falsifiable change-manifest, the operational-
> mirror as a review lens, variant isolation, and AEGIS's *diagnosis* stages run as
> a propose-only advisor behind the existing human gate.

This document is the concrete spec for the five adopted pieces, all already
implemented under `cio/harness/` and wired into `cio/agent.py`. Tests:
`tests/test_harness_x.py` (+ the wiring update in `tests/test_harness.py`).

---

## Item 1 — Typed processor / hook run loop

**Problem.** V1 (consistency) and V2 (citation) were exposed as MCP tools the model
*chose* to call (a `SYSTEM_PROMPT` instruction). A guard the model can forget does
not structurally close the defect that motivated it (the MCHP self-check failure,
conv_turns 326–329).

**Design.** A processor bound to a lifecycle hook, fired unconditionally by a run
loop — HarnessX's composition layer (paper §3).

- `cio/harness/runloop.py`
  - `Hook` (IntEnum): `BEFORE_MODEL`, `AFTER_MODEL`, `BEFORE_TOOL`, `AFTER_TOOL`.
    Only `AFTER_MODEL` is wired; the rest are defined so a future guard drops in
    without a type change.
  - `Event` — the unit handed to each processor: `text` (assistant reply), `scope`,
    `profile`, `sources`, injected `resolver`, `extra` flags. All defaulted.
  - `Processor` (Protocol): `name`, `hook`, `process(event) -> ProcessorResult`.
  - `ProcessorResult` — `findings`, `note` (⚠️ string to append), `intercept` +
    `replacement` (reserved for a future hard-block mode).
  - `Runloop.fire(event) -> RunResult` — runs the hook's processors in order,
    concatenates notes, first intercept wins. **Never raises**: a throwing
    processor is recorded empty and the turn proceeds (a broken guard must not
    break the bot).
- `cio/harness/processors.py`
  - `ConsistencyProcessor` (AFTER_MODEL): parses ```` ```plan {json} ```` blocks the
    model emits, runs the unchanged `consistency.check_trade_plan`, annotates.
    Deterministic parse (no NLU); unknown JSON keys are filtered so `TradePlan(**d)`
    can't blow up; no plan block ⇒ clean no-op (cannot false-positive).
  - `CitationProcessor` (AFTER_MODEL): verifies the turn's **structured sources**
    (already assembled by the agent) for liveness; material-corroboration applies
    only when the answer carries a material claim (`event.extra["material"]`).
  - `after_model_note(...) -> str | None` — the convenience the agent calls;
    mirrors `_run_verifier`'s `str | None` contract exactly.

**Owner decision (locked).** Door-guard (hook) runs V1/V2; V3 stays a model-pulled
analytic tool. V1/V2 are *checks* the hook can do unconditionally; V3 *computes*
something whose trigger depends on what was asked, so it has no unconditional hook
— it remains `harness_event_study`. Consequence: V1/V2 removed from the MCP surface
(tool count 46 → 44); a smaller tool surface also saves schema tokens every
session.

**Live behavior.** ANNOTATE-ONLY. The hook appends a ⚠️ gate note (same contract as
the existing Haiku verifier); it never silently drops the answer. `intercept` exists
in the types for a future hard-block mode but no default processor uses it.

**Wiring** (`cio/agent.py:ask()`, after the Sources footer, before `_run_verifier`):

```python
if _HARNESS_HOOK:                       # CIO_HARNESS_HOOK=1 default; =0 disables
    hnote = harness.processors.after_model_note(
        text, scope=self._scope, profile="committee",
        sources=list(_sources_for(self._scope)),
        material=_has_material_or_web_claim(text))
    if hnote:
        text = text + hnote
```

**Model contract** (`SYSTEM_PROMPT`): emit any entry/exit plan as a ```` ```plan ````
block with one JSON object (keys = `TradePlan` fields). The gate reads that block;
the model no longer calls a consistency/citation tool.

---

## Item 4 — Situation-routed variants

**Problem.** `cio/stock/profiles.py` already routes by situation
(committee / monitor / swing) but only selected TA strategy sets, not which harness
guards run. So guards fired everywhere or were model-elective.

**Design.** Lift the profile concept to the harness layer (paper §4.5 variant
isolation, done deterministically by owner config rather than auto-evolution).

- Each `PROFILES` entry gains a `harness` list:
  - `committee`: `["v1","v2","v3"]` — deep decision, full scrutiny.
  - `monitor`: `["v2"]` — a watchlist pass emits no plan, so v1 has nothing to
    check and could only false-positive; keep cheap citation liveness only.
  - `swing`: `["v1","v3"]` — entry timing + magnitude.
- `profiles.harness_for(name) -> list[str]` — resolve (reusing `resolve_profile`,
  aliases included), default `committee` on unknown, never raises.
- `processors.build_runloop(profile)` consults `harness_for`; maps `v1`/`v2` to
  processors, **skips `v3`** (it's a tool, not a hook processor).

**Payoff.** A guard fires only where it applies (v1 cannot false-positive on a
monitor pass), the cheap pass stays cheap, and an edit to one variant cannot regress
another — enforced by a regression golden (item 3).

---

## Item 2 — Change-manifest + Level-2 verify

**Problem.** The Meta gate's `verify()` was Level-1 only (callable returns expected),
and admitted skills carried no falsifiable record of what they do.

**Design** (paper §B.3 / Table 9, App C).

- `cio/harness/models.py::SkillManifest` — `bucket`, `predicted_unlocks/_stabilizes/
  _at_risk`, `attribution_signature` (the trace feature that MUST appear if the
  skill fired — grep production transcripts for it to confirm it's live, not inert),
  `capability_evidence` (Level-2 proof), `rollback_target`. `complete()` requires a
  signature + a prediction. `HarnessSkill.manifest` added; surfaced in `to_row()`.
- `cio/harness/registry.py`
  - `VerifyCase.level` (1|2) + `runner`. Level-2: the registry calls
    `runner(skill.check, input)` — wired by the case author to drive the skill
    through the real path (e.g. fire it via the run loop) and return what the
    model's next input would carry.
  - `verify()` **requires** a Level-2 case for `kind in {processor, tool}` (a unit
    pass cannot prove the model sees the effect). Existing kinds
    (validator/resolver/analytic) are unaffected → no regression.
  - `SkillRegistry(require_manifest=True)` (opt-in): `approve()` refuses a
    non-builtin skill lacking a complete manifest. Default `False` keeps the base
    gate and its tests unchanged; the dashboard/advisor path turns it on. Builtins
    (origin `builtin`) are exempt and carry dogfood manifests in `tools.py`.

---

## Item 3 — Operational-mirror as a review checklist (+ regression seesaw)

**Zero-code part — the checklist.** Apply to EVERY harness change, self-authored or
human, before admission (paper §4.2's three RL pathologies as design risks):

1. **Reward hacking.** Are the `VerifyCase`s adversarial + held-out, or
   author-flattering happy paths? Does `check` measure the real failure or a proxy
   string it can game?
2. **Catastrophic forgetting.** Does this edit touch a prompt clause / memory key /
   rule that an existing skill or rule ALSO touches? Run the regression suite — does
   any previously-passing golden flip? (This is the τ³-Telecom −14% failure mode and
   the exact risk hit by the triple-homed relative-weakness rule.)
3. **Under-exploration.** Is this the cheap local edit (another `SYSTEM_PROMPT`
   bullet) when the durable fix is a deterministic check? The harness exists to turn
   nudge → check; skipping that *is* the pathology.

**Code part — the seesaw.** `cio/harness/regression.py`: a fixed set of golden
`(Event → expectation)` pairs over the processor/profile layer. `check_regression()`
returns failures (empty ⇒ all hold). Goldens include a **variant-isolation** case
(the MCHP plan that BLOCKS under `committee` must NOT block under `monitor`), so a
careless edit that widens a guard into the wrong situation is caught. Run in CI
(`tests/test_harness_x.py`) and optionally before admitting a config edit:
a flipped golden = a regression = refuse.

---

## Optional — Propose-only advisor (AEGIS Digester/Planner, declawed)

**Design** (`cio/harness/advisor.py`). AEGIS = Digester → Planner → Evolver →
Critic+gate, auto-shipping on a verifier score. CIOAgent has no market verifier, so
the auto-ship half is off the table. The first two stages need only traces + rules:

- `digest(traces, min_count=3) -> [DefectPattern]` — count recurring finding codes
  (the codes the after_model run loop already produces); surface those ≥ threshold.
- `plan(patterns) -> [ProposalDraft]` — map known codes to draft proposals via a
  template table; unknown codes are reported, not auto-drafted.
- `run_advisor(traces, path) -> [record]` — files PROPOSED records via
  `store.propose` and **nothing else**.

**Hard boundary.** The advisor can only reach `PROPOSED`. It never calls
verify/approve/activate — the human gate (`cio.harness.admin` / dashboard `/skills`)
is the only path to ACTIVE. Worst case: a bad suggestion the owner rejects at the
gate (archived with reason). Deterministic core (no LLM in the tested path); runs on
demand over batched traces, not per turn — fits the token doctrine, not a 100M-token
AEGIS round.

---

## Dependency order

`runloop` (1) → `build_runloop` consumes `profiles.harness_for` (4) → goldens fire
through the run loop (3). Manifest + Level-2 (2) is independent. The advisor
(optional) drafts into the existing store/gate; build it last.

## Test plan (executed)

`tests/test_harness_x.py` — run loop mechanics (throwing/​intercept/​hook-match);
consistency processor (plan parse, MCHP block, clean no-op, malformed, extra keys,
multi-plan); citation processor (dead/​live/​no-source/​non-material); variant
routing + isolation; `after_model_note`; manifest `complete()` + `to_row`; Level-2
gate (processor refused without L2, verified with L2, runner used, validator L1-only);
manifest gate (strict refuse/​allow, builtin exempt); builtin manifests; regression
suite (clean config green, broken/​widened configs detected); advisor (threshold,
sort, known-code mapping, PROPOSED-only boundary, empty). `tests/test_harness.py`
`TestAgentWiring` updated for the V1/V2 tool removal. Full suite must stay green.
