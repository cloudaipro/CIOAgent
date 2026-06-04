
# PRD Update

# AI Investment Committee Agent System

## Version 1.1

---

# New Agent

## Geopolitical & Macro Intelligence Agent

### Purpose

Monitor global macroeconomic and geopolitical developments and evaluate their impact on:

* Stocks
* ETFs
* Sectors
* Industries
* Investment portfolios

---

## Mission

Provide early warning of external risks and opportunities that may materially affect investment performance.

---

## Responsibilities

### Geopolitical Monitoring

Track:

* Middle East conflicts
* Russia–Ukraine developments
* Taiwan Strait developments
* US–China relations
* Trade wars
* Sanctions
* Export controls
* Political instability

---

### Commodity Monitoring

Track:

* Brent Crude
* WTI Crude
* Natural Gas
* Gold
* Copper
* Lithium
* Rare Earths

---

### Currency Monitoring

Track:

* USD
* EUR
* JPY
* CNY
* CAD

Evaluate:

* FX volatility
* Currency trends
* Cross-border impacts

---

### Global Economic Monitoring

Track:

* GDP
* CPI
* PPI
* PMI
* Interest rates
* Treasury yields

---

### Supply Chain Monitoring

Assess impacts to:

* Semiconductors
* Energy
* Manufacturing
* Transportation

---

## Outputs

```yaml
macro_environment:
  supportive

geopolitical_risk:
  medium

commodity_risk:
  low

major_events:
  - Israel-Iran escalation
  - US export restrictions

affected_sectors:

  positive:
    - Energy
    - Defense

  negative:
    - Airlines
    - Consumer Electronics
```

---

# Architecture Update

Previous:

```text
CIO
 ├─ Market
 ├─ Equity
 ├─ Industry
 ├─ Valuation
 ├─ Quant
 ├─ ETF
 ├─ Risk
 └─ Catalyst
```

Updated:

```text
CIO
 │
 ├─ Market Intelligence Agent
 ├─ Geopolitical & Macro Intelligence Agent
 ├─ Equity Research Agent
 ├─ Industry Intelligence Agent
 ├─ Valuation Agent
 ├─ Quantitative Agent
 ├─ ETF Research Agent
 ├─ Risk Management Agent
 └─ Catalyst Agent
```

---

# Investment Committee Update

## New Required Debate Topic

Every investment committee session must evaluate:

### Macro Impact

Questions:

```text
Could current macro conditions
invalidate the investment thesis?
```

---

### Geopolitical Impact

Questions:

```text
Could current geopolitical developments
materially impact earnings,
valuation, or market sentiment?
```

---

### Commodity Impact

Questions:

```text
Could commodity price movements
affect margins or demand?
```

---

# CIO Decision Framework Update

Add:

```yaml
macro_alignment_score:

geopolitical_risk_score:

external_risk_adjustment:
```

Final recommendation must consider:

```text
Fundamentals

+
Valuation

+
Macro Environment

+
Geopolitical Environment

+
Risk Analysis
```

---

# New Report Sections

Every report must now include:

## Global Macro Environment

### Interest Rates

### Inflation

### Economic Growth

### Market Liquidity

---

## Geopolitical Assessment

### Current Conflicts

### Regulatory Risks

### Trade Risks

### Supply Chain Risks

---

## External Risk Matrix

Example:

```yaml
external_risks:

  geopolitical:
    medium

  commodity:
    high

  currency:
    low

  regulatory:
    medium
```

---

# Implementation Reconciliation (v1.1 → actual AICAS design)

This PRD was adapted to fit the existing committee system rather than introduce a
new standalone service. Conflicts resolved as follows:

- **Realized as the 9th committee specialist** (`key: "macro"`,
  "Geopolitical & Macro Intelligence") in `cio/committee/roles.py::SPECIALISTS`,
  inserted after Market. It auto-joins the existing Round-1 → bounded-debate →
  moderator → CIO pipeline — no separate orchestration. It is NOT a new top-level
  agent/service.
- **Outputs follow the yaml-contract pattern** every specialist uses. Emitted
  fields: `macro_environment` (supportive|neutral|restrictive), `geopolitical_risk`,
  `commodity_risk`, `currency_risk`, `regulatory_risk` (each low|medium|high),
  `major_events`, `affected_sectors_positive`, `affected_sectors_negative`, plus the
  shared `vote`/`confidence`/`reason`/`memory_note`.
- **Required debate topics** (macro / geopolitical / commodity impact) are injected
  via the specialist's system prompt (the three questions it must answer) and the
  Moderator now explicitly weighs that external-risk debate. No new debate stage —
  fits the bounded bear-vs-bull + risk-vs-valuation design.
- **CIO Decision Framework** gained `macro_alignment_score` (0-100),
  `geopolitical_risk_score` (0-100), and `external_risk_adjustment` in the CIO yaml
  contract; the final call integrates fundamentals + valuation + macro + geopolitical
  + risk.
- **Report** gains a "Global Macro & Geopolitical Environment" section + the
  External Risk Matrix table, and the CIO external-risk fields render in Final
  Recommendation (`cio/committee/report.py`).
- **Backend / cost:** macro runs on NIM like the other specialists
  (`config/committee_models.yaml`). Cost impact is +2 LLM calls/run (Round-1 +
  Round-3 revision), keeping a review within the ~20-call ceiling.

---


