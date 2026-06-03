# Product Requirements Document (PRD)

# Watchlist Monitoring Agent (WMA)

### Version 1.0

---

# 1. Executive Summary

## Product Name

**Watchlist Monitoring Agent (WMA)**

## Purpose

The Watchlist Monitoring Agent is a specialized AI agent responsible for monitoring a predefined watchlist of stocks and ETFs before each market open.

Its primary purpose is to act like a professional buy-side research analyst who prepares a daily morning briefing for a portfolio manager.

The agent continuously scans:

* Company news
* Industry news
* Macroeconomic developments
* Analyst upgrades/downgrades
* Earnings announcements
* Regulatory changes
* Market sentiment
* ETF-specific developments

and transforms this information into actionable investment intelligence.

---

# 2. Business Objective

Enable investors to answer the following questions before market open:

### What happened since yesterday?

### Is there any new risk?

### Is there any new opportunity?

### Did the investment thesis change?

### Should I buy, hold, reduce, or closely monitor this stock?

---

# 3. Product Scope

The agent only monitors securities explicitly included in the user's watchlist.

Supported assets:

* Individual stocks
* ETFs

Not supported:

* Options
* Futures
* Crypto
* Forex

(v1 scope)

---

# 4. Core Responsibilities

The Watchlist Monitoring Agent must perform the following tasks before every market open.

---

## Responsibility 1

### Overnight News Intelligence

Analyze all relevant developments occurring since the previous market close.

Sources:

* Company announcements
* SEC filings
* Press releases
* Earnings releases
* Industry publications
* Major financial news

Objective:

Determine whether new information materially changes the investment thesis.

---

## Responsibility 2

### Material Event Detection

Detect high-impact events.

Examples:

#### Earnings

* Earnings beat
* Earnings miss
* Guidance increase
* Guidance reduction

#### Corporate Actions

* Stock buybacks
* Secondary offerings
* Mergers
* Acquisitions

#### Management

* CEO changes
* Executive departures

#### Legal

* Lawsuits
* Regulatory investigations

#### Government

* Export restrictions
* Trade restrictions
* Regulatory approvals

---

## Responsibility 3

### Analyst Sentiment Monitoring

Track:

* Target price revisions
* Rating upgrades
* Rating downgrades
* Consensus estimate changes

Output:

```yaml
analyst_sentiment:
  bullish
```

or

```yaml
analyst_sentiment:
  bearish
```

---

## Responsibility 4

### Industry Impact Assessment

Evaluate whether recent industry developments affect the watchlist stock.

Examples:

For NVIDIA:

* AI demand
* Data center spending

For Micron:

* HBM demand
* DRAM pricing

For ETFs:

* Sector trends
* Capital inflows/outflows

---

## Responsibility 5

### Market Context Analysis

Evaluate:

* Interest rates
* Treasury yields
* CPI
* Federal Reserve commentary
* Major index performance

Determine whether macro conditions support or weaken the stock's outlook.

---

## Responsibility 6

### Catalyst Monitoring

Identify upcoming events likely to move stock price.

Examples:

```yaml
upcoming_catalysts:
  - earnings next week
  - product launch
  - investor day
```

---

## Responsibility 7

### Risk Monitoring

Identify newly emerging risks.

Examples:

```yaml
new_risks:
  - valuation expansion
  - regulatory pressure
  - weakening demand
```

---

# 5. Daily Workflow

## Step 1

Load Watchlist

Example:

```yaml
watchlist:
  - NVDA
  - MU
  - TSM
  - VOO
  - SCHD
```

---

## Step 2

Collect Latest Data

Gather:

* News
* Filings
* Analyst changes
* Earnings updates
* Industry reports
* Macro events

---

## Step 3

Evaluate Significance

Classify each event:

```yaml
importance:
  low
  medium
  high
  critical
```

---

## Step 4

Generate Security Assessment

For each security:

### Bullish Signals

### Bearish Signals

### Risks

### Catalysts

### Recommended Action

---

## Step 5

Generate Morning Briefing

Produce consolidated report.

---

# 6. Security Assessment Framework

Every stock or ETF must receive:

---

## Overall Status

```yaml
overall_status:
  bullish
  neutral
  bearish
```

---

## Conviction Score

```yaml
conviction_score:
  0-100
```

Interpretation:

| Score  | Meaning             |
| ------ | ------------------- |
| 90-100 | Strong Conviction   |
| 75-89  | High Conviction     |
| 60-74  | Moderate Conviction |
| 40-59  | Weak Conviction     |
| <40    | Low Conviction      |

---

## Event Importance Score

```yaml
event_importance:
  low
  medium
  high
  critical
```

---

## Recommendation

Allowed values:

```yaml
recommendation:
  Buy
  Add
  Hold
  Monitor
  Reduce
  Sell
```

---

# 7. Output Format

## Individual Security Report

```yaml
ticker:
  MU

company:
  Micron Technology

overall_status:
  bullish

conviction_score:
  84

recommendation:
  Add

key_positive_events:
  - HBM demand increased
  - Analyst target raised

key_negative_events:
  - Memory pricing concerns

new_risks:
  - China exposure

upcoming_catalysts:
  - earnings release

investment_thesis_change:
  unchanged
```

---

# 8. Daily Morning Briefing Format

## Section 1

### Executive Summary

Example:

```yaml
market_environment:
  constructive

watchlist_summary:
  bullish: 4
  neutral: 3
  bearish: 1

highest_priority:
  MU
```

---

## Section 2

### Highest Priority Alerts

Only include:

* High
* Critical

events

Example:

```yaml
critical_alerts:
  - MU earnings beat
  - NVDA guidance raised
```

---

## Section 3

### Watchlist Review

Provide summary for every security.

---

## Section 4

### New Risks

List newly discovered risks.

---

## Section 5

### Upcoming Catalysts

List upcoming earnings and events.

---

# 9. Alert System

The agent must generate alerts when:

### Critical Event Detected

Examples:

* Earnings miss
* Guidance cut
* Regulatory action

---

### Investment Thesis Changed

Examples:

```yaml
thesis_change:
  positive
```

or

```yaml
thesis_change:
  negative
```

---

### New Major Risk

Examples:

* Geopolitical escalation
* Export restrictions
* Debt concerns

---

# 10. Scheduling Requirements

Default schedule:

```yaml
execution_time:
  60 minutes before market open
```

US Market:

```yaml
08:30 ET
```

Configurable.

---

# 11. Integration with Main Investment System

The Watchlist Monitoring Agent serves as the first layer of the architecture.

```text
Watchlist Monitoring Agent
           │
           ▼

Event Prioritization

           │
           ▼

If Important Event Detected

           │
           ▼

Full Investment Committee Analysis

           │
           ▼

CIO Final Review
```

This prevents unnecessary deep analysis on every stock every day.

---

# 12. Success Metrics

### Relevance

Signal-to-noise ratio remains high.

### Accuracy

Events correctly classified.

### Timeliness

Reports completed before market open.

### Explainability

Every recommendation includes evidence.

### Consistency

Identical inputs produce similar outputs.

---

# 13. Future Enhancements

### v2

* Earnings call transcript analysis
* Insider buying/selling analysis
* Hedge fund ownership changes
* Institutional flow analysis

### v3

* Automated thesis tracking
* Historical recommendation scoring
* Recommendation performance dashboard
* Self-learning priority engine

---

# Final Mission Statement

The Watchlist Monitoring Agent acts as a professional buy-side morning analyst whose sole responsibility is to monitor a defined watchlist, identify meaningful developments, assess their impact, and deliver a concise, evidence-based pre-market briefing that helps investors focus on what matters most before the market opens.
