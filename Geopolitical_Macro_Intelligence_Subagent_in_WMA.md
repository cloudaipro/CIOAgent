# PRD Update

# Watchlist Monitoring Agent

## Version 1.1

---

# New Core Responsibility

## Responsibility 8

### Global Macro & Geopolitical Monitoring

The agent must monitor global events that could impact watchlist securities.

---

### Geopolitical Events

Monitor:

* Armed conflicts
* Military escalation
* Trade disputes
* Sanctions
* Export restrictions
* Elections affecting policy

---

### Commodity Events

Monitor:

* Oil spikes
* Natural gas spikes
* Metal shortages
* Agricultural disruptions

---

### Currency Events

Monitor:

* USD strength
* USD weakness
* CNY volatility
* JPY movements

---

### Global Economic Events

Monitor:

* CPI releases
* GDP releases
* Central bank announcements
* Treasury yield movements

---

# New Morning Briefing Section

## Global Market Intelligence

Must appear before stock analysis.

Example:

```yaml
global_summary:

  market_sentiment:
    cautious

  geopolitical_risk:
    elevated

  key_events:

    - Israel-Iran conflict escalation
    - Brent crude above $90
    - US 10Y yield rising
```

---

# New Morning Briefing Section

## Watchlist Exposure Analysis

Example:

```yaml
watchlist_exposure:

  NVDA:
    geopolitical_impact:
      medium

  MU:
    geopolitical_impact:
      high

  XOM:
    geopolitical_impact:
      positive
```

---

# New Alert Type

## Macro Alert

Example:

```yaml
alert_type:
  macro

severity:
  high

reason:
  Oil prices rose 8%
```

---

## Geopolitical Alert

Example:

```yaml
alert_type:
  geopolitical

severity:
  critical

reason:
  New semiconductor export restrictions announced
```

---

# Security Assessment Update

Add:

```yaml
external_risk_score:

macro_sensitivity:

geopolitical_sensitivity:

commodity_sensitivity:

currency_sensitivity:
```

---

# Updated Daily Workflow

```text
Load Watchlist

      ↓

Collect Company Data

      ↓

Collect Industry Data

      ↓

Collect Macro Data

      ↓

Collect Geopolitical Data

      ↓

Determine Portfolio Impact

      ↓

Generate Alerts

      ↓

Generate Morning Briefing
```

---

# Recommended Final Architecture

```text
Watchlist Monitoring Agent
        │
        ├── News Intelligence Module
        ├── Event Detection Module
        ├── Analyst Sentiment Module
        ├── Industry Impact Module
        ├── Macro Intelligence Module
        ├── Geopolitical Intelligence Module
        ├── Risk Detection Module
        └── Catalyst Detection Module

                │

                ▼

      Morning Briefing Engine

                │

                ▼

      Priority Ranking Engine

                │

                ▼

      Investment Committee Trigger
```

---

# Implementation Reconciliation (v1.1 → actual WMA design)

The WMA's first layer is deliberately cheap (one model call per security), so the
"Macro / Geopolitical Intelligence Module" was realized WITHOUT a separate per-name
sub-agent. Conflicts resolved as follows:

- **Per-security external-risk read = zero extra calls.** The existing single
  `monitor_symbol` call now also emits `external_risk_score` (0-100) and
  `macro_sensitivity` / `geopolitical_sensitivity` / `commodity_sensitivity` /
  `currency_sensitivity` (each low|medium|high). Added to `WMA_SYSTEM` + parsed in
  `agent.py`.
- **Global Market Intelligence = ONE shared call per run.** New
  `global_macro_snapshot()` (`agent.py`, prompt `MACRO_SNAPSHOT_SYSTEM`,
  `role_key="macro"`) does a single macro/geopolitical headline read for the whole
  briefing — not per security — emitting `market_sentiment` (risk-on|cautious|
  risk-off), `geopolitical_risk`, `commodity_risk`, `key_events`, `summary`. Renders
  as the "Global Market Intelligence" section ABOVE the per-stock review.
- **Watchlist Exposure Analysis** is derived from the per-security sensitivity
  fields (sorted table) — no extra call.
- **Macro / Geopolitical alerts** are derived from the snapshot risk levels + any
  high per-security sensitivity — no extra call.
- **Daily workflow:** the macro snapshot runs once alongside the per-security scan
  (not as separate sequential collection stages), then feeds the briefing.
- **Backward compatible:** `monitor_watchlist` still returns `list[dict]`;
  `build_briefing` / `briefing_summary` gained an optional `macro=` argument. Callers
  (`bot.py`, `scheduler.py`, `__main__.py`) fetch the snapshot and pass it; a snapshot
  failure degrades to a briefing without the global section.