# Technical Report — Playbooks Tab, Economic-Event Calendar & Auto-Alerts

**Date:** 2026-06-06
**Scope:** `cio/econ_calendar.py` (new), `cio/scheduler.py`, `cio/agent.py`,
`cio/db.py`, `cio/memory.py`, `cio/dashboard/{server,views}.py`
**Origin:** Telegram chat 8535885767. A hot Nonfarm-Payrolls print (172k vs 80k
consensus) drove a sharp semiconductor sell-off that blindsided the portfolio. The
operator asked for (a) a reusable procedure to enumerate each month's high-impact
events and (b) an automatic heads-up *before* those events land.

---

## 1. Summary

Three shipments, built on existing infrastructure:

1. **Playbooks dashboard tab** (`/playbooks`) — view/delete the agent's saved
   reusable procedures, which previously had no UI.
2. **`monthly_red_events` playbook** — a saved procedure the agent replays at the
   start of each month to populate that month's high-impact economic events.
3. **Economic-event calendar + auto-alert** — a new event store, a deterministic
   NFP seeder, two agent tools to populate it, an APScheduler job that warns
   subscribed chats ahead of each event, and a `/econ` dashboard tab.

No new third-party dependency. No paid economic-calendar API. Dates that follow a
rule (NFP) are computed; everything else is fetched by the agent from authoritative
sources and stored.

---

## 2. What shipped

### 2.1 Data model — `econ_events` table (`cio/db.py`)

Added to the shared `SCHEMA` string, so it auto-creates on the next `db.connect()`
(every connection runs `executescript(SCHEMA)` with `CREATE TABLE IF NOT EXISTS` —
no migration script needed).

| Column | Purpose |
|---|---|
| `event_date` | ISO `YYYY-MM-DD` |
| `name` | e.g. `Nonfarm Payrolls (NFP)`, `CPI`, `FOMC decision` |
| `impact` | `high` \| `medium` \| `low` (default `high`) |
| `time_et` | e.g. `08:30 ET` (display only) |
| `source` | provenance URL or `rule:first-friday` |
| `alerted` | `0/1` — set once a heads-up was sent (dedupe) |
| `created_at` | insert timestamp |

`UNIQUE (event_date, name)` makes re-adding an event an idempotent UPSERT that
refreshes metadata **without** resetting `alerted`.

### 2.2 `cio/econ_calendar.py` (new module)

| Function | Behaviour |
|---|---|
| `nfp_date(year, month)` | First Friday of the month (the BLS Employment Situation release). Pure arithmetic. |
| `seed_nfp(months_ahead=2)` | Idempotently ensures NFP rows exist for the current + next N months. Zero lookups. |
| `add_event(date, name, impact, time_et, source)` | UPSERT on `(event_date, name)`; validates the date string; returns `1` if newly created, `0` if it already existed. |
| `upcoming(lead_days=1)` | Un-alerted events in `[today, today+lead_days]` — the alert query. |
| `list_upcoming(days=45)` | All events in a forward window — for the tool / dashboard. |
| `list_all()` | Every row, newest first — dashboard management view. |
| `mark_alerted(ids)` | Flip `alerted=1` so the daily job never double-sends. |
| `delete_event(id)` | Remove one row (dashboard delete). |

Mirrors `cio/memory.py` conventions: `db.connect(db_path)`, `with conn:` for writes,
`sqlite3.Row` dicts out.

### 2.3 Scheduler job — `econ_event_alert(bot)` (`cio/scheduler.py`)

- Seeds NFP, reads `upcoming(lead_days)`, broadcasts one plain-text heads-up per
  event to `memory.subscribed_chats()`, then `mark_alerted()`.
- **Never raises** into the scheduler (whole body wrapped; per-chat sends isolated
  so one bad chat can't block the rest) — same contract as `daily_digest` /
  `watchlist_briefing`.
- Registered in `start()` as a daily cron + a boot one-shot (~45s after start) so a
  restart re-checks the window and seeds NFP.
- **Zero LLM tokens** — deterministic text from the table, same philosophy as the
  daily digest.

Knobs (env): `CIO_ECON_ALERT_HOUR` (default `7`, `off` disables),
`CIO_ECON_ALERT_MINUTE` (default `0`), `CIO_ECON_ALERT_LEAD_DAYS` (default `1`).

### 2.4 Agent tools (`cio/agent.py`)

- `add_econ_event(event_date, name, impact, time_et, source)` — records a verified
  event. The description instructs the model to use **real, looked-up** dates.
- `list_econ_events()` — seeds NFP, lists the next ~45 days (incl. `alerted` flag).

Registered in `CIO_TOOLS` (now 27 tools). These are what the playbook calls.

### 2.5 `monthly_red_events` playbook (`memory.add_playbook`, global scope)

Five steps: check what's scheduled → web-search and **verify** the month's
CPI/Core CPI/PPI/PCE/FOMC/GDP/Retail Sales/JOLTS dates against BLS/BEA/Fed or a
reputable calendar → `add_econ_event` each → confirm → summarize and propose
trimming risk 1–2 days prior. Steps reference **tools, not numbers** (figures
firewall), so the playbook never goes stale.

### 2.6 Dashboard tabs (`cio/dashboard/`)

- **`/playbooks`** — Name · Scope · Hits · Steps · Created · Delete.
- **`/econ`** — Date · Event · Impact · Time · Source · Alerted · Delete; seeds NFP
  on GET so the table is never empty.

Both follow the established read-with-per-row-delete pattern: shared `_wipe_form`
with a JS-confirm danger button, POST→303 redirect (PRG), same auth gate, and a
render that **never 500s** the operator (caught and shown as a flash).

---

## 3. How it was verified

All checks run against the live code with `.venv/bin/python`.

### 3.1 Calendar logic (unit-level)

```
NFP rule:        nfp_date(2026,7) == 2026-07-03 (Fri)   ✅  (matches the date the
                 nfp_date(2026,6) == 2026-06-05 (Fri)        bot quoted in the transcript)
seed_nfp:        first call inserts 3 rows, second call inserts 0  ✅ (idempotent)
add_event:       new insert → 1, duplicate → 0          ✅ (UPSERT, no dupes)
upcoming:        CPI on 2026-06-10 visible from 2026-06-09 lead=1 ✅
mark_alerted:    after marking, CPI drops out of upcoming()        ✅ (no re-send)
delete_event:    test rows removed                       ✅
```

### 3.2 Alert formatting

`_format_econ_alert()` produced:

```
🗓️ *High-impact economic events tomorrow*

• 2026-06-10 (Wed) 08:30 ET — CPI [HIGH]

These releases historically move markets (esp. vs. consensus). Consider trimming
risk 1–2 days prior, per your monthly_red_events playbook.
```

### 3.3 Playbook save

`memory.add_playbook("monthly_red_events", …)` saved (id 2) and read back with 5
steps. The figures firewall initially rejected two innocent substrings — `"a`**`gain`**`st"`
matched the `gain` keyword and `"o`**`pe`**`rator"` matched `p/?e` — confirming the
deterministic guard is active; the steps were reworded ("using", "owner") until the
guard passed legitimately. No figures are stored.

### 3.4 Integration / imports

```
import cio.agent → CIO_TOOLS contains add_econ_event, list_econ_events   ✅ (27 tools)
import cio.scheduler → econ_event_alert present                          ✅
py_compile on all 7 changed files                                       ✅
```

### 3.5 Dashboard (live HTTP, throwaway ports)

```
GET /econ        → 200; renders "Economic events", NFP row, Delete buttons   ✅
GET /playbooks   → 200; renders monthly_red_events                           ✅
nav              → /econ and /playbooks links present cross-page             ✅
POST delete bad id (econ & playbooks) → 303 PRG, error flash, no 500,
                 real rows untouched                                        ✅
```

---

## 4. Design notes

**Why a dedicated table, not memory.** Memory is figure-firewalled and meant for
qualitative context; event *dates* are structured data with a dedupe/alert lifecycle.
A table gives a clean UNIQUE key, an `alerted` flag, and SQL windows — and keeps
dates out of the note store entirely.

**Why NFP is computed but the rest is fetched.** NFP has a clean rule (first Friday,
08:30 ET), so it is seeded with zero lookups and can never drift. CPI/PPI/PCE/FOMC/
GDP/Retail have **no** clean monthly rule — their release dates are announced by
BLS/BEA/the Fed and vary. Hardcoding them risks a wrong date, and **a wrong date is
worse than no date** (false confidence before a market-moving print). So the agent
fetches and verifies them via the playbook and stores the result. This matches the
codebase's core ethos: *recompute, never store stale.*

**Why deterministic, zero-token alerts.** Like the daily digest and pre-market
briefing, the alert is built directly from the table — no model call. A daily
broadcast must be cheap and reliable; the conversational agent is reserved for
interactive turns. Idempotency is carried by the `alerted` flag (survives restarts),
not by an in-memory guard.

**Why the alert is plain text (no Markdown parse_mode).** Event names can contain
Markdown-special characters (`_`, `&`, `*`); enabling `parse_mode="Markdown"` would
make Telegram reject any such message and the chat would silently miss its alert.
The rest of the bot's scheduled pushes send plain text for the same reason.

**Why one heads-up per event, not per day.** `lead_days` defines a window and the
`alerted` flag fires exactly once per event when it first enters that window. A
larger `CIO_ECON_ALERT_LEAD_DAYS` widens the warning lead without creating repeats.

**Why the playbook stores steps, not a calendar.** Playbooks describe *how* to
produce the answer; running one fetches fresh data each time. The playbook is the
durable "how", the `econ_events` table is the per-month "what", and the scheduler is
the "when". Clean separation: the operator never edits dates by hand, and the
procedure outlives any single month.

**Failure isolation.** Every scheduled path swallows its own exceptions and logs;
every dashboard render is wrapped so a bad row shows a flash, not a 500. A 24/7
operator tool must degrade, never crash.

---

## 5. Operating it

1. **Restart the bot** so the scheduler registers the new job at boot.
2. **`/subscribe`** in Telegram to receive alerts (same opt-in list as the digest
   and briefing).
3. **First run:** message the bot "run monthly_red_events" to populate the current
   month (NFP auto-fills regardless).
4. **Observe/manage:** dashboard `/econ` (events + alert status) and `/playbooks`.

Disable entirely with `CIO_ECON_ALERT_HOUR=off`. Widen the warning lead with
`CIO_ECON_ALERT_LEAD_DAYS=2`.
