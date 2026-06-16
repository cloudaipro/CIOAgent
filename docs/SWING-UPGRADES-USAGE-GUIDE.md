# Swing Upgrades — Operator Usage Guide (Telegram & Dashboard)

How the pass-1→5 swing work is used from the two operator surfaces: the Telegram bot
(`cio/bot.py`) and the local dashboard (`cio/dashboard/`). It states what is **live now**,
what **data prerequisites** apply, and how to read the coverage columns (Anlst / CovEdge /
CovFlag) that drive the Alpha Hunter ranking.

---

## 1. Feature → surface map

| Feature (pass) | Telegram | Dashboard | State |
|----------------|----------|-----------|-------|
| Coverage density — analyst + institutional (1, 3, 4) | `/alpha` ranking | `/alpha` ranking + table columns | Live; itemized in the `/alpha` table (Anlst / CovEdge / CovFlag) since pass 5 |
| Four-layer gate (2) | `/committee` PDF | `/committee/<run_id>` | Live |
| Expectancy KPI (3) | — | `/expectancy` tab | Live; populate the ledger via the Sync button below |
| Trade ledger / IBKR sync (2, 3) | — | `/portfolio` "Sync trade ledger" button | Trigger wired (pass 5); needs `CIO_IBKR_TWS` |
| Hold management 肥/勤 (3, 5) | `/briefing` (Hold posture line) | — | Surfaced in the monitor briefing since pass 5 |
| Rule 6 / OOS check (4) | — | — | Analytic helper (`expectancy.oos_check`); documented only |

---

## 2. Telegram usage

Commands registered in `cio/bot.py`: `/watchlist`, `/alpha`, `/committee`, `/briefing`,
`/playbooks`, plus `/start`, `/help`, `/subscribe`, `/unsubscribe`, `/stop`.

### `/alpha`
Runs the Alpha Hunter funnel (`cio.alpha.engine.run`) and publishes the
`Alpha-yyyy-mm-dd` watchlist. The **coverage-density** work is live here: under-covered
names that also carry a real catalyst rank higher, because `coverage.apply` amplifies the
earnings/catalyst term inside `final_score`. Analyst count is live on the free Finnhub
tier; institutional ownership only contributes on a premium key (otherwise None, no
effect — see Section 5).

### `/committee SYMBOL [zh]`
Runs the committee and returns the report as a PDF (`zh` for Traditional Chinese). The
**four-layer gate** is live here: the report and dossier carry the `four_layer_gate`
block — per-layer catalyst / behavior / momentum / execution scores plus `blocked_by`. It
makes the ROKU failure visible: a green execution layer can no longer hide a dead
catalyst. The gate is advisory; it annotates, it does not block the CIO vote.
(PDF rendering needs `markdown` + `weasyprint` installed.)

### `/watchlist`
Latest prices for the active watchlist — typically the list `/alpha` just published.

### `/briefing [SYMBOL…] [zh]`
Runs the Watchlist Monitoring Agent and returns the pre-market briefing (PDF). Since
pass 5 each per-security block carries a **Hold posture** line — the 肥/勤 hold decision:
`HOLD / TRIM / EXIT (style, stop mode) — reason`. EXIT fires when the catalyst layer
breaks (thesis gone) even if the chart still looks green; TRIM on euphoric behavior in an
uptrend or a defensive (RED) regime; HOLD while the layers stay intact. The posture is
omitted for a name with no hold decision (degrades cleanly).

---

## 3. Dashboard usage

Routes are in `cio/dashboard/server.py`; the nav bar is `views._NAV`
(Committee, Watchlist, **Alpha Hunter**, **Expectancy**, Portfolio, …).

### `/alpha`
`render_alpha(latest_run, list_runs(10), …)` shows the ranked candidates with a
run/refresh control. Since pass 5 the candidate table itemizes the coverage signal in
three columns — **Anlst** (analyst count), **CovEdge** (0–100 coverage-density edge;
higher = more neglected), **CovFlag** (`under_covered` / `saturated` /
`value_trap_floor` / `institutionally_*`) — so you can see *why* a name ranked where it
did. Legacy rows from before the upgrade show `—` in those columns.

### `/expectancy` (added in pass 2)
Fully wired: pulls `trades.list_closed()`, derives `avg_hold_days` from the entry/exit
dates, calls `expectancy.summary(...)`, and renders the headline **expectancy** (with the
annualized figure), profit factor, SQN, and payoff ratio — with **win rate deliberately
demoted to a sub-stat**. An empty ledger renders "no closed trades yet" rather than
crashing. This is the screen that answers "is the edge real?", and it stays blank until
the ledger is fed (Gap 2).

### `/committee/<run_id>`
`render_committee_run` shows the saved transcript; the four-layer gate block is included
through `cio/committee/report.py`.

### `/portfolio`
The IBKR positions snapshot (read-only). Since pass 5 it also carries a **"Sync trade
ledger"** button: it calls `ibkr.sync_trades()` — seed open positions from broker average
cost, then log fills (orphan-safe) — and flashes "N fill(s) logged, M seeded, K skipped".
This is the trigger that populates the ledger behind `/expectancy`. With `CIO_IBKR_TWS`
unset it flashes "IBKR disabled" and does nothing. It is distinct from the existing
"Sync from IBKR" drift/marks button.

---

## 4. Operator daily workflow (today)

```mermaid
flowchart TD
    A["Telegram /alpha"] --> B["Alpha watchlist<br/>coverage-amplified ranking"]
    B --> C["Telegram /committee TICKER"]
    C --> D{"four-layer gate<br/>catalyst green?"}
    D -->|"blocked, catalyst red"| E["skip<br/>execution-only is the ROKU trap"]
    D -->|"pass"| F["enter position"]
    F --> G["log trade<br/>trades.open_trade"]
    G --> H["on exit, close_trade"]
    H --> I["Dashboard /expectancy<br/>expectancy, not win rate"]
    I --> J{"positive and beats<br/>buy-and-hold?"}
    J -->|"no"| K["stop or index instead"]
    J -->|"yes"| A
```

---

## 5. Data prerequisites

- **Finnhub key (`FINNHUB_API_KEY`).** Free tier powers analyst counts and recommendation
  trends. **Institutional ownership %** uses `/stock/ownership`, a premium endpoint, and is
  therefore **off by default**: set `CIO_FINNHUB_INSTITUTIONAL=1` to enable it once your
  key has institutional-data access. Left off (the default), `institutional_ownership_pct`
  returns None *without making any call* — so the free tier is never slowed by, nor spams
  the log with, the guaranteed 403 on that endpoint; the coverage blend simply falls back
  to the analyst signal. Enabling it on a non-premium key will 403 and still degrade to
  None safely.
- **IBKR (`CIO_IBKR_TWS`).** Read-only TWS/Gateway socket. Unset = disabled: every IBKR
  function returns its empty value with no network call.
- **PDF (`/committee` PDF, dashboard committee export).** Needs `markdown` + `weasyprint`.

---

## 6. Surfacing — closed in pass 5

All three display/trigger gaps from the first cut are now wired end-to-end and tested.
Each shipped through the Arch → Bob → Richard loop (Richard's verdict: no issues). Kept
here for history.

### Gap 1 — coverage itemized in `/alpha` ✓
`render_alpha` now renders three columns — **Anlst / CovEdge / CovFlag** — from the
persisted `alpha_candidates` row, so the operator can see *why* a name ranked where it
did. Legacy rows show `—`. (Closed pass 5.)

### Gap 2 — trade-ledger sync trigger added ✓
`/portfolio` now has a **"Sync trade ledger"** button → `ibkr.sync_trades()` (seed
positions from broker average cost → log fills, orphan-safe, read-only), flashing the
counts. `/expectancy` populates from it. No-op flash when `CIO_IBKR_TWS` is unset.
(Closed pass 5.) A cron/CLI trigger remains available as an alternative.

### Gap 3 — hold decision surfaced ✓
The monitor briefing's per-security block (`_security_block`) now renders a **Hold
posture** line from `assessment["hold_decision"]` — action + style + stop + reason — so
Telegram `/briefing` shows the 肥/勤 call. (Closed pass 5.) A dedicated `/monitor`
dashboard tab remains a future option but is not required.

```mermaid
flowchart LR
    subgraph compute["compute layer"]
        FUN["Alpha funnel + coverage"]
        COM["committee + four-layer gate"]
        LED["trade ledger + expectancy"]
        MON["monitor + hold 肥/勤"]
    end
    subgraph tg["Telegram"]
        TA["/alpha"]
        TC["/committee"]
        TB["/briefing"]
    end
    subgraph dash["Dashboard"]
        DA["/alpha + coverage cols"]
        DC["committee run detail"]
        DE["/expectancy"]
        DP["/portfolio + sync button"]
    end
    FUN --> TA
    FUN --> DA
    COM --> TC
    COM --> DC
    LED --> DE
    LED -->|"Sync trade ledger button"| DP
    MON -->|"Hold posture line"| TB
```

---

## 7. Reading Anlst / CovEdge / CovFlag in the `/alpha` candidate table

These three columns appear in the **Selected candidates** section of the Dashboard
`/alpha` page (and drive the ranking for Telegram `/alpha`). Every cell has a hover
tooltip; a collapsible **Column guide** accordion below the table repeats this
reference inline.

### Anlst — Analyst Count

Total analysts currently covering the stock (sum of strong\_buy + buy + hold + sell +
strong\_sell from Finnhub). Requires `FINNHUB_API_KEY`; shows `—` without one.

**Not absolute** — what matters is whether the count is high or low *for the stock's
market cap*. A $300 M name with 3 analysts is neglected; a $3 B name with 3 analysts
is severely neglected. The model adjusts via `expected_coverage()`:

| Market cap | Expected analysts |
|------------|------------------|
| ~$300 M | ~3 |
| ~$3 B | ~11 |
| ~$30 B | ~19 |
| ~$300 B | ~27 |
| ~$3 T | ~35 |

---

### CovEdge — Coverage-Density Edge (0–100)

Neglect score: how far the actual analyst count falls below the size-expected count,
converted to a 0–100 scale. Higher = more under-covered for the stock's market cap =
wider repricing window when a catalyst lands (Hong, Lim & Stein 2000).

**Formula:** `50 − 2.5 × (actual_analysts − expected_analysts)`

When `CIO_FINNHUB_INSTITUTIONAL=1`, institutional ownership % is blended in:
`edge = avg(analyst_edge, 100 − institutional_pct)`.

| CovEdge | Interpretation | Earnings score multiplier |
|---------|---------------|--------------------------|
| 70–100 | Significantly under-covered → strong edge window | ×1.12 → ×1.30 |
| 55–69 | Slightly under-covered | ×1.03 → ×1.12 |
| 45–54 | Neutral (on expectation) | ~×1.00 |
| 30–44 | Slightly over-covered | ×0.88 → ×0.97 |
| 0–29 | Saturated or value-trap | ×0.70 → ×0.88 |

**Effect on ranking:** `final_score` amplifies the earnings/catalyst term by
`1 + 0.30 × (CovEdge − 50) / 50`. Coverage **cannot manufacture a thesis** —
if Earn = 0 the multiplier has no effect. Hover any CovEdge cell to see the
exact per-row multiplier.

---

### CovFlag — Coverage Situation Label

Qualitative diagnosis of the coverage picture. Hover a badge for the full
explanation; summary:

| Flag | Colour | Meaning | Ranking effect |
|------|--------|---------|----------------|
| `under-covered` | 🟢 green | Fewer analysts than size expects | Earnings amplified |
| `saturated` | 🟡 amber | More analysts than expected | Earnings dampened |
| `value-trap` | 🔴 red | 0 analysts + market cap < $1 B — un-investable neglect | Amplification zeroed |
| `inst-neglected` | 🟢 green | Institutional ownership < 30 % — reinforces analyst neglect | Blended into CovEdge |
| `inst-crowded` | 🟡 amber | Institutional ownership > 70 % — crowded from both angles | Blended into CovEdge |
| `count-only` | neutral | No market-cap data; raw count used (conservative estimate) | Moderate edge at best |
| `—` | — | Legacy row (pre-upgrade) or no Finnhub key | No coverage effect |

---

### Reading a row — worked examples

```
NVDA   Anlst=52   CovEdge=18   CovFlag=saturated
```
52 analysts on a mega-cap: well over expected. Catalyst already priced in fast.
Earnings score **dampened ×0.82**. Ranks below a same-fundamentals neglected peer.

```
SMCI   Anlst=6    CovEdge=81   CovFlag=under-covered
```
Only 6 analysts on a mid-cap ($5–10 B). Catalyst news diffuses slowly.
Earnings score **amplified ×1.19**. Ranks above a crowded peer with identical fundamentals.

```
XYZ    Anlst=0    CovEdge=0    CovFlag=value-trap
```
Zero coverage, micro-cap < $1 B. Not neglect — un-investable. Amplification forced
to 0; name will not surface near the top regardless of other scores.

---

## 8. Command and route reference (quick ref)

| Surface | Entry point | Backed by |
|---------|-------------|-----------|
| Telegram | `/alpha` | `cio.alpha.engine.run` + `coverage` |
| Telegram | `/committee SYMBOL [zh]` | committee + `tirf.gate` (four-layer) |
| Telegram | `/watchlist` | active watchlist prices |
| Telegram | `/briefing [SYMBOL…] [zh]` | monitor + Hold posture (`hold.hold_decision`) |
| Dashboard | `/alpha` | `views.render_alpha` (+ coverage columns) |
| Dashboard | `/expectancy` | `trades.list_closed` + `expectancy.summary` |
| Dashboard | `/committee/<run_id>` | `views.render_committee_run` + `report` |
| Dashboard | `/portfolio` | `ibkr` positions + `sync_trades` button |

---

## 9. Status — pass 5 complete

All three gaps in Section 6 are closed: coverage columns in `/alpha`, the
"Sync trade ledger" trigger on `/portfolio`, and the Hold-posture line in the
`/briefing` monitor report. Each shipped through the Arch → Bob → Richard loop with a
clean review. The full pass-1→5 stack is now visible from both surfaces. A dedicated
`/monitor` dashboard tab is the only remaining optional enhancement (not required — the
hold posture already rides in `/briefing`).
