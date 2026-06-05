# Product Requirements Document — Transparent Investment Research Framework (TIRF)

**Version:** 1.0 (implementation PRD)
**Author:** Arch (Architect), CIOAgent
**Status:** Approved for build
**Source proposal:** `Transparent_Investment_Research_Framework(TIRF).md`
**Implements into:** `cio/committee/` (AI Investment Committee subsystem)

---

## 0. How this PRD relates to the proposal

The root proposal `Transparent_Investment_Research_Framework(TIRF).md` is the **vision**.
This document is the **build contract**: it keeps every requirement of the proposal and
binds it to the real CIOAgent architecture and its *locked owner decisions* (see
`memory/aicas-committee.md`). Where the proposal's generic shape conflicts with a locked
decision, this PRD records the **adaptation** and the reason. Nothing in the proposal is
dropped; some things are re-homed.

### Locked decisions TIRF must honor

| Locked decision | Consequence for TIRF |
|---|---|
| Committee review = **~20 LLM calls** ceiling | TIRF adds **zero** new LLM calls. Every deliverable rides an *existing* specialist/CIO turn (same pattern as `memory_note` and the data bundle). All TIRF processing — scoring, validation, versioning, persistence, dossier — is **deterministic Python**. |
| Single LLM entry point = `engine.ask_role` (monkeypatched in tests) | TIRF never calls a model directly. |
| Surface = **Telegram** (no CLI surface, no new MCP tool, `CIO_TOOLS` stays 20) | The proposal's HTTP API (§16) is re-homed to a **Python store API** + the existing read-only **dev dashboard** + a **dev CLI** (`python -m cio.committee.tirf`). No web server is added. |
| Committee persistence = `committee.db` (alongside `token_usage`, `committee_transcript`) | All nine TIRF tables live in `committee.db`. Module-level `DB_PATH`, monkeypatchable, never-raises — mirrors `transcript.py`/`usage.py`. |
| Figures firewall (no stale numbers in durable memory) | TIRF evidence/assumptions **may** carry figures because they are *snapshot-scoped to one report* (auditable, versioned, never recalled as live truth). They never enter `mem_notes`. |
| Offline-safe, never-raises | Every TIRF function degrades gracefully; a TIRF failure never breaks a committee run or a Telegram reply. |

---

## 1. Executive Summary

TIRF is a **mandatory research-documentation layer** inserted between the specialist
agents and the investment committee. Today a specialist emits a conclusion
(`vote`, `confidence`, `reason`) plus role fields. After TIRF, every specialist conclusion
is additionally backed by, in the **same** LLM turn:

* **Evidence** — supporting facts with source, date, finding, impact, confidence
* **Assumptions** — every explicit assumption the view rests on
* **Reasoning** — the logical chain from evidence to conclusion
* **Counterarguments** — the opposing case (≥3)
* **Sources** — every reference used

These deliverables are then **scored, validated, versioned, persisted, and rendered**
into a human-readable **Research Dossier**, making every committee conclusion
**Traceable, Explainable, Reproducible, Auditable, and Challengeable.**

---

## 2. Goals & Non-Goals

### Goals
1. Every specialist conclusion carries the six mandatory deliverables (§6 of proposal).
2. Evidence is scored for quality (reliability × recency × relevance).
3. Assumptions are tracked with provenance (agent, version, date, confidence).
4. Every committee research output is versioned per ticker.
5. Every run is reproducible: data snapshot + prompt version + agent version + research version are stored.
6. A complete Research Dossier (11 sections) is generated and persisted.
7. The committee challenge protocol (debate) is persisted and auditable.
8. The CIO review scorecard (§14) is computed deterministically before final approval.
9. Five success metrics are computed per report (Explainability, Traceability, Auditability, Reproducibility, Challenge Coverage).

### Non-Goals (v1)
* No automatic fact verification / citation confidence / contradiction detection (proposal §18 → TIRF v2).
* No research graph database / knowledge lineage (proposal §18 → TIRF v3).
* No public HTTP server (re-homed to store API + dashboard + dev CLI).
* TIRF does **not** force a model to comply; it **measures** compliance and surfaces gaps. Non-compliant output lowers a report's quality scores but never crashes a run.

---

## 3. Design Principles (from proposal §4, binding)

1. **Evidence before opinion** — the specialist contract lists evidence *before* vote.
2. **Assumptions must be explicit** — hidden assumptions lower the Explainability score.
3. **Reasoning must be inspectable** — the chain is parsed and rendered.
4. **Alternative views must be preserved** — counterarguments are mandatory and stored.
5. **All conclusions are versioned** — every report has `(ticker, version)`.

---

## 4. System Architecture

```
                    gather_bundle(symbol)                 ← data + as_of (snapshot source)
                            │
                            ▼
   ┌─────────────── 9 Specialist agents (1 LLM call each) ───────────────┐
   │  yaml output now also carries:                                       │
   │    evidence[], assumptions{}, reasoning[], counterarguments[],       │
   │    sources[]   (+ existing vote/confidence/reason/role-fields/note)  │
   └──────────────────────────────┬──────────────────────────────────────┘
                                   │  (deterministic, no LLM)
                 ┌─────────────────┴───────────────────┐
                 ▼                                      ▼
        tirf.extract  → models            tirf.scoring → EvidenceScore
                 │                                      │
                 ▼                                      ▼
        tirf.validate → completeness + 5 success metrics
                 │
                 ▼
   debate (existing) ──► committee_challenges / committee_responses (persisted)
                 │
                 ▼
        moderator (LLM) + deterministic vote tally
                 │
                 ▼
        CIO (LLM)  ──►  tirf.review → CIO Review Scorecard (deterministic)
                 │
                 ▼
        tirf.repro  → reproducibility manifest (data hash, prompt/agent/research versions)
                 │
                 ▼
        tirf.store  → committee.db (9 tables, versioned)
                 │
                 ├──► tirf.dossier → 11-section Markdown memo  (persisted + PDF)
                 └──► report.build_report → existing 14-section report + TIRF appendix
```

**Key invariant:** the only LLM calls are the existing specialists + moderator + CIO +
bounded debate. TIRF is a pure post-processing + persistence layer.

---

## 5. Mandatory Deliverables — data contract (proposal §6)

Each specialist's yaml fence gains five optional-but-expected keys. The role prompt
demands them; the extractor tolerates absence (degraded score, never a crash).

### 5.1 Evidence (`evidence:`) — list, **min 3**
```yaml
evidence:
  - source: "SEC 10-Q"        # free text; classified into a reliability tier
    date: "2026-05-01"         # ISO; drives recency score (vs report as_of)
    finding: "HBM revenue +120% QoQ"
    impact: positive           # positive | negative | neutral
    relevance: direct          # direct | related | indirect  (default: related)
    confidence: high           # high | medium | low
```

### 5.2 Assumptions (`assumptions:`) — map or list, all explicit
```yaml
assumptions:
  revenue_growth: "15%"
  terminal_growth: "3%"
  discount_rate: "9%"
```

### 5.3 Reasoning (`reasoning:`) — ordered list (logical chain)
```yaml
reasoning:
  - "Evidence A raises the earnings outlook"
  - "Higher earnings imply higher fair value"
  - "Current price sits below that fair value"
```

### 5.4 Counterarguments (`counterarguments:`) — list, **min 3**
```yaml
counterarguments:
  - "HBM supply may normalize, compressing pricing"
  - "AI capex could slow in 2H"
  - "Export controls may tighten"
```

### 5.5 Sources (`sources:`) — list
```yaml
sources:
  - "SEC 10-Q (2026-05-01)"
  - "Q1 earnings call"
  - "SemiAnalysis industry report"
```

The **Structured Output** (proposal Deliverable 1) is the already-existing
`vote`/`confidence`/`reason` + role fields — unchanged.

---

## 6. Evidence Scoring Engine (proposal §8) — deterministic

`tirf.scoring`. Each evidence item gets a 0–100 **quality score**, a weighted blend of:

**Source reliability** (keyword-classified from the free-text `source`):

| Tier | Match keywords | Score |
|---|---|---|
| SEC Filing | 10-k, 10-q, 8-k, 20-f, 6-k, s-1, sec, edgar, filing | 100 |
| Earnings Call | earnings call, transcript, conference call | 90 |
| Company Guidance | guidance, investor day, management, press release | 85 |
| Industry Research | industry, research report, analyst, semianalysis, gartner, idc | 80 |
| News Source | news, reuters, bloomberg, cnbc, wsj, ft, article | 60 |
| Social Media | twitter, x.com, reddit, stocktwits, social, forum | 20 |
| Unknown | (default) | 50 |

**Recency** (evidence `date` vs report `as_of`):

| Age | Score |
|---|---|
| < 7 days | 100 |
| < 30 days | 80 |
| < 90 days | 60 |
| older / undated | 30 |

**Relevance** (`relevance` field):

| Level | Score |
|---|---|
| direct | 100 |
| related (default) | 70 |
| indirect | 40 |

**Composite:** `item_score = round(0.50·reliability + 0.30·relevance + 0.20·recency)`.
Weights chosen so source authority dominates, relevance second, freshness third —
documented here as the single source of truth. Report-level **evidence quality** =
mean item score; **evidence count** and **min-3 gate** reported separately.

---

## 7. Assumption Tracking Engine (proposal §9)

`tirf` persists each assumption with: `assumption` (key+value text), `agent` (role key),
`ticker`, `version`, `confidence`, `created_at`. Purpose: when a thesis later fails,
the audit can ask *which assumption drove the error*. Stored in `assumptions` table,
keyed to the parent `research_report`.

---

## 8. Research Versioning (proposal §10)

`tirf.repro` + `tirf.store`. A committee research output for a ticker is versioned:

```
report_id : uuid (hex12)
agent     : "committee"   (specialist sub-reports tagged by role key)
ticker    : resolved symbol
version   : MAX(version for ticker) + 1   (deterministic auto-increment)
timestamp : UTC ISO
```

Version is assigned at persist time inside one transaction (no race for the single-operator
runtime). Retrieval can fetch latest or a specific version.

---

## 9. Reproducibility Framework (proposal §11)

`tirf.repro` builds a **manifest** stored with every report:

| Field | Value |
|---|---|
| `data_snapshot` | canonical JSON of the bundle (quote/fundamentals/ta/filings/analyst/earnings) |
| `data_hash` | sha256 of the canonical snapshot |
| `prompt_version` | `roles.PROMPT_VERSION` constant (bumped when prompts change) |
| `agent_version` | `roles.AGENT_VERSION` constant (bumped when the roster/logic changes) |
| `research_version` | the per-ticker version from §8 |
| `as_of` | bundle timestamp |

**Reproducibility check** (deterministic, no LLM): re-running `gather_bundle` for the same
inputs and re-hashing must equal the stored `data_hash`; same prompt/agent versions ⇒ the
run is reproducible *by construction of inputs*. (LLM text may vary; TIRF guarantees the
**inputs and method** are pinned and replayable — proposal's "similar outputs".) A
report's **Reproducibility score** is 100 when all four pins are present and the data hash
is recomputable, else degraded.

---

## 10. Database Design (proposal §15) — `committee.db`

Nine tables. All created idempotently (`CREATE TABLE IF NOT EXISTS`); ALTER-migrated like
`transcript.py`. FK by id (no enforced PRAGMA — matches the existing committee DBs).

1. **research_reports** — `id, report_id, ticker, agent, version, as_of, source, prompt_version, agent_version, data_hash, data_snapshot, final_recommendation, confidence, evidence_quality, explainability, traceability, auditability, reproducibility, challenge_coverage, created_at`
2. **evidence_items** — `id, report_id, role_key, source, source_tier, date, finding, impact, relevance, confidence, reliability_score, recency_score, relevance_score, item_score, created_at`
3. **assumptions** — `id, report_id, role_key, name, value, confidence, created_at`
4. **reasoning_chains** — `id, report_id, role_key, step_no, statement, created_at`
5. **counterarguments** — `id, report_id, role_key, argument, created_at`
6. **source_references** — `id, report_id, role_key, reference, source_tier, reliability_score, created_at`
7. **committee_sessions** — `id, report_id, run_id, ticker, source, debate_on, n_specialists, n_challenges, created_at`
8. **committee_challenges** — `id, report_id, run_id, challenger_key, challenger_title, target_key, target_title, challenge, created_at`
9. **committee_responses** — `id, report_id, challenge_id, responder_key, response, created_at`

---

## 11. Research Dossier (proposal §7)

`tirf.dossier` renders the 11 required sections from the persisted report — Markdown
primary, PDF secondary (via the existing `render_pdf.markdown_to_pdf`):

1. Executive Summary · 2. Investment Thesis · 3. Evidence Summary (scored table) ·
4. Financial Analysis · 5. Industry Analysis · 6. Valuation Analysis · 7. Risks ·
8. Counterarguments · 9. Assumptions · 10. Sources (with reliability) · 11. Final Recommendation.

The dossier is also surfaced as a **TIRF Appendix** appended to the existing 14-section
committee report, so committee members and the operator receive the transparency layer
inline (proposal §12).

---

## 12. Committee Challenge Protocol (proposal §13) & CIO Review (§14)

* **Challenge protocol** maps onto the existing **debate** engine (bear↔bull + risk↔valuation
  cross-exam). TIRF persists each challenge/response into `committee_challenges` /
  `committee_responses`, making the debate auditable.
* **CIO Review Scorecard** (`tirf.review`) — deterministic, computed from the aggregated
  specialist TIRF packages *before* the report is finalized:
  - **Evidence Quality** — mean evidence item score across specialists.
  - **Assumption Quality** — fraction of specialists that stated ≥1 explicit assumption.
  - **Counterargument Coverage** — fraction of specialists meeting the ≥3 gate.
  - **Source Reliability** — mean source-tier reliability across all sources.
  - **Reasoning Consistency** — fraction of specialists with a ≥2-step reasoning chain whose final step is non-empty.

---

## 13. Success Metrics (proposal §17) — computed per report

| Metric | Definition (0–100) |
|---|---|
| **Explainability** | Can a human see *why*? = weighted presence of reasoning + assumptions + a non-empty reason. |
| **Traceability** | Can every conclusion trace to evidence? = scaled by evidence count (≥3 ⇒ full) × evidence quality. |
| **Auditability** | Can the decision be reconstructed later? = persistence complete (report row + children) **and** reproducibility pins present. |
| **Reproducibility** | Same inputs ⇒ similar outputs? = all four repro pins present + data hash recomputable. |
| **Challenge Coverage** | Were meaningful counterarguments considered? = scaled by counterargument count (≥3 ⇒ full) + debate participation. |

A report's **TIRF score** = mean of the five. These are the quantitative acceptance gates.

---

## 14. API / Access (proposal §16, re-homed)

No HTTP server (locked surface decision). Equivalent access:

| Proposal endpoint | TIRF equivalent |
|---|---|
| `POST /research/generate` | runs automatically inside `run_committee`; dev CLI `python -m cio.committee.tirf generate SYMBOL` |
| `GET /research/{id}` | `tirf.store.get_report(report_id)` / dev CLI `... show <report_id>` |
| `GET /research/{id}/evidence` | `tirf.store.get_evidence(report_id)` |
| `GET /research/{id}/assumptions` | `tirf.store.get_assumptions(report_id)` |
| `POST /committee/review` | the CIO review scorecard, produced inline |

Retrieval is also exposed read-only on the existing localhost dev dashboard (optional view).

---

## 15. Module layout

```
cio/committee/tirf/
  __init__.py     exports: build_research_report, score_evidence, validate_report,
                  persist, get_report, render_dossier, cio_review, PROMPT/AGENT versions
  models.py       dataclasses: EvidenceItem, Assumption, ReasoningStep,
                  Counterargument, SourceRef, SpecialistResearch, ResearchReport
  extract.py      parse TIRF keys out of a parsed specialist yaml → models (tolerant)
  scoring.py      evidence scoring engine (§6)
  validate.py     completeness gates + 5 success metrics (§13) + CIO review inputs
  repro.py        data snapshot, hashing, version constants, manifest (§8/§9)
  store.py        committee.db schema + persist + retrieval (§10)
  review.py       CIO Review Scorecard (§12)
  dossier.py      11-section Markdown memo (§11)
  __main__.py     dev CLI: generate / show / list
```

Wiring touch-points (minimal, additive):
* `roles.py` — append the TIRF deliverable instructions to `_BASE_RULES`; add `PROMPT_VERSION`, `AGENT_VERSION`.
* `engine.py::run_specialist` — capture the raw parsed yaml so `extract` can read TIRF keys; attach `research` to the opinion dict.
* `engine.py::run_committee` — after CIO, build + score + validate + persist the `ResearchReport`; attach to `CommitteeResult.tirf`.
* `report.py` — append the TIRF appendix (or call `dossier`).
* `delivery.py` / `__main__.py` — optionally also write the dossier artifact.

---

## 16. Acceptance Criteria (binding — the build is done only when ALL hold)

**A. Zero-cost invariant**
- A1. With debate off, a `run_committee` makes exactly the same number of `ask_role` calls **with and without** TIRF enabled (specialists + moderator + CIO). TIRF adds 0 calls. *(test: call counter)*

**B. Data contract & extraction**
- B1. A well-formed specialist yaml with evidence/assumptions/reasoning/counterarguments/sources parses into the model objects with all fields populated.
- B2. A bare yaml (vote/confidence/reason only) extracts to an empty-but-valid research object — no crash.
- B3. Malformed / partial yaml never raises; missing pieces degrade scores.

**C. Evidence scoring (§6)**
- C1. SEC-filing source classifies to reliability 100; social media to 20; unknown to 50.
- C2. Recency buckets map exactly (<7→100, <30→80, <90→60, older/undated→30) against `as_of`.
- C3. Relevance maps (direct 100 / related 70 / indirect 40); composite uses the documented 0.5/0.3/0.2 weights.

**D. Validation & metrics (§13)**
- D1. The ≥3-evidence and ≥3-counterargument gates flip correctly at the boundary (2 fails, 3 passes).
- D2. A fully-populated report scores ≥80 TIRF; an empty report scores low (<40) — both without raising.
- D3. All five success metrics are present and in [0,100].

**E. Versioning & reproducibility (§8/§9)**
- E1. Two successive persists for the same ticker yield versions N and N+1.
- E2. The data hash is stable for identical bundles and differs when the bundle changes.
- E3. The reproducibility manifest carries all four pins; Reproducibility score = 100 when complete.

**F. Persistence & retrieval (§10/§14)**
- F1. Persisting a report writes the parent row + all child rows; `get_report` round-trips it.
- F2. `get_evidence` / `get_assumptions` return the persisted children.
- F3. All store functions are never-raises and run against a monkeypatched temp DB.

**G. Challenge protocol (§12)**
- G1. A debate result persists challenges and responses linked to the report; counts match the exchanges.

**H. CIO review (§12)**
- H1. The scorecard returns the five sub-scores in [0,100], computed from aggregated specialist packages.

**I. Dossier (§11)**
- I1. The dossier renders all 11 required section headers from a persisted report.
- I2. Missing data renders `_Insufficient data._`, never a crash.

**J. Integration & non-regression**
- J1. `run_committee` end-to-end (ask_role monkeypatched to TIRF-rich yaml) attaches a populated `CommitteeResult.tirf` and persists it.
- J2. The existing committee report still builds and now contains the TIRF appendix headers.
- J3. **The entire pre-existing test suite still passes** (no regression).

**K. Docs**
- K1. This PRD and the Test Plan (`docs/TIRF-TEST-PLAN.md`) exist and match the build.

---

## 17. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Larger specialist output (more tokens/turn) | Accepted — TIRF's value is verbosity-for-transparency; **call count** (the locked ceiling) is unchanged. Prompts cap counterarguments/reasoning length. |
| LLM omits TIRF keys | Extractor is tolerant; validation **measures** the gap rather than failing; gates surface low scores. |
| Persistence failure mid-run | Never-raises store; a TIRF failure is logged and the committee run/report still completes. |
| DB growth | Reuse the dev-capture pruning posture; reports are small structured rows. |
| Evidence carrying figures vs figures firewall | TIRF figures are report-scoped + versioned (auditable), never written to `mem_notes`; the firewall on durable memory is untouched. |
