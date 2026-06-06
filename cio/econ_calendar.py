"""High-impact economic-event calendar + alerting store.

The transcript with the operator showed the real pain: a hot NFP print blindsided
the portfolio. This module lets the bot warn ahead of the events that historically
move markets (NFP, CPI, FOMC, PCE, …).

Design mirrors the rest of the codebase:
  * Dates are DATA, not memory — they live in their own table, never in the
    figures-firewalled note store.
  * NFP is purely rule-based (first Friday of the month, 08:30 ET) so it is seeded
    deterministically with zero lookups and never goes stale.
  * Everything else (CPI/PPI/PCE/FOMC/GDP/Retail) has no clean monthly rule, so the
    agent populates real release dates via web lookup (the monthly_red_events
    playbook → add_event). We deliberately do NOT hardcode those — a wrong date is
    worse than no date.

The scheduler (cio.scheduler.econ_event_alert) reads upcoming() once a day and
sends one heads-up per event ahead of time.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import db

# Canonical name for the deterministic monthly jobs report so seeding is idempotent.
NFP_NAME = "Nonfarm Payrolls (NFP)"
NFP_TIME = "08:30 ET"


# ----- deterministic NFP -----------------------------------------------------

def nfp_date(year: int, month: int) -> date:
    """First Friday of the month — the BLS Employment Situation (NFP) release."""
    # weekday(): Mon=0 … Fri=4. Find the first Friday on/after the 1st.
    first = date(year, month, 1)
    offset = (calendar.FRIDAY - first.weekday()) % 7
    return first + timedelta(days=offset)


def _month_iter(start: date, months: int):
    """Yield (year, month) for *months* months starting at *start*'s month."""
    y, m = start.year, start.month
    for _ in range(months):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def seed_nfp(months_ahead: int = 2, today: date | None = None,
             db_path=db.DB_PATH) -> int:
    """Ensure an NFP row exists for the current month and the next *months_ahead*.

    Idempotent: UNIQUE(event_date, name) means re-seeding is a no-op. Returns the
    number of new rows inserted."""
    today = today or date.today()
    inserted = 0
    for y, m in _month_iter(today, months_ahead + 1):
        d = nfp_date(y, m).isoformat()
        inserted += add_event(d, NFP_NAME, impact="high", time_et=NFP_TIME,
                              source="rule:first-friday", db_path=db_path)
    return inserted


# ----- store -----------------------------------------------------------------

def add_event(event_date: str, name: str, impact: str = "high",
              time_et: str = "", source: str = "", db_path=db.DB_PATH) -> int:
    """Insert a high-impact event. UPSERT on (event_date, name): re-adding the same
    event updates its metadata without resetting the *alerted* flag. Returns 1 if a
    new row was created, 0 if it already existed."""
    event_date = (event_date or "").strip()
    name = (name or "").strip()
    if not event_date or not name:
        raise ValueError("event_date and name are required")
    # Validate the date shape early so a bad string never poisons the table.
    date.fromisoformat(event_date)
    conn = db.connect(db_path)
    with conn:
        existed = conn.execute(
            "SELECT 1 FROM econ_events WHERE event_date=? AND name=?",
            (event_date, name)).fetchone() is not None
        conn.execute(
            "INSERT INTO econ_events (event_date,name,impact,time_et,source) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(event_date,name) DO UPDATE SET "
            "  impact=excluded.impact, time_et=excluded.time_et, source=excluded.source",
            (event_date, name, impact.strip() or "high", time_et.strip(), source.strip()),
        )
    conn.close()
    return 0 if existed else 1


def upcoming(lead_days: int = 1, today: date | None = None,
             db_path=db.DB_PATH) -> list[dict]:
    """Un-alerted events from today through today+lead_days (inclusive)."""
    today = today or date.today()
    hi = (today + timedelta(days=lead_days)).isoformat()
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, event_date, name, impact, time_et, source FROM econ_events "
        "WHERE alerted=0 AND event_date>=? AND event_date<=? "
        "ORDER BY event_date, time_et",
        (today.isoformat(), hi),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_upcoming(days: int = 45, today: date | None = None,
                  db_path=db.DB_PATH) -> list[dict]:
    """All events from today through today+days, for display (dashboard / tool)."""
    today = today or date.today()
    hi = (today + timedelta(days=days)).isoformat()
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, event_date, name, impact, time_et, source, alerted FROM econ_events "
        "WHERE event_date>=? AND event_date<=? ORDER BY event_date, time_et",
        (today.isoformat(), hi),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all(db_path=db.DB_PATH) -> list[dict]:
    """Every stored event, newest date first — for the dashboard management view."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, event_date, name, impact, time_et, source, alerted, created_at "
        "FROM econ_events ORDER BY event_date DESC, time_et"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alerted(ids, db_path=db.DB_PATH) -> int:
    """Flag events as alerted so the daily job never double-sends. Returns count."""
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute(
            f"UPDATE econ_events SET alerted=1 WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
    n = cur.rowcount
    conn.close()
    return n


def delete_event(eid: int, db_path=db.DB_PATH) -> int:
    """Delete one event by id. Returns rows removed."""
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute("DELETE FROM econ_events WHERE id=?", (int(eid),))
    n = cur.rowcount
    conn.close()
    return n


# ----- report rendering ------------------------------------------------------

_IMPACT_DOT = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def render_report_md(events: list[dict], today: date | None = None) -> str:
    """Deterministic Markdown report (with a real table) for a set of events.

    Data-driven — the caller passes whatever `list_upcoming()` returned. Used to
    render the PDF the bot sends alongside the agent's text reply. Distinct event
    sources are listed once at the foot."""
    today = today or date.today()
    title_month = today.strftime("%B %Y")
    lines = [
        f"# 📅 Economic Red-Events — {title_month}",
        "",
        "_High-impact US economic releases that historically move markets. The bot "
        "sends an automatic heads-up the day before each one (all times ET)._",
        "",
    ]
    if not events:
        lines.append("_No upcoming events recorded._")
        return "\n".join(lines)

    lines += [
        "| Date | Day | Time (ET) | Event | Impact |",
        "|---|---|---|---|---|",
    ]
    sources: list[str] = []
    for e in events:
        d = e["event_date"]
        try:
            dow = date.fromisoformat(d).strftime("%a")
        except ValueError:
            dow = ""
        impact = (e.get("impact") or "high").lower()
        dot = _IMPACT_DOT.get(impact, "")
        lines.append(
            f"| {d} | {dow} | {e.get('time_et') or ''} | {e.get('name','')} | "
            f"{dot} {impact.upper()} |")
        src = (e.get("source") or "").strip()
        if src.startswith("http") and src not in sources:
            sources.append(src)

    lines += [
        "",
        "_The bot auto-alerts the day before each event. Manage these on the "
        "dashboard's **Econ events** tab._",
    ]
    if sources:
        lines += ["", "**Sources:**"] + [f"- {s}" for s in sources]
    return "\n".join(lines)
