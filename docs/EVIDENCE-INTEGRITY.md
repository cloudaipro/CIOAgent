# Evidence Integrity — Technical Report

**Feature:** Source-trust layer + primary-source tools + provenance observability
**Status:** Shipped (PR #12, merged into `main` 2026-06-07) + a follow-up committee-path log patch
**Authors:** Three Man Team — Arch (design + policy), Bob (build), Richard (review)
**Date:** 2026-06-06

---

## 1. Problem

The conversational agent told the user that a Neurocrine pipeline drug (`NBI-1117568`) had a
Phase-3 **MDD** result of `MADRS −35%, p<0.001`. Every part of that is wrong: the drug is for
**schizophrenia**, the Phase-3 trial has **no published readout**, and the `MADRS −35%` figure
**does not exist**. The agent had relayed it faithfully from **monexa.ai** — an AI-aggregator
site that itself hallucinated and conflated the drug with its indication.

The dangerous property: the failure looked *well-sourced*. The agent had genuinely run
`web_search` → `web_scrape`, the cited URL was real, and the answer carried a verified Sources
footer. The user invests on these answers, so a confidently-wrong, citation-dressed claim is the
worst-case output.

### 1.1 Why the obvious fixes don't work

| Candidate fix | Why it fails this bug |
|---|---|
| **Scrape-before-cite enforcement** (only cite pages you actually fetched) | The agent *did* fetch monexa.ai. The call passes. Answer still wrong. |
| **Claim-vs-page verifier** (does the claim match the scraped text?) | The claim *did* match monexa.ai's text — the page literally said `MADRS −35%`. Verifier returns ✅. Still wrong. |
| **Provenance / inference labeling** | Helps a *different* hole (model's own guesses presented as fact), but the monexa.ai claim was presented as sourced, not inferred. |

**Root cause (correctly diagnosed):** the agent **trusted the wrong KIND of source for a material
fact**. A clinical-trial endpoint was sourced from a finance/AI-aggregator page instead of the
trial registry. This is a *source-provenance* problem, not a scrape-discipline or text-fidelity
problem. The fix must constrain **which sources may back which claims**.

A second, independent hole was also present: the agent freely mixed its own computed/estimated
numbers (e.g. "INGREZZA ≈ 75% of revenue", self-assigned price targets, position sizing) into the
factual stream with no marker, so the user could not tell fact from inference.

---

## 2. Design

Four mechanisms, deterministic-first to respect the project's `$0/run` and committee cost ceiling
(KG-7). Only the optional verifier costs an LLM call.

### 2.1 Source tiering (the core fix)

Every web domain is classified into a trust tier at fetch time:

- **Tier 1 — PRIMARY**: SEC/EDGAR, clinicaltrials.gov, FDA, and the *issuer's own* domain/IR site.
  May back any material fact on its own.
- **Tier 2 — REPUTABLE**: Reuters, AP, Bloomberg, WSJ, FT, CNBC, Barron's, Yahoo (quotes/analyst
  panel), and PR-wires (PRNewswire/Businesswire/Globenewswire). May back a material fact only with
  corroboration.
- **Tier 3 — LOW-TRUST**: AI aggregators (**monexa.ai**, any unlisted `*.ai`), content farms
  (Motley Fool, Zacks), forums (Reddit, StockTwits). **Can never back a stated fact** — read for
  leads only.

**Locked owner decisions** (ratified in session):
- PR-wires are **Tier 2**, not Tier 1 (company-authored but wire-distributed; needs the matching
  primary).
- Unknown / unlisted domains **fail closed to Tier 3** — this is precisely what stops monexa.ai
  (an unlisted domain) from backing a fact.
- Price/valuation **figures** never come from the web at all — stock tools only (pre-existing rule,
  preserved).

### 2.2 Claim-class routing

A material fact must come from the *right kind* of authority, not merely any Tier-1 page:

| Claim class | Authoritative source |
|---|---|
| Clinical (phase, indication, endpoint, p-value, approval) | clinicaltrials.gov / FDA / company release |
| Financial (revenue, EPS, margin, guidance, cash, debt) | SEC/EDGAR / company earnings release |
| Corporate action (M&A, dividend, buyback, split) | company IR + a Tier-2 wire |
| Regulatory (FDA decision, PDUFA) | FDA / company release |
| Analyst (rating, price target) | Finnhub recs / Yahoo analyst panel |

"`NBI-1117568` is an MDD drug" is a **clinical** claim → its authority is the registry → the
registry says **schizophrenia** → the false claim cannot survive. A finance page is the *wrong
class* for a clinical endpoint regardless of its tier.

### 2.3 Corroboration

A material fact is **verified** iff backed by **≥1 Tier-1 source OR ≥2 independent Tier-2
sources**; otherwise it is labelled `single-source / unverified`. Tier-3 never contributes.

### 2.4 Provenance labeling + scrape floor

Every factual line carries its citation; anything the model reasoned/estimated itself is prefixed
`[inference]`. The Sources footer prints each source's tier and an overall corroboration verdict.

---

## 3. Implementation

### 3.1 The locked policy — `cio/data/source_policy.py`

Single source of truth. All consumers import it; there is no second tier list.

- `class Tier(IntEnum)` — `PRIMARY=1`, `REPUTABLE=2`, `LOW_TRUST=3`.
- `TIER_1_PRIMARY`, `TIER_2_REPUTABLE`, `TIER_3_LOW_TRUST` domain sets; `DEFAULT_TIER = LOW_TRUST`.
- `classify(host, issuer_domains=None) -> Tier` — registrable-suffix match; promotes the issuer's
  own domain to Tier 1; forces unlisted `*.ai` and any unknown host to Tier 3 (fail-closed).
- `class ClaimClass`, `MATERIAL_CLASSES`, `REQUIRED_SOURCE` — the claim taxonomy and routing map.
- `is_verified(tiers: list[Tier]) -> bool` — the corroboration rule (1×T1 or 2×T2).

### 3.2 Primary-source tools wired into the agent — `cio/agent.py`

The agent reached for monexa.ai because it had **no primary tool to reach for**. Five tools were
added to `CIO_TOOLS` (count 29 → 34), each config-gated and offline-safe:

| Tool | Backend | Source tier |
|---|---|---|
| `sec_filings` | `cio/data/edgar.py` (SEC EDGAR API) | Tier 1 |
| `analyst_ratings` | `cio/data/finnhub.py` | Tier 2 |
| `earnings_info` | `cio/data/finnhub.py` | Tier 2 |
| `company_profile` | `cio/data/finnhub.py` (new `company_profile()`) | Tier 1 (issuer identity) |
| `clinical_trials` | `cio/data/clinicaltrials.py` (new; clinicaltrials.gov API v2) | Tier 1 |

`company_profile` returns the issuer `weburl`, whose host is registered into a **per-scope**
issuer-domain set (`_ISSUER_DOMAINS`) so the company's own IR pages resolve to Tier 1.

### 3.3 Provenance in the web tools

- `web_search` / `web_scrape` stamp each result/page with `⟨TIER n LABEL⟩` in-band (plus a
  Tier-3 "leads only — cannot back a stated fact" warning), so the model cannot claim ignorance of
  a source's trust level.
- The `_SOURCES` registry stores a `tier` per entry, computed via `_classify_url(url, scope)`.
- `_append_sources` prints `(Tier n LABEL)` per source line and one corroboration verdict
  (`✅ corroborated` / `⚠️ single-source / unverified`) from `is_verified`, emitted only when ≥1
  source was cited.

### 3.4 System prompt

A new **EVIDENCE INTEGRITY** block in `SYSTEM_PROMPT` encodes §2.1–§2.4: material-fact →
right-class-Tier-1 routing, Tier-3-never-backs-a-fact, web-snippet ≠ evidence, corroboration, and
`[verified]`/`[inference]` labeling. For a genuine buy/sell decision it instructs the agent to
offer the real committee rather than free-handing a verdict.

### 3.5 Optional verifier — `CIO_VERIFY_CLAIMS`

A Haiku post-pass (`_run_verifier`, wired into `CIOAgent.ask`) that flags material claims lacking
right-class / corroborated backing. **Default off** (one extra call per turn), at most one call per
turn, skipped when no material/web claim is present. Tracked as KG-8.

---

## 4. Observability

So an operator can confirm from logs that the primary sources actually fire (and were configured),
a dedicated `cio.evidence` logger emits one INFO line per primary-source access.

- **Chat-agent path** (`cio/agent.py`): each of the 5 tools logs on every path including the
  empty-input guard — `tool=… symbol=… configured=… source=… <count>`.
- **Committee path** (`cio/committee/bundle.py::_external`): the committee gathers EDGAR/Finnhub
  **directly via the data layer**, not through the agent's MCP tools, so it was originally invisible
  in the evidence stream. It now emits the same lines tagged `via=committee`. Example:

```
tool=sec_filings     symbol=NBIX configured=False source=EDGAR  filings=0   via=committee
tool=analyst_ratings symbol=NBIX configured=True  source=Finnhub found=True via=committee
tool=earnings_info   symbol=NBIX configured=True  source=Finnhub found=True via=committee
```

### 4.1 Central logging — `cio/logsetup.py`

`configure_logging()` (called by `cio/bot.py` and `cio/dashboard/__main__.py`, replacing their old
`basicConfig`) sets up console logging plus an **optional date-based file** at
`logs/cio-YYYY-MM-DD.log` (one file per day). `apply_file_logging()` can add/remove the handler
live; `current_log_file()` reports the active path.

**Security note:** `httpx` logs each request URL at INFO, and the Finnhub URL contains the API
token as a query parameter — which would otherwise be written to the on-disk log. `configure_logging`
caps `httpx`/`httpcore`/`urllib3`/`anthropic` to WARNING so the token never reaches a persisted file.
(Verified: token absent from file, evidence lines retained.)

### 4.2 Dashboard toggle

The Configure tab gained a **Logging** section showing ON/OFF, the active dated file, and the
directory, with an Enable/Disable toggle. The choice is persisted in
`data/dashboard_settings.json` (new `cio/dashboard/settings.py`, atomic write) and applied live to
the running process. The env var `CIO_LOG_TO_FILE` overrides and locks the toggle.

---

## 5. Configuration

| Variable | Effect | Default |
|---|---|---|
| `CIO_SEC_UA` | Enables EDGAR (SEC fair-access User-Agent) | unset → EDGAR disabled |
| `FINNHUB_API_KEY` | Enables Finnhub (analyst/earnings/profile) | unset → Finnhub disabled |
| `CIO_CT_TIMEOUT` | clinicaltrials.gov request timeout (s) | 20 |
| `CIO_VERIFY_CLAIMS` | `1` enables the Haiku claim verifier | `0` (off) |
| `CIO_LOG_TO_FILE` | `1` mirrors logs to a dated file (overrides + locks the dashboard toggle) | unset → dashboard setting |
| `CIO_LOG_DIR` | Log directory | `<project>/logs` |

All external sources are **config-gated and offline-safe**: unset keys yield empty results with no
network call, and tools return an explicit "not configured" message — the whole suite and offline
operation behave exactly as before.

---

## 6. Testing

- `tests/test_source_policy.py` — `classify` fail-closed (monexa.ai/unlisted-`.ai`/unknown → Tier 3),
  issuer promotion + subdomain match, PR-wire = Tier 2, `is_verified` matrix, footer tier labels +
  verdicts, per-scope issuer isolation (no cross-chat leak), verifier-off no-op.
- `tests/test_tool_wiring.py` — all 5 tools present in `CIO_TOOLS`, `mcp__cio__`-prefixed, in
  `build_options().allowed_tools`, descriptions name their source; tool→data-layer wire proven per
  tool (mock the data fn, assert call + output threading); evidence logging on configured /
  unconfigured / empty-input paths; committee-path `via=committee` logging; `_ev` no trailing space.
- `tests/test_logsetup.py` — dated-file creation/on/off/idempotency, env-override-vs-setting,
  httpx token suppression, settings round-trip, Configure-tab render (toggle / env-lock states).

**Suite:** 381 passing. 12 failures are **pre-existing and unrelated** — NIM model-routing +
translator resolution config (`test_fallback_chain`, `test_committee::TestModelsConfig` /
`TestAskRoleRouting`, `test_pdf_report::TestResolveTranslator`); they fail with this work removed
too. Tracked as **KG-9**.

**Review:** the Haiku reviewer ran three cycles, finding and confirming the fix of two blockers
(a per-scope `_ISSUER_DOMAINS` cleanup leak; an unlogged empty-query guard path) plus minor items,
ending in SHIP.

---

## 7. Limitations & follow-ups

- **No system makes an LLM 100% accurate.** What this buys: a material fact can only *originate*
  from a primary, claim-appropriate, corroborated source, and everything else is *visibly labelled*
  inference. The user can always separate fact from guess — the exact trust failure that started this.
- The deterministic layer cannot parse arbitrary prose to prove every sentence's class; it constrains
  the *sources available* and *labels provenance*. The optional `CIO_VERIFY_CLAIMS` pass narrows the
  residual gap at the cost of one Haiku call.
- **KG-8** — verifier is off by default (cost). **KG-9** — pre-existing NIM/translator routing test
  failures, unrelated, to be triaged separately.
- For an actual investment decision, the multi-agent committee (`run_committee`) remains the verified
  gold path; the conversational agent is advisory.

---

## 8. Changed files

**New:** `cio/data/source_policy.py`, `cio/data/clinicaltrials.py`, `cio/logsetup.py`,
`cio/dashboard/settings.py`, `tests/test_source_policy.py`, `tests/test_tool_wiring.py`,
`tests/test_logsetup.py`.

**Modified:** `cio/agent.py`, `cio/data/finnhub.py`, `cio/data/__init__.py`,
`cio/committee/bundle.py`, `cio/bot.py`, `cio/dashboard/__main__.py`, `cio/dashboard/views.py`,
`cio/dashboard/server.py`, `tests/test_committee.py`, `tests/test_panel.py`, `.gitignore`,
`.env.example`.
