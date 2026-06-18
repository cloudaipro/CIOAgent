# Harness Engineering for CIOAgent — Evaluation

> Research evaluation prompted by user-found defects in `conv_turns` records
> 326–329 (MCHP) and 346–349 (INTC). Question: will acquiring *harness
> engineering* capability significantly improve CIOAgent's accuracy and
> capabilities?
>
> Date: 2026-06-18. Status: reference / decision record.

## Verdict

Yes — but with a split that matters more than the headline. Harness engineering
significantly improves CIOAgent on the failure classes that are **externally
verifiable**, and two of the three vulnerabilities found are exactly that class.
On the third (forecast magnitude) it improves honesty and calibration but
**cannot** improve raw predictive accuracy, because the ceiling there is set by
market efficiency, not by scaffolding.

The most telling fact in the transcripts: **both flaws were caught by the user,
not the agent.** That is a live instance of Huang et al. 2023 — LLMs do not
reliably self-correct reasoning without an external signal, and can degrade when
they try. Harness engineering is the discipline of converting "the user happened
to catch it" into "an automated check always catches it."

## The three vulnerabilities (from the records)

| Vuln | Source | Failure class |
|---|---|---|
| **V1** | MCHP 326–329 | `$97.50` limit entry contradicts the agent's own Rule 2c (relative-strength anomaly). Internal inconsistency — rules not jointly enforced. |
| **V2** | INTC 347 | Cited a CNBC URL that 404'd. Source fabrication. |
| **V3** | INTC 349 | "Wave 2 = 30–60% of Wave 1" tagged `[inference]`. Empirically ungrounded forecast. |

V1: the agent recommended a shallow-pullback limit below the current price
without checking that, if the index is up, a fill at that price implies the stock
*underperformed* the market — which should trip its own anomaly rule. The entry
trigger and the anomaly rule were never jointly enforced.

V2: the agent invented a plausible-looking source URL; it did not resolve.

V3: the magnitude heuristic had no empirical backing.

## What "harness engineering" is, and how big the lever is

Definition (current literature): the discipline of designing the scaffolding —
context delivery, tool interfaces, planning artifacts, verification loops, memory,
sandboxes — *around* a model. "Every component exists because the model can't do
it alone." It is distinct from prompt engineering (wording) and from training
(weights).

Magnitude evidence: on identical model weights, swapping only the harness moves
agent task-success by 20–30 points on SWE-bench-class benchmarks — a ~22-point
swing from scaffold alone, often larger than a model-tier upgrade. Counterweight:
a strong *unscaffolded* model (Gemini 2.5 Pro) still scores 50.8% bare, and the
field's own principle is that good components are "built assuming their own
obsolescence." So harness ROI is highest for today's models and for *verifiable*
failures, and decays as base models improve.

## Core mapping: vulnerability → remedy → expected payoff

| Vuln | Remedy | Expected improvement |
|---|---|---|
| V1 | Deterministic post-generation validator in the loop's *verify* step: cross-check every emitted entry/stop against the full rule set | **High** — the property is computable; a linter catches it 100% vs. the model's 0% |
| V2 | Fetch-before-cite: resolve every cited URL, fail-closed on non-200, drop/flag; only *live* sources count toward corroboration | **High** — URL liveness is binary; cleanest win; extends Evidence Integrity Policy |
| V3 | Event-study tool returning an empirical *distribution* by event type | **Bounded** — improves calibration/honesty, not point accuracy |

V1/V2 are where the literature is unambiguous: verification-based citation
checking beats LLM self-judgment (citation-grounding / CiteCheck); external
verifiers raise reliability above the bare generator (Lightman et al., *Let's
Verify Step by Step*); deterministic output guardrails are standard production
practice. TIRF already proves the team believes this — it is a zero-LLM-cost
audit layer. A consistency validator and a URL-liveness check are the same move
applied to the two specific holes the user found.

V3 is bounded by market efficiency. Event-study research: returns after large
moves are only partially predictable; high-volume spikes are often followed by
reversals/drift; earnings/analyst catalysts cluster around ~3–4% 20-day abnormal
returns (positive) and ~−2.25% (negative). A tool that replaces a hallucinated
"30–60%" with that empirical distribution improves calibration and honesty — but
it cannot make the forecast precise, because the unpredictable part is the part
the market already arbitraged away. Sell it as "honesty/calibration," not
"accuracy."

## The meta-capability: the agent harness-engineering itself

The framing "CIOAgent will add skills or operations to fix them" is the
self-improvement variant: each found defect becomes a durable, persistent
artifact. Strongest precedent — Voyager: an agent that accumulates an
**execution-verified** skill library (3.3× more items, key milestones up to 15.3×
faster, skills generalize to new worlds). That is the compounding you want.

The hard condition comes with it. Voyager works because skills are *validated by
execution before admission*. Reflexion works because feedback is *external*. If
CIOAgent authors its own checks and validates them by its own judgment, it
reproduces the failure it is trying to fix. So the self-authoring loop needs two
gates: **Verification/CI** (a new skill must pass real held-out cases before
admission) and **Human-in-the-loop** (owner approval). Without them, three known
failure modes appear: checks overfit the single triggering case, the library
ossifies into contradictory rules, and the skill surface becomes an injection
target (agent-skills security literature).

Token irony worth naming: every self-added check runs every session. An unbounded
self-growing validator stack is a token-cost regression — against this project's
own token-optimization ethos. TIRF's "zero-LLM-cost" instinct is the fix: keep
new checks **deterministic** (Python, not model calls) wherever the property is
computable — which for V1 and V2 it entirely is.

## Bottom line

- **Consistency validators + source-liveness checks (V1, V2):** high, durable,
  deterministic, cheap. Do first.
- **Event-study / analog tool (V3):** do it, but scope the claim to calibration
  and honesty, not foresight.
- **Self-authoring loop (meta):** high upside via the Voyager skill-library
  pattern — but only if every self-authored skill is admitted through external
  verification + owner approval and kept deterministic. Ungated, it recreates the
  self-correction failure it is meant to solve.

CIOAgent is already heavily harness-engineered (TIRF, Evidence Integrity Policy,
fallback chains, source policy). The decision is not *whether* to
harness-engineer — it already does — but whether to **institutionalize it as the
agent's response to every user-found defect**, with verification gates. For
verifiable defects: yes, significantly. For market forecasting: calibration, not
accuracy — and conflating the two is the one mistake to avoid.

## Sources

- Agent Harness for LLM Agents: A Survey (Preprints.org 202604.0428); awesome-harness-engineering (taxonomy); Harness-Bench (arXiv 2605.27922); SWE-bench Pro scaffold analysis.
- Huang et al., *LLMs Cannot Self-Correct Reasoning Yet* (arXiv 2310.01798); Shinn et al., *Reflexion* (arXiv 2303.11366); Lightman et al., *Let's Verify Step by Step* (arXiv 2305.20050).
- Citation Grounding (arXiv 2606.00898); CiteCheck (arXiv 2605.27700); Deterministic Guardrails for LLMs (Rulebricks).
- Wang et al., *Voyager* (arXiv 2305.16291); Agent Skills: Architecture, Acquisition, Security (arXiv 2602.12430); SoK: Agentic Skills (arXiv 2602.20867).
- Kothari & Warner, *Econometrics of Event Studies*; Return Predictability Following Large Price Changes (ScienceDirect S0378426600000911).
