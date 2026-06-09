"""Durable state for the 24/7 runtime (MemCore store layer).

Backed by SQLite so everything survives restarts. Three concerns:

1. **Tiered notes** (`mem_notes`) — qualitative agent memory, namespaced by
   `scope` ('global' or 'chat:<id>'), split into HOT (injected at session start)
   and WARM (recalled on demand), scored by importance/hits, with provenance.
   The **figures firewall** forbids storing financial figures here — numbers
   stay in `transactions`/`prices` and are always recomputed. This module owns
   the write/read path; `recall.py` adds hybrid (FTS5+vector) search on top.

2. **User profile** (`user_profile`) — per-scope role/stack/prefs/goals, also
   injected at session start (Hermes USER.md equivalent).

3. **Chat registry / meta** (`chats`, `meta`) — digest subscriptions, per-chat
   SDK `session_id` for resume, and runtime bookkeeping.
"""
from __future__ import annotations

import math
import os
import re

from . import db
from . import recall as _recall   # aliased: this module also defines a recall() fn

# Eviction keeps memory bounded over months of 24/7 use. Hot/user notes are
# never evicted; warm agent/auto notes decay by recency×importance×hits.
MAX_NOTES_PER_SCOPE = int(os.getenv("CIO_MAX_NOTES", os.getenv("CFO_MAX_NOTES", "500")))
_HALFLIFE_DAYS = 30.0
# A warm note recalled this many times is auto-promoted to hot (injected at
# session start) — memory curates itself by usefulness (self-improving loop).
PROMOTE_HITS = int(os.getenv("CIO_PROMOTE_HITS", os.getenv("CFO_PROMOTE_HITS", "3")))


class FiguresFirewallError(ValueError):
    """Raised when a note tries to store a financial figure (must be recomputed)."""


# ----- figures firewall -----------------------------------------------------
# Numbers describing money/holdings must NOT be memorized — they go stale. They
# live in transactions/prices and are recomputed. Qualitative notes (prefs,
# watchlist, dates, plans) are fine.

_FIG_KEYWORDS = (
    r"worth|priced?|price|trading at|valued?|value|valuation|market value|cost basis|"
    r"balance|p&l|pnl|profit|loss|equity|return|gain|dividend|"
    # fundamentals/ratios — also numbers that go stale and must be recomputed
    r"\broe\b|\broa\b|\broic?\b|\broi\b|margins?|\beps\b|ebitda|revenue|"
    r"\byield\b|multiple|p/?e|\bcagr\b|\bfcf\b|payout"
)
_HAS_NUMBER = re.compile(r"\d")
_CURRENCY = re.compile(r"[$€£¥]\s*\d")
_NUM_NEAR_KW = re.compile(rf"(?:{_FIG_KEYWORDS}).*?\d|\d.*?(?:{_FIG_KEYWORDS})", re.I)


def _looks_like_figure(value: str) -> bool:
    # Keyword-gated only: a number is blocked when it sits near a figure keyword
    # ("27% margins", "141% ROE" → margins/roe keywords catch them). A bare number
    # or percentage with NO figure keyword ("trim 50% on breakout") passes — the
    # LLM sanitizer (committee.note_sanitizer) carries the semantic load now, so the
    # regex stays a precise, low-false-positive deterministic backstop.
    if _CURRENCY.search(value):
        return True
    if _HAS_NUMBER.search(value) and _NUM_NEAR_KW.search(value):
        return True
    return False


def _guard_figures(value: str) -> None:
    if _looks_like_figure(value):
        raise FiguresFirewallError(
            "Refusing to store a financial figure as memory — numbers go stale. "
            "Use set_price / the portfolio tools; they are always recomputed. "
            "Memory is for qualitative context only (preferences, watchlist, plans)."
        )


# ----- tiered notes ---------------------------------------------------------

def remember(value: str, key: str | None = None, scope: str = "global",
             tier: str = "warm", importance: float = 1.0, source: str = "agent",
             db_path=db.DB_PATH) -> int:
    """Store a qualitative note; returns its id. Upserts when `key` is given.

    Rejects financial figures (figures firewall). `tier` is 'hot' (injected at
    session start) or 'warm' (recall on demand)."""
    value = value.strip()
    _guard_figures(value)
    conn = db.connect(db_path)
    with conn:
        if key:
            conn.execute(
                "INSERT INTO mem_notes (scope,tier,key,value,importance,source) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, "
                "tier=excluded.tier, importance=excluded.importance, "
                "source=excluded.source, updated_at=datetime('now','localtime')",
                (scope, tier, key, value, importance, source),
            )
            nid = conn.execute("SELECT id FROM mem_notes WHERE scope=? AND key=?",
                               (scope, key)).fetchone()["id"]
        else:
            cur = conn.execute(
                "INSERT INTO mem_notes (scope,tier,key,value,importance,source) "
                "VALUES (?,?,?,?,?,?)",
                (scope, tier, key, value, importance, source),
            )
            nid = cur.lastrowid
    conn.close()
    _recall.index_note(nid, value, db_path)   # keep the semantic index in sync
    if count_notes(scope, db_path=db_path) > MAX_NOTES_PER_SCOPE:
        evict(scope, max_notes=MAX_NOTES_PER_SCOPE, db_path=db_path)
    return nid


def recall(key: str, scope: str = "global", db_path=db.DB_PATH) -> str | None:
    """Exact-key lookup within a scope; bumps the hit counter."""
    conn = db.connect(db_path)
    row = conn.execute("SELECT id, value FROM mem_notes WHERE scope=? AND key=?",
                       (scope, key)).fetchone()
    if row:
        with conn:
            conn.execute("UPDATE mem_notes SET hits=hits+1 WHERE id=?", (row["id"],))
    conn.close()
    return row["value"] if row else None


def get_note(note_id: int, db_path=db.DB_PATH) -> dict | None:
    conn = db.connect(db_path)
    row = conn.execute("SELECT * FROM mem_notes WHERE id=?", (note_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_notes(scope: str = "global", tier: str | None = None, limit: int = 50,
               db_path=db.DB_PATH) -> list[dict]:
    """Notes for a scope, most important/recent first."""
    conn = db.connect(db_path)
    sql = "SELECT * FROM mem_notes WHERE scope=?"
    args: list = [scope]
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_scopes(db_path=db.DB_PATH) -> list[dict]:
    """All scopes present in this db with their note count, busiest first.

    Used by the dev dashboard's memory tab to enumerate per-agent / per-chat
    memory namespaces. Returns [] if the table is empty or unreadable.
    """
    try:
        conn = db.connect(db_path)
        rows = conn.execute(
            "SELECT scope, COUNT(*) AS n FROM mem_notes GROUP BY scope "
            "ORDER BY n DESC, scope ASC"
        ).fetchall()
        conn.close()
        return [{"scope": r["scope"], "count": r["n"]} for r in rows]
    except Exception:
        return []


def count_notes(scope: str | None = None, db_path=db.DB_PATH) -> int:
    conn = db.connect(db_path)
    if scope:
        n = conn.execute("SELECT COUNT(*) c FROM mem_notes WHERE scope=?", (scope,)).fetchone()["c"]
    else:
        n = conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"]
    conn.close()
    return n


def bump(note_id: int, by: int = 1, db_path=db.DB_PATH) -> None:
    """Record a use (hit) and refresh recency — feeds retention/eviction."""
    conn = db.connect(db_path)
    with conn:
        conn.execute("UPDATE mem_notes SET hits=hits+?, updated_at=datetime('now','localtime') WHERE id=?",
                     (by, note_id))
    conn.close()


def forget(key: str | None = None, note_id: int | None = None,
           scope: str = "global", db_path=db.DB_PATH) -> bool:
    """Delete a note by key (within scope) or by id. Returns True if removed."""
    conn = db.connect(db_path)
    with conn:
        if note_id is not None:
            ids = [note_id]
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM mem_notes WHERE scope=? AND key=?", (scope, key)).fetchall()]
        for i in ids:
            conn.execute("DELETE FROM mem_vec WHERE note_id=?", (i,))  # drop semantic index too
        if note_id is not None:
            cur = conn.execute("DELETE FROM mem_notes WHERE id=?", (note_id,))
        else:
            cur = conn.execute("DELETE FROM mem_notes WHERE scope=? AND key=?", (scope, key))
    n = cur.rowcount
    conn.close()
    return n > 0


def clear_notes(scope: str | None = None, db_path=db.DB_PATH) -> int:
    """Delete notes and their semantic-index rows. ``scope=None`` wipes EVERY note
    in *db_path* (used by the dashboard to clear all agent memory). FTS stays in
    sync via table triggers; ``mem_vec`` has no trigger, so it is cleared here.
    Returns the number of notes removed. Irreversible."""
    conn = db.connect(db_path)
    with conn:
        if scope is None:
            n = conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"]
            conn.execute("DELETE FROM mem_vec")
            conn.execute("DELETE FROM mem_notes")
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM mem_notes WHERE scope=?", (scope,)).fetchall()]
            n = len(ids)
            for i in ids:
                conn.execute("DELETE FROM mem_vec WHERE note_id=?", (i,))
            conn.execute("DELETE FROM mem_notes WHERE scope=?", (scope,))
    conn.close()
    return n


def _score(importance: float, hits: int, age_days: float) -> float:
    """Retention score: importance × usage, decayed by age (30-day half-life)."""
    return importance * (1.0 + math.log1p(hits)) * (0.5 ** (age_days / _HALFLIFE_DAYS))


def evict(scope: str, max_notes: int = MAX_NOTES_PER_SCOPE, db_path=db.DB_PATH) -> int:
    """Trim a scope back to `max_notes`, dropping the lowest-scoring WARM
    agent/auto notes first. HOT and user-authored notes are never evicted.
    Returns the number removed."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, importance, hits, tier, source, "
        "julianday('now','localtime') - julianday(updated_at) AS age FROM mem_notes WHERE scope=?",
        (scope,),
    ).fetchall()
    overflow = len(rows) - max_notes
    if overflow <= 0:
        conn.close()
        return 0
    evictable = [r for r in rows if r["tier"] != "hot" and r["source"] != "user"]
    evictable.sort(key=lambda r: _score(r["importance"], r["hits"], r["age"] or 0.0))
    ids = [r["id"] for r in evictable[:overflow]]
    with conn:
        for i in ids:
            conn.execute("DELETE FROM mem_vec WHERE note_id=?", (i,))
            conn.execute("DELETE FROM mem_notes WHERE id=?", (i,))
    conn.close()
    return len(ids)


def promote_hot(scope: str, hits_threshold: int = PROMOTE_HITS, db_path=db.DB_PATH) -> int:
    """Promote frequently-recalled WARM notes to HOT so they get injected at
    session start. Part of the self-improving loop. Returns the number promoted."""
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute(
            "UPDATE mem_notes SET tier='hot', updated_at=datetime('now','localtime') "
            "WHERE scope=? AND tier='warm' AND hits>=?",
            (scope, hits_threshold),
        )
    n = cur.rowcount
    conn.close()
    return n


# ----- conversation turns (COLD store + dev-dashboard history) --------------

def log_turn(chat_id: int | None, session_id: str | None, user_text: str,
             assistant_text: str, db_path=db.DB_PATH) -> None:
    """Append a user/assistant exchange to the COLD ``conv_turns`` store.

    Feeds two things: the dev dashboard's Telegram history view, and the cold
    layer of hybrid recall (the FTS index stays in sync via table triggers).
    Gated by capture level (off at level 3). Best-effort; never raises so a
    logging hiccup can't break a chat turn."""
    from . import devcapture
    if not devcapture.telegram_enabled():
        return
    try:
        conn = db.connect(db_path)
        with conn:
            for role, content in (("user", user_text), ("assistant", assistant_text)):
                if content and content.strip():
                    conn.execute(
                        "INSERT INTO conv_turns (chat_id,session_id,role,content) "
                        "VALUES (?,?,?,?)",
                        (chat_id, session_id, role, content),
                    )
        conn.close()
    except Exception:
        import logging
        logging.getLogger("cio.memory").warning("log_turn failed", exc_info=True)


def delete_turns_on_day(day: str, db_path=db.DB_PATH) -> int:
    """Delete every conversation turn whose LOCAL calendar day == *day* (YYYY-MM-DD),
    matching how the dashboard groups them. Drops the semantic-index rows too; FTS
    stays in sync via triggers. Returns the number of turns removed. Irreversible."""
    from . import timeutil
    conn = db.connect(db_path)
    ids = [r["id"] for r in conn.execute("SELECT id, ts FROM conv_turns").fetchall()
           if timeutil.local_day(r["ts"]) == day]
    with conn:
        for i in ids:
            conn.execute("DELETE FROM turn_vec WHERE turn_id=?", (i,))
            conn.execute("DELETE FROM conv_turns WHERE id=?", (i,))
    conn.close()
    return len(ids)


def conv_history(chat_id: int | None = None, limit: int | None = 200,
                 db_path=db.DB_PATH) -> list[dict]:
    """Recent conversation turns, newest first. All chats when chat_id is None.
    ``limit=None`` returns every turn (no cap)."""
    conn = db.connect(db_path)
    sql = "SELECT id,chat_id,session_id,role,content,ts FROM conv_turns"
    args: list = []
    if chat_id is not None:
        sql += " WHERE chat_id=?"
        args.append(chat_id)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def conv_days(db_path=db.DB_PATH) -> list[dict]:
    """Distinct LOCAL days present in conv_turns, newest first, each with its turn
    count. Powers the Telegram page's day selector."""
    from . import timeutil
    conn = db.connect(db_path)
    rows = conn.execute("SELECT ts FROM conv_turns").fetchall()
    conn.close()
    tally: dict[str, int] = {}
    for r in rows:
        tally[timeutil.local_day(r["ts"])] = tally.get(timeutil.local_day(r["ts"]), 0) + 1
    return [{"day": d, "count": c} for d, c in sorted(tally.items(), reverse=True)]


def conv_history_on_day(day: str, db_path=db.DB_PATH) -> list[dict]:
    """All conversation turns on a given LOCAL day (YYYY-MM-DD), newest first."""
    from . import timeutil
    return [t for t in conv_history(limit=None, db_path=db_path)
            if timeutil.local_day(t["ts"]) == day]


# ----- session digests (rolling-session checkpoints) ------------------------

def add_digest(chat_id: int | None, session_id: str | None, summary: str,
               turn_count: int = 0, token_count: int = 0, db_path=db.DB_PATH) -> int:
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute(
            "INSERT INTO session_digests (chat_id,session_id,summary,turn_count,token_count) "
            "VALUES (?,?,?,?,?)",
            (chat_id, session_id, summary.strip(), turn_count, token_count),
        )
    nid = cur.lastrowid
    conn.close()
    return nid


def latest_digest(chat_id: int | None, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute(
        "SELECT summary FROM session_digests WHERE chat_id IS ? ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    conn.close()
    return row["summary"] if row else None


# ----- user profile (USER.md equivalent) ------------------------------------

_PROFILE_FIELDS = ("role", "stack", "prefs", "goals")


def get_profile(scope: str = "global", db_path=db.DB_PATH) -> dict:
    conn = db.connect(db_path)
    row = conn.execute("SELECT * FROM user_profile WHERE scope=?", (scope,)).fetchone()
    conn.close()
    return {k: row[k] for k in _PROFILE_FIELDS if row and row[k]} if row else {}


def set_profile(scope: str = "global", db_path=db.DB_PATH, **fields) -> None:
    """Upsert profile fields (role/stack/prefs/goals); only provided keys change."""
    cols = {k: v for k, v in fields.items() if k in _PROFILE_FIELDS}
    if not cols:
        return
    conn = db.connect(db_path)
    with conn:
        conn.execute("INSERT OR IGNORE INTO user_profile (scope) VALUES (?)", (scope,))
        sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=datetime('now','localtime')"
        conn.execute(f"UPDATE user_profile SET {sets} WHERE scope=?",
                     (*cols.values(), scope))
    conn.close()


# ----- runtime meta ---------------------------------------------------------

def get_meta(key: str, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_meta(key: str, value: str, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    conn.close()


# ----- chat registry --------------------------------------------------------

def touch_chat(chat_id: int, db_path=db.DB_PATH) -> None:
    """Ensure a chat row exists (no-op if already present)."""
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id) VALUES (?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id,),
        )
    conn.close()


def set_subscribed(chat_id: int, subscribed: bool, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id, subscribed, updated_at) "
            "VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(chat_id) DO UPDATE SET subscribed=excluded.subscribed, "
            "updated_at=datetime('now','localtime')",
            (chat_id, 1 if subscribed else 0),
        )
    conn.close()


def all_chats(db_path=db.DB_PATH) -> list[int]:
    """Every chat the bot has ever interacted with (for boot-time pre-warm)."""
    conn = db.connect(db_path)
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def subscribed_chats(db_path=db.DB_PATH) -> list[int]:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT chat_id FROM chats WHERE subscribed = 1"
    ).fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def list_subscribers(db_path=db.DB_PATH) -> list[dict]:
    """Subscribed chats (digest + watchlist briefing recipients) with chat_id and
    updated_at (when the subscription last changed), newest first. For the dashboard
    subscribers page."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT chat_id, updated_at FROM chats WHERE subscribed = 1 "
        "ORDER BY updated_at DESC, chat_id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session_id(chat_id: int, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute(
        "SELECT session_id FROM chats WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row["session_id"] if row and row["session_id"] else None


def set_session_id(chat_id: int, session_id: str, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id, session_id, updated_at) "
            "VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id, "
            "updated_at=datetime('now','localtime')",
            (chat_id, session_id),
        )
    conn.close()


# ----- playbooks (learning loop) --------------------------------------------
# Reusable procedures distilled from recurring tasks. Steps reference TOOLS, not
# cached numbers — the figures firewall applies, so a playbook never goes stale.

def add_playbook(name: str, steps: str, scope: str = "global", db_path=db.DB_PATH) -> int:
    _guard_figures(steps)
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO playbooks (scope,name,steps) VALUES (?,?,?) "
            "ON CONFLICT(scope,name) DO UPDATE SET steps=excluded.steps",
            (scope, name.strip(), steps.strip()),
        )
        pid = conn.execute("SELECT id FROM playbooks WHERE scope=? AND name=?",
                           (scope, name.strip())).fetchone()["id"]
    conn.close()
    return pid


def get_playbook(name: str, scope: str = "global", db_path=db.DB_PATH) -> dict | None:
    conn = db.connect(db_path)
    row = conn.execute("SELECT * FROM playbooks WHERE name=? AND scope IN (?, 'global')",
                       (name.strip(), scope)).fetchone()
    if row:
        with conn:
            conn.execute("UPDATE playbooks SET hits=hits+1 WHERE id=?", (row["id"],))
    conn.close()
    return dict(row) if row else None


def list_playbooks(scope: str = "global", db_path=db.DB_PATH) -> list[dict]:
    """Playbooks visible to a scope (its own + global), most-used first."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, scope, name, steps, hits FROM playbooks WHERE scope IN (?, 'global') "
        "ORDER BY hits DESC, name",
        (scope,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_playbooks(db_path=db.DB_PATH) -> list[dict]:
    """Every playbook across all scopes — for the dashboard management view."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, scope, name, steps, hits, created_at FROM playbooks "
        "ORDER BY scope, hits DESC, name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_playbook(pid: int, db_path=db.DB_PATH) -> int:
    """Delete one playbook by id. Returns rows removed (0 if not found)."""
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute("DELETE FROM playbooks WHERE id=?", (pid,))
    n = cur.rowcount
    conn.close()
    return n
