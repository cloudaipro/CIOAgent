# Harness Engineering — Testing Plan

> Comprehensive test plan for `cio/harness/`. Executed by `tests/test_harness.py`
> (pytest). All deterministic: no network, no live cfo.db. HTTP and DB are
> injected.

## Strategy

Three layers, mirroring the repo's test conventions (`test_tirf.py`,
`test_source_policy.py`):

1. **Unit** — each capability's rules in isolation, including the negative paths
   (where a rule must *not* fire — over-blocking is a harness failure too).
2. **Gate/property** — the registry admission state-machine: every illegal
   transition is refused, every legal one audited.
3. **Replay** — the two real defects (conv_turns 326–329 MCHP, 347/349 INTC)
   driven end-to-end through the *active* registry skills. These are the
   acceptance tests: the harness must catch what the user caught.

## Coverage matrix

| # | Area | Cases |
|---|---|---|
| 1 | V1 consistency | MCHP replay ⇒ BLOCK `R1_REL_WEAKNESS`; market-down pullback ⇒ no R1 (no false positive); sub-threshold ⇒ WARN not BLOCK; incoherent stop/target ⇒ BLOCK `R2`; sub-floor R:R ⇒ WARN `R3`; pre-earnings squeeze ⇒ WARN `R4`; chase ⇒ WARN `R5`; clean plan ⇒ ok; empty plan ⇒ never raises |
| 2 | V2 citation | dead URL ⇒ BLOCK `C_DEAD_URL`; INTC caught (dead CNBC + 1 live Tier-2) ⇒ blocked + material unverified; INTC fixed (Reuters+Yahoo live Tier-2) ⇒ ok + material verified; live Tier-3 backing material ⇒ verdict not ok + `C_MATERIAL_UNVERIFIED`; issuer domain promoted to Tier-1; fake resolver offline |
| 3 | V3 event study | reference fallback (sample=reference, n=0, note present); historical fit (≥8 samples, correct mean/hit-rate); <8 ⇒ reference; always a distribution (p25<p75); `wave2_estimate` band low<high + caveat; percentile correctness; prices provider builds samples from an in-memory prices table |
| 4 | Registry gate | admit happy path ⇒ ACTIVE; approve-before-verify ⇒ refused, state unchanged; failing case ⇒ REJECTED + approve still refused; activate-before-approve ⇒ refused; run_active only on ACTIVE; throwing check ⇒ REJECTED; empty cases ⇒ refused; duplicate id ⇒ raises; self-authored uses identical gate; audit trail complete |
| 5 | Default registry | `build_default_registry` ⇒ V1/V2/V3 all ACTIVE via the gate; each has verify_pass+approve+activate audit |
| 6 | Tools | TOOL_SPECS well-formed; dispatch trade-plan/event-study offline; unknown ⇒ error |
| 7 | Replay (acceptance) | MCHP via `run_active('v1_consistency')` ⇒ blocked; INTC caught vs fixed via citation skill ⇒ blocked then ok |

## Pass criteria

All cases green. The two replays (row 7) are the load-bearing acceptance tests:
if either regresses, the harness no longer catches the defect that motivated it.

## Run

```bash
pytest tests/test_harness.py -v
# full suite regression (harness must not break existing modules):
pytest -q
```

## Wiring (post-merge, manual)

Append `cio.harness.tools.TOOL_SPECS` to the agent tool list and route those tool
names to `cio.harness.tools.dispatch`. V1 should run on every emitted trade plan
(verify step), V2 on every response carrying citations, V3 when a magnitude
question is asked. Kept out of `CIO_TOOLS` until owner enables, per the admission
philosophy.
