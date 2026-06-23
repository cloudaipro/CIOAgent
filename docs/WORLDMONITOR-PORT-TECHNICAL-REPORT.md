# WorldMonitor → CIOAgent Feature Port — Technical Report

**Author:** Arch (Architect)
**Date:** 2026-06-22
**Branch:** `feat/worldmonitor-port` (5 commits, unpushed)
**Scope:** Port reusable capabilities from the vendored `worldmonitor/` codebase into CIOAgent
**Status:** Complete — 8 features shipped, fully tested, 1 follow-up deferred (F10 / KG-10)

---

## 1. Executive Summary

A deep review of the vendored `worldmonitor/` project (a TypeScript global-intelligence dashboard)
identified five reusable algorithms and one curated dataset that fit CIOAgent's scope (accounting,
stock-market, and inventory tooling for a solo operator). Because the two codebases share no stack —
worldmonitor is a browser SPA on Vercel edge functions + Railway + Redis + Tauri; CIOAgent is a
Python/pandas/SQLite CLI with a server-rendered dashboard — the work ported **algorithms and curated
data, not code**.

Eight features were delivered across three phases, plus one English-only refinement and two fixes
discovered during testing:

| Phase | Features |
|-------|----------|
| A — zero new deps/keys | F1 source-tier expansion · F6 insider transactions · F2 data-source freshness monitor |
| B — news + alerting | F3 GDELT news source · F9 alert dedup/cooldown · F5 news-spike alert |
| C — brief + regime | F8 FRED yield-curve/regime · F7 deterministic daily market brief |
| Post-test | GDELT English-only filter · 2 bug fixes |
| D — convergence | F10 cross-source convergence signal (KG-10 follow-up, built on request) |

**Outcome:** 28 files changed (+1691 / −18), 8 new modules, ~45 new tests. Full suite on the correct
interpreter: **1197 passed, 6 skipped, 0 failed.** Live integration against real FRED/Finnhub/GDELT
endpoints verified. Every external source is config-gated and offline-safe.

---

## 2. Background & Objective

The operator asked three questions of the worldmonitor codebase:
1. Can we add **more news sources**?
2. Can we add a **worldmonitor-style UI** to the dashboard?
3. Can we adopt any **alerts** from worldmonitor?

The review answered all three concretely while rejecting the large fraction of worldmonitor that is
either wrong-stack (maps/globe/WebGL, Tauri desktop, proto/sebuf RPC, edge+Redis infra) or off-scope
(geopolitical/military/maritime/aviation/wildfire/cyber intelligence, 24-language i18n, country
instability index).

---

## 3. Source Review — What Was Evaluated

`worldmonitor/` is a ~v2.8 TypeScript SPA. Key subsystems reviewed:

- `src/services/` (~180 modules) — news/RSS, market, breaking-news alerts, signal aggregation,
  data-freshness, trending-keywords (spike detection), daily-market-brief.
- `shared/` — curated JSON datasets: `source-tiers.json` (255 outlets scored 1–3), `stocks.json`,
  `commodities.json`, `sectors.json`.
- `server/worldmonitor/` + `api/` — domain gateways, market composite (Fear & Greed), macro signals.
- `.env.example` — 65+ provider integrations.

The transferable nucleus (after discarding wrong-stack/off-scope material): the **RSS source-tier
table**, the **data-freshness tracker** (`data-freshness.ts`), the **trending-keyword spike detector**
(`trending-keywords.ts`), the **breaking-news alert discipline** (`breaking-news-alerts.ts`), the
**rule-based daily brief** (`daily-market-brief.ts` `buildRuleSummary`), and the idea of a **macro
regime line** (Fear & Greed / yield curve).

---

## 4. Feature Selection & Rationale

| ID | Feature | worldmonitor origin | CIOAgent landing | Keyless? |
|----|---------|---------------------|------------------|----------|
| F1 | Source-tier expansion | `shared/source-tiers.json` | `cio/data/source_policy.py` | ✅ |
| F2 | Data-source freshness monitor | `data-freshness.ts` | `cio/data/freshness.py` + dashboard | ✅ |
| F3 | GDELT news source | (new; worldmonitor uses GDELT elsewhere) | `cio/data/gdelt.py` | ✅ |
| F5 | News-spike alert | `trending-keywords.ts` | `cio/watchlist_monitor/spike.py` | ✅ |
| F6 | Insider transactions | `insider-transactions.ts` | `cio/data/finnhub.py` | needs Finnhub key |
| F7 | Deterministic daily brief | `daily-market-brief.ts` | `cio/watchlist_monitor/report.py` | ✅ |
| F8 | FRED regime / yield curve | market composite / macro-signals | `cio/data/fred.py` | needs FRED key |
| F9 | Alert dedup/cooldown | `breaking-news-alerts.ts` | `cio/alerts.py` | ✅ |

**Deferred:** F10 cross-source convergence (downstream of F5+F6). **Out of scope:** F4 RSS feeds
(GDELT covers the news need with no XML parser and no key).

---

## 5. Architecture & Conventions

All work conformed to the existing `cio/data/` contract (stated in `cio/data/__init__.py`):

- **Config-gated + offline-safe.** No key/flag ⇒ empty result, **zero network calls**. Keeps the test
  suite and CI fully offline by default.
- **Reused infrastructure:** `_http.get_json` + `RateLimiter` (`cio/data/_http.py`) and
  `_cache.read/write` (`cio/data/_cache.py`). No new HTTP stack.
- **No new dependencies.** GDELT and FRED are JSON over the existing `httpx`; no `feedparser`.
- **Zero LLM in hot paths.** The F5 spike detector and F7 brief are pure rules — respecting the ~20-call
  committee cost ceiling (KG-7).
- **Owner-locked file respected.** `source_policy.py` (the evidence-integrity authority) was extended
  additively only — no change to `classify()`, the fail-closed default, `is_verified()` corroboration,
  or the claim taxonomy.
- **Durable state in SQLite** via `cio/db.py` (the alert cooldown table), mirroring `econ_calendar`'s
  "dates are DATA, not memory" rule.

---

## 6. Implemented Features (Detail)

### F1 — Source-tier expansion (`cio/data/source_policy.py`)
Extended `TIER_2_REPUTABLE` and `TIER_3_LOW_TRUST` with finance outlets. The outlet **universe** was
cross-checked against `worldmonitor/shared/source-tiers.json`, but the **tiers were assigned by
CIOAgent's finance-evidence axis, not worldmonitor's numbers** — a load-bearing decision (see §7).
Added Tier-2: marketwatch, nikkei, axios, economist, fortune, morningstar, theinformation,
businessinsider, investors.com. Added Tier-3: benzinga, marketbeat, investorplace, tipranks,
thestreet, finbold, simplywall.st, wallstreetzen. No logic change; fail-closed default preserved.

### F6 — Insider transactions (`cio/data/finnhub.py`)
`insider_transactions(symbol)` over the free Finnhub `/stock/insider-transactions` endpoint, plus
`insider_net(symbol)` → `{buy_count, sell_count, net_shares, cluster_buy}`. Only open-market purchases
(transaction code `P`, positive change) count as buys; grants (`A`), option exercises (`M`), and gifts
(`G`) are excluded. `cluster_buy` flags ≥3 distinct insiders buying in-window — the conviction signal.
Wired into the committee data bundle (`cio/committee/bundle.py`) as a new `INSIDER:` line; `_external()`
now returns a 4-tuple.

### F2 — Data-source freshness monitor (`cio/data/freshness.py`)
A source **heartbeat** tracker — distinct from the agent's price-bar staleness. Answers "when did each
source last return data?" Status buckets (worldmonitor thresholds): `fresh` <15m, `stale` <2h,
`very_stale` ≥2h, `no_data`, `error`. `summary()` returns per-source rows plus a **worst-wins rollup
over the required sources** (yfinance, finnhub). State is a small atomic-replace JSON file (cross-process
safe for the dashboard). Heartbeats recorded by the finnhub, edgar, and yfinance fetchers. Surfaced as a
new **"Data Health"** dashboard tab (`cio/dashboard/views.py` `render_health` + `/health` route in
`server.py`). Purpose: stop a panel from showing a confident "all clear" over silently-missing inputs.

### F3 — GDELT news source (`cio/data/gdelt.py`)
Keyless GDELT DOC 2.1 API. `headlines(query, hours, limit)` (ArtList) and `tone_volume(query, hours)`
(ToneChart → `{volume, avg_tone}`). The one source **enabled by default** (`CIO_GDELT_ENABLED=0` to
disable). English-only by default via a `sourcelang:eng` filter (see §6 refinement). Cached 1h,
rate-limited ~1 req/s, freshness-tracked, offline-safe.

### F9 — Alert dedup/cooldown (`cio/alerts.py` + `alert_cooldown` table)
Port of worldmonitor's breaking-news discipline: a normalized per-event key (title + source + host
hash), per-event cooldown, and a global rate gate. Backed by SQLite so it survives bot restarts.
`claim()` is the atomic "should I send this?" call. Used by F5 and available to retrofit any alert path.

### F5 — News-spike alert (`cio/watchlist_monitor/spike.py` + scheduler)
Unscheduled-catalyst detector — the complement to `econ_calendar`'s dated catalysts. A spike fires when
the 2-hour article count ≥ `MIN_COUNT` **and** (≥ `MULT` × the 7-day baseline **or** there was no prior
coverage) **and** ≥ `MIN_SOURCES` distinct sources. **Key adaptation:** worldmonitor persists a rolling
timestamp window because the browser ingests RSS itself; CIOAgent instead *queries* GDELT, which already
aggregates volume over time — so GDELT is the history store and no rolling-window table is needed. The
scheduler job `news_spike_alert` (off by default; `CIO_SPIKE_EVERY_MIN=<min>` to arm) dedups via
`cio.alerts` and pushes Telegram. Zero LLM.

### F8 — FRED regime / yield curve (`cio/data/fred.py`)
Treasury constant-maturity yields (DGS2/10/30) → `yield_curve()` with `spread_2s10s` and an `inverted`
flag; ICE BofA HY OAS (BAMLH0A0HYM2) → `hy_spread()`; and a deterministic `regime_label()`
(risk-on / caution / risk-off) from curve + credit. `FRED_API_KEY`-gated: unset ⇒ `{}` with no network.
Cached 6h, freshness-tracked.

### F7 — Deterministic daily market brief (`cio/watchlist_monitor/report.py`)
`build_market_brief()` — a rules-only snapshot (port of worldmonitor's `buildRuleSummary`, zero LLM).
Breadth (leaders/neutral/defensive) is derived from the WMA assessments' existing `overall_status`
(no extra TA fetch, no `pandas_ta` dependency). A risk-on/off bias line combines breadth with the FRED
regime, where a risk-off macro backdrop can only *darken* a green-breadth read, never the reverse.
The macro line renders only when FRED is configured. Rendered as a new "Market Brief" section in
`build_briefing` (the daily pre-market WMA path that already has assessments).

### F10 — Cross-source convergence (`cio/convergence.py`)
The WorldMonitor cross-stream idea applied to a single security: five independent deterministic streams
pointing the same way is stronger evidence than any one alone. Blends (1) TA composite, (2) analyst-rating
delta (this period vs last, from `analyst_recs_history` — a cache hit on the same endpoint as
`analyst_recs`), (3) earnings beat/miss, (4) insider cluster (F6 `insider_net`), and (5) news spike + tone
(F5 `detect_spike`, now carrying `avg_tone`). `score` is the weighted mean over the **active** factors
(insider and TA weight 2; the rest 1); `conviction` is a separate function of active-count × agreement, so
absent sources lower conviction without biasing direction. Returns
`{score, label, conviction, agreement, factors}`. Wired into the committee bundle as a `CONVERGENCE:`
line (reusing the TA composite + insider already fetched; `CIO_CONVERGENCE=0` disables; per-factor weights
tunable via `CIO_CONV_W_*`). Zero LLM, offline-safe, each factor self-gating. It deliberately does **not**
plug into TIRF — TIRF scores LLM-research evidence, whereas convergence is a pre-committee deterministic
filter. Live-verified on AAPL: bullish TA + earnings beat vs. analyst downgrade + insider net-selling →
`mixed / low conviction`, correctly refusing to manufacture conviction from conflicting streams.

### Refinement — GDELT English-only (`cio/data/gdelt.py`)
GDELT is multilingual; a bare query returned foreign-language headlines and skewed the spike volume
baseline. A `sourcelang:` filter (verified live: `eng` = English) is appended to **both** `headlines`
and `tone_volume` so displayed stories and spike volume share one language scope. Default `eng`;
`CIO_GDELT_LANG` overrides (e.g. `fra`, `spa`, `deu`); empty = all languages. A caller-supplied
`sourcelang:` is not double-filtered; the cache key varies with the applied query.

---

## 7. Key Design Decisions

**D1 — F1 tiers use CIOAgent's axis, not worldmonitor's numbers.** worldmonitor's `source-tiers.json`
scores reliability for *geopolitical-news* intelligence: it ranks **SEC = 3** and **Yahoo Finance = 4**.
CIOAgent's `source_policy.py` correctly treats `sec.gov` as **Tier-1 PRIMARY** (it backs material
financial facts). Importing worldmonitor's numbers would have demoted the single most important
financial primary source. We took the *outlet universe* (a vetted checklist of real finance outlets) but
assigned tiers by CIOAgent's finance-evidence policy. A regression test guards this
(`test_finance_evidence_axis_not_worldmonitor_axis`).

**D2 — F5 uses GDELT as the history store.** A literal port would persist a rolling 2-hour timestamp
window. Since GDELT already aggregates article volume over time, querying it for both the 2h window and
the 7-day baseline is simpler and more accurate. Only the alert cooldown (F9) needs persistence.

**D3 — F7 reuses WMA assessments for breadth, not `signal_state`.** The plan initially suggested pulling
breadth from `cio/stock/signal_state.py`, but that imports `pandas_ta` and would re-fetch TA the WMA
already computed. Deriving breadth from the assessments' `overall_status` is cheaper, dependency-free,
and testable on any interpreter.

**D4 — F7 lands in `build_briefing`, not `daily_digest`.** The plan named `daily_digest`, but that is a
*portfolio* digest with no watchlist assessments. The pre-market WMA briefing is where market/breadth
context belongs and where the assessments already exist.

**D5 — F8 built offline-safe and dormant.** Per owner decision, FRED was built to activate the moment
`FRED_API_KEY` is set, with no behavioral change until then — matching every other opt-in source.

**D6 — F10 built as a pre-committee filter, not a TIRF evidence item.** Cross-source convergence was
deferred until F5+F6 existed, then built on request. It is deliberately kept out of TIRF (the LLM-research
evidence/scoring layer) — convergence is a deterministic signal that sits *in front of* the committee and
feeds the bundle, a different layer. It reuses inputs the bundle already fetches to stay cheap.

---

## 8. Testing

### 8.1 Critical finding — interpreter trap (KG-11)
The shell's PATH `python` is `~/venv/PseCo` (Python 3.11, **no `pandas_ta`**), which silently
cannot-collect every stock/viz test (~9 phantom "failures"). The project's real interpreter is the repo
**`.venv` (Python 3.12, with `pandas_ta` + `yfinance`)**. All verification was redone on it; future runs
must use `.venv/bin/python -m pytest`.

### 8.2 Test plan & results

| Test | Method | Result |
|------|--------|--------|
| T1 Full suite | `.venv` py3.12 pytest | **1197 passed, 6 skipped, 0 failed** |
| T2 Static/imports | py_compile + import every changed module | clean |
| T3 Offline-safety | keys unset + `httpx.get` forced to raise | PASS — all sources empty, zero network |
| T4 Live integration | real FRED/Finnhub/GDELT calls | PASS (one transient GDELT 429 degraded to `[]`) |
| T5 End-to-end | live brief regime, dashboard health, bundle insider | PASS |

### 8.3 Live data confirmed
- **FRED:** 2y 4.19 / 10y 4.46 / 30y 4.90, 2s10s **+27bps** (not inverted), HY **266bps** → **risk-on**.
- **Brief:** *"Bias: leaning risk-on … yield curve normal (2s10s +27bps) … HY OAS 266bps"* — the
  regime-lift logic verified against live data.
- **Finnhub insider:** AAPL 13 sells, −607,703 net shares.
- **GDELT:** live English parse confirmed (`sourcelang:eng` → 5/5 English); 429s degrade to `[]`.

### 8.4 Bugs found & fixed during testing (commit `2f35a6b`)
1. **Insider freshness gap (real, introduced by F6).** `insider_transactions` did not record a freshness
   heartbeat, so an insider-only call left the `finnhub` source reading `no_data` despite success. Fixed
   by adding the heartbeat; verified `fresh`.
2. **Stale viz nav test (pre-existing).** `test_viz.py::test_dashboard_form_and_nav` unpacked `_NAV` as a
   flat `(label, href)` list, but `_NAV` became hierarchical in commit `0c62665` (the sidebar
   conversion). The test had been invisible on the 3.11 env (viz tests can't collect without
   `pandas_ta`); on 3.12 it failed with a `TypeError`. The app nav was always correct — the test was
   updated to walk both levels.

---

## 9. Commit History

```
2cccb9b feat(gdelt): English-only headlines by default (sourcelang filter)
2f35a6b fix(data,test): insider freshness heartbeat + stale viz nav test + env docs
ede94a9 feat(brief): FRED regime + deterministic market brief (Phase C)
faaeec9 feat(news): GDELT source + news-spike alerting (Phase B)
1cb14b2 feat(data): source tiers, insider-tx, freshness monitor (Phase A)
```
Branch `feat/worldmonitor-port`: **28 files changed, +1691 / −18.** Unpushed, unmerged.

New modules: `cio/data/freshness.py`, `cio/data/gdelt.py`, `cio/data/fred.py`, `cio/alerts.py`,
`cio/watchlist_monitor/spike.py`, and tests `test_freshness.py`, `test_alerts.py`, `test_spike.py`.

---

## 10. Configuration & Activation Guide

All sources are dormant until configured. Relevant `.env` keys/knobs (documented in `.env.example`):

| Variable | Default | Effect |
|----------|---------|--------|
| `FINNHUB_API_KEY` | unset | Enables insider transactions (F6) + analyst/news/earnings |
| `FRED_API_KEY` | unset | Enables yield-curve/regime (F8) and the brief's macro line (F7) |
| `CIO_GDELT_ENABLED` | `1` (on) | GDELT news (F3); set `0` to disable |
| `CIO_GDELT_LANG` | `eng` | GDELT coverage language; empty = all languages |
| `CIO_SPIKE_EVERY_MIN` | off | Arm the news-spike scheduler (F5); e.g. `30` = every 30 min |
| `CIO_SPIKE_MIN_COUNT` / `_MULT` / `_MIN_SOURCES` / `_COOLDOWN_MIN` | 5 / 3 / 2 / 30 | Spike thresholds |

The dashboard **Data Health** tab (F2) is always available at `/health`.

---

## 11. Known Gaps & Follow-ups

- **KG-10 — RESOLVED.** F10 cross-source convergence built (`cio/convergence.py`), wired into the
  committee bundle. See §6.
- **KG-11 — Interpreter trap.** Always run tests with `.venv/bin/python` (py3.12 + pandas_ta), not the
  PATH `python` (PseCo 3.11), which silently skips stock/viz tests.
- **Not built:** F4 RSS feeds (GDELT covers news with no parser/key).

---

## 12. Conclusion

The three operator questions are answered: **more news** (GDELT, keyless, English-only, plus 17 finance
outlets added to the trust table); a **worldmonitor-style dashboard surface** (the native, server-rendered
Data Health tab — their data ideas rebuilt in CIOAgent's stack, not their WebGL components); and **alerts**
(news-spike on watchlist names with proper dedup discipline). All eight features are offline-safe,
config-gated, zero-LLM where it matters, and verified against both the full test suite (1197 passing on
the correct interpreter) and live external data. The branch is ready to push and open as a PR.
