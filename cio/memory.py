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
# Hot is a privilege, not a ratchet: without a cap + demotion the bump→promote
# loop slowly turns every note hot, hot notes are never evicted, and once a scope's
# hot count exceeds MAX_NOTES eviction can never reach its target again. Non-user
# hot notes beyond this cap are demoted back to warm by retention score.
MAX_HOT_PER_SCOPE = int(os.getenv("CIO_MAX_HOT", "30"))
# COLD-store retention: conv_turns (and their 768-dim embeddings, ~3 KB/row) would
# otherwise grow without bound on a 24/7 runtime — and the vec0 KNN scan is linear
# in rows, so search slows as it grows. 0 disables the respective limit.
TURN_RETAIN_DAYS = int(os.getenv("CIO_TURN_RETAIN_DAYS", "365"))
MAX_TURNS = int(os.getenv("CIO_MAX_TURNS", "50000"))
# Keys that must survive eviction even as warm notes (long-term month memory).
_EVICT_PROTECT_PREFIXES = ("monthly_rollup:",)
# Expired-note filter, shared by every read path (recall.py keeps its own copy).
_NOT_EXPIRED = "(expires_at IS NULL OR expires_at > datetime('now','localtime'))"


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
    # \b-anchored: unanchored "p/?e" matched the "pe" inside words like
    # "operator"/"open", blocking any note that also contained a digit.
    r"\byield\b|multiple|\bp/?e\b|\bcagr\b|\bfcf\b|payout"
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
             ttl_days: float | None = None, db_path=db.DB_PATH) -> int:
    """Store a qualitative note; returns its id. Upserts when `key` is given.

    Rejects financial figures (figures firewall). `tier` is 'hot' (injected at
    session start) or 'warm' (recall on demand). `ttl_days` sets `expires_at` for
    time-bound notes ("watch FOMC next week") — expired notes stop surfacing in
    every read path and are purged by maintain()."""
    value = value.strip()
    _guard_figures(value)
    expires = f"+{float(ttl_days)} days" if ttl_days and ttl_days > 0 else None
    conn = db.connect(db_path)
    with conn:
        if key:
            conn.execute(
                "INSERT INTO mem_notes (scope,tier,key,value,importance,source,expires_at) "
                "VALUES (?,?,?,?,?,?,datetime('now','localtime',?)) "
                "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, "
                "tier=excluded.tier, importance=excluded.importance, "
                "source=excluded.source, expires_at=excluded.expires_at, "
                "updated_at=datetime('now','localtime')",
                (scope, tier, key, value, importance, source, expires),
            )
            nid = conn.execute("SELECT id FROM mem_notes WHERE scope=? AND key=?",
                               (scope, key)).fetchone()["id"]
        else:
            cur = conn.execute(
                "INSERT INTO mem_notes (scope,tier,key,value,importance,source,expires_at) "
                "VALUES (?,?,?,?,?,?,datetime('now','localtime',?))",
                (scope, tier, key, value, importance, source, expires),
            )
            nid = cur.lastrowid
    conn.close()
    # Best-effort semantic indexing: an embedding hiccup must not fail a save that
    # is already committed (the note would look "failed" yet exist, and retries
    # would duplicate). reindex_missing() in maintain() heals any gap.
    try:
        _recall.index_note(nid, value, db_path)
    except Exception:
        import logging
        logging.getLogger("cio.memory").warning("note %s saved but not indexed", nid,
                                                exc_info=True)
    if count_notes(scope, db_path=db_path) > MAX_NOTES_PER_SCOPE:
        evict(scope, max_notes=MAX_NOTES_PER_SCOPE, db_path=db_path)
    return nid


def recall(key: str, scope: str = "global", db_path=db.DB_PATH) -> str | None:
    """Exact-key lookup within a scope; bumps the hit counter. Expired notes
    (past their `expires_at`) are invisible here."""
    conn = db.connect(db_path)
    row = conn.execute(
        f"SELECT id, value FROM mem_notes WHERE scope=? AND key=? AND {_NOT_EXPIRED}",
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
               include_expired: bool = False, db_path=db.DB_PATH) -> list[dict]:
    """Notes for a scope, most important/recent first. Expired notes are hidden
    unless `include_expired` (the dashboard may want to show them)."""
    conn = db.connect(db_path)
    sql = "SELECT * FROM mem_notes WHERE scope=?"
    args: list = [scope]
    if not include_expired:
        sql += f" AND {_NOT_EXPIRED}"
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


def _evict_protected(row) -> bool:
    """Never evict: HOT, user-authored, or long-term keyed notes (monthly rollups
    stay searchable month memory even after the hot cap demotes them to warm)."""
    if row["tier"] == "hot" or row["source"] == "user":
        return True
    key = row["key"]
    return bool(key) and key.startswith(_EVICT_PROTECT_PREFIXES)


def evict(scope: str, max_notes: int = MAX_NOTES_PER_SCOPE, db_path=db.DB_PATH) -> int:
    """Trim a scope back to `max_notes`: expired notes first, then the
    lowest-scoring WARM agent/auto notes. HOT, user-authored, and monthly-rollup
    notes are never evicted. Returns the number removed."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, importance, hits, tier, source, key, "
        f"NOT {_NOT_EXPIRED} AS expired, "
        "julianday('now','localtime') - julianday(updated_at) AS age FROM mem_notes WHERE scope=?",
        (scope,),
    ).fetchall()
    overflow = len(rows) - max_notes
    if overflow <= 0:
        conn.close()
        return 0
    expired = [r for r in rows if r["expired"]]
    evictable = [r for r in rows if not r["expired"] and not _evict_protected(r)]
    evictable.sort(key=lambda r: _score(r["importance"], r["hits"], r["age"] or 0.0))
    ids = [r["id"] for r in (expired + evictable)[:overflow]]
    with conn:
        for i in ids:
            conn.execute("DELETE FROM mem_vec WHERE note_id=?", (i,))
            conn.execute("DELETE FROM mem_notes WHERE id=?", (i,))
    conn.close()
    if len(ids) < overflow:
        import logging
        logging.getLogger("cio.memory").warning(
            "evict: scope %s still %d over cap — too many protected (hot/user) notes; "
            "check CIO_MAX_HOT / enforce_hot_cap", scope, overflow - len(ids))
    return len(ids)


def promote_hot(scope: str, hits_threshold: int = PROMOTE_HITS, db_path=db.DB_PATH) -> int:
    """Promote frequently-recalled WARM notes to HOT so they get injected at
    session start. Part of the self-improving loop. Returns the number promoted.
    The hot cap is enforced afterwards so promotion can never ratchet a scope
    into an all-hot (uneveictable) state."""
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute(
            "UPDATE mem_notes SET tier='hot', updated_at=datetime('now','localtime') "
            "WHERE scope=? AND tier='warm' AND hits>=?",
            (scope, hits_threshold),
        )
    n = cur.rowcount
    conn.close()
    if n:
        enforce_hot_cap(scope, db_path=db_path)
    return n


def enforce_hot_cap(scope: str, max_hot: int = MAX_HOT_PER_SCOPE,
                    db_path=db.DB_PATH) -> int:
    """Demote the lowest-scoring non-user HOT notes back to WARM so at most
    `max_hot` of them stay hot. Keeps the injected block high-signal (old monthly
    rollups and stale promotions decay out of injection but remain searchable —
    the same idea as OpenClaw's gated 'dreaming' promotion). `source='user'` hot
    notes are never demoted and don't count toward the cap. Returns demoted count.
    Demotion does NOT refresh updated_at, so a demoted note keeps decaying."""
    if max_hot <= 0:
        return 0
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, importance, hits, "
        "julianday('now','localtime') - julianday(updated_at) AS age "
        "FROM mem_notes WHERE scope=? AND tier='hot' AND source!='user'",
        (scope,),
    ).fetchall()
    if len(rows) <= max_hot:
        conn.close()
        return 0
    ranked = sorted(rows, key=lambda r: _score(r["importance"], r["hits"], r["age"] or 0.0),
                    reverse=True)
    ids = [r["id"] for r in ranked[max_hot:]]
    with conn:
        conn.executemany("UPDATE mem_notes SET tier='warm' WHERE id=?",
                         [(i,) for i in ids])
    conn.close()
    return len(ids)


def purge_expired(scope: str | None = None, db_path=db.DB_PATH) -> int:
    """Delete notes past their `expires_at` (all scopes when scope is None),
    dropping their semantic-index rows too. Returns the number removed."""
    conn = db.connect(db_path)
    sql = f"SELECT id FROM mem_notes WHERE NOT {_NOT_EXPIRED}"
    args: list = []
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    ids = [r["id"] for r in conn.execute(sql, args).fetchall()]
    with conn:
        for i in ids:
            conn.execute("DELETE FROM mem_vec WHERE note_id=?", (i,))
            conn.execute("DELETE FROM mem_notes WHERE id=?", (i,))
    conn.close()
    return len(ids)


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
        new_turns: list[tuple[int, str]] = []
        with conn:
            for role, content in (("user", user_text), ("assistant", assistant_text)):
                if content and content.strip():
                    cur = conn.execute(
                        "INSERT INTO conv_turns (chat_id,session_id,role,content) "
                        "VALUES (?,?,?,?)",
                        (chat_id, session_id, role, content),
                    )
                    new_turns.append((cur.lastrowid, content))
        conn.close()
        # Semantic index each turn on write (FTS stays in sync via table triggers),
        # so cold-store hybrid recall has BOTH halves. Consistent with remember()
        # indexing notes and add_digest() indexing digests. Best-effort per turn.
        for tid, content in new_turns:
            try:
                _recall.index_turn(tid, content, db_path)
            except Exception:
                import logging
                logging.getLogger("cio.memory").debug("turn index failed", exc_info=True)
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


def prune_turns(retain_days: int = TURN_RETAIN_DAYS, max_rows: int = MAX_TURNS,
                db_path=db.DB_PATH) -> int:
    """Bound the COLD store: delete conversation turns older than `retain_days`,
    then the oldest rows beyond `max_rows`. Embedding rows go with them (FTS stays
    in sync via triggers). Either limit can be disabled with 0. Long-term context
    survives in session digests and monthly rollups; this only trims raw turns —
    without it conv_turns/turn_vec grow without bound on a 24/7 runtime.
    Returns the number of turns removed."""
    conn = db.connect(db_path)

    def _drop(ids: list[int]) -> None:
        with conn:
            for i in ids:
                conn.execute("DELETE FROM turn_vec WHERE turn_id=?", (i,))
                conn.execute("DELETE FROM conv_turns WHERE id=?", (i,))

    removed = 0
    if retain_days and retain_days > 0:
        old = [r["id"] for r in conn.execute(
            "SELECT id FROM conv_turns WHERE ts < datetime('now','localtime',?)",
            (f"-{int(retain_days)} days",)).fetchall()]
        _drop(old)
        removed += len(old)
    if max_rows and max_rows > 0:
        total = conn.execute("SELECT COUNT(*) c FROM conv_turns").fetchone()["c"]
        overflow = total - max_rows
        if overflow > 0:
            oldest = [r["id"] for r in conn.execute(
                "SELECT id FROM conv_turns ORDER BY id ASC LIMIT ?",
                (overflow,)).fetchall()]
            _drop(oldest)
            removed += len(oldest)
    conn.close()
    return removed


def maintain(db_path=db.DB_PATH, force: bool = False) -> dict:
    """Daily memory upkeep for one DB — the single maintenance entry point
    (scheduled like OpenClaw's dreaming sweep; see scheduler.memory_maintenance):

    1. purge expired notes (TTL),
    2. prune the COLD turn store to its retention window,
    3. enforce the per-scope hot cap (demote stale hot notes),
    4. re-embed any rows the write path failed to index,
    5. re-verify the design's runtime invariants (cio/invariants.py) — violations
       are logged, returned in the summary, and persisted to
       `meta.last_invariant_violations` for the dashboard.

    Guarded once per local day via the `last_maintenance_day` meta marker unless
    `force`. Best-effort throughout — never raises. Returns a summary dict."""
    import logging
    log = logging.getLogger("cio.memory")
    from . import timeutil
    today = timeutil.today_local()
    summary: dict = {"ran": False}
    try:
        if not force and get_meta("last_maintenance_day", db_path=db_path) == today:
            return summary
    except Exception:
        return summary
    summary["ran"] = True
    try:
        summary["purged"] = purge_expired(db_path=db_path)
    except Exception:
        log.warning("maintain: purge_expired failed", exc_info=True)
    try:
        summary["turns_pruned"] = prune_turns(db_path=db_path)
    except Exception:
        log.warning("maintain: prune_turns failed", exc_info=True)
    try:
        summary["demoted"] = sum(
            enforce_hot_cap(s["scope"], db_path=db_path)
            for s in list_scopes(db_path=db_path))
    except Exception:
        log.warning("maintain: enforce_hot_cap failed", exc_info=True)
    try:
        summary["reindexed"] = _recall.reindex_missing(db_path=db_path)
    except Exception:
        log.warning("maintain: reindex_missing failed", exc_info=True)
    try:
        # Invariants run LAST so they verify the post-maintenance state (e.g. no
        # expired notes left, hot caps hold). check() never raises.
        import json
        from . import invariants
        summary["violations"] = invariants.check(db_path=db_path)
        set_meta("last_invariant_violations", json.dumps(summary["violations"]),
                 db_path=db_path)
    except Exception:
        log.warning("maintain: invariant check failed", exc_info=True)
    try:
        set_meta("last_maintenance_day", today, db_path=db_path)
    except Exception:
        log.warning("maintain: could not persist day marker", exc_info=True)
    log.info("memory maintenance for %s: %s", db_path, summary)
    return summary


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
    summary = summary.strip()
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute(
            "INSERT INTO session_digests (chat_id,session_id,summary,turn_count,token_count) "
            "VALUES (?,?,?,?,?)",
            (chat_id, session_id, summary, turn_count, token_count),
        )
    nid = cur.lastrowid
    conn.close()
    # Index for long-term hybrid recall (FTS stays in sync via table triggers).
    # Best-effort: an embedding hiccup must not lose the digest row already written.
    try:
        _recall.index_digest(nid, summary, db_path)
    except Exception:
        import logging
        logging.getLogger("cio.memory").debug("digest index failed", exc_info=True)
    return nid


def latest_digest(chat_id: int | None, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute(
        "SELECT summary FROM session_digests WHERE chat_id IS ? ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    conn.close()
    return row["summary"] if row else None


def digests_in_month(chat_id: int | None, year_month: str,
                     db_path=db.DB_PATH) -> list[dict]:
    """All session digests for *chat_id* created in *year_month* ('YYYY-MM'), oldest
    first. Feeds the monthly rollup (digest-of-digests). created_at is local time."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, summary, created_at FROM session_digests "
        "WHERE chat_id IS ? AND substr(created_at,1,7)=? ORDER BY id ASC",
        (chat_id, year_month),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def _last_turn_day_key(chat_id: int | None) -> str:
    return f"last_turn_day:{chat_id if chat_id is not None else 'global'}"


def get_last_turn_day(chat_id: int | None, db_path=db.DB_PATH) -> str | None:
    """Local calendar day (YYYY-MM-DD) of this chat's most recent agent turn, or
    None if it has never spoken. Persisted across restarts so the agent can detect
    a day boundary and roll its session even after the process bounced."""
    return get_meta(_last_turn_day_key(chat_id), db_path=db_path)


def set_last_turn_day(chat_id: int | None, day: str, db_path=db.DB_PATH) -> None:
    """Record the local day of this chat's most recent agent turn."""
    set_meta(_last_turn_day_key(chat_id), day, db_path=db_path)


# ----- playbooks (learning loop) --------------------------------------------
# Reusable procedures distilled from recurring tasks. Steps reference TOOLS, not
# cached numbers — the figures firewall applies, so a playbook never goes stale.

def add_playbook(name: str, steps: str, scope: str = "global", db_path=db.DB_PATH) -> int:
    _guard_figures(steps)
    conn = db.connect(db_path)
    with conn:
        # created_at is set explicitly to LOCAL time. The table default is only
        # localtime on DBs created after the timezone fix; older DBs kept a UTC
        # default that CREATE TABLE IF NOT EXISTS can't change, so don't rely on it.
        conn.execute(
            "INSERT INTO playbooks (scope,name,steps,created_at) "
            "VALUES (?,?,?,datetime('now','localtime')) "
            "ON CONFLICT(scope,name) DO UPDATE SET steps=excluded.steps",
            (scope, name.strip(), steps.strip()),
        )
        pid = conn.execute("SELECT id FROM playbooks WHERE scope=? AND name=?",
                           (scope, name.strip())).fetchone()["id"]
    conn.close()
    return pid


def promote_playbook(pid: int, db_path=db.DB_PATH) -> dict:
    """Promote a chat-scoped playbook to **global**: upsert its (name, steps) into the
    global scope and remove the chat-scoped original (so it isn't duplicated). Returns
    ``{promoted, name, global_id}``. No-op (``promoted=False``) if it is already global.
    Raises ValueError if *pid* doesn't exist."""
    conn = db.connect(db_path)
    row = conn.execute("SELECT id, scope, name, steps FROM playbooks WHERE id=?",
                       (pid,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"no playbook with id {pid}")
    if row["scope"] == "global":
        conn.close()
        return {"promoted": False, "name": row["name"], "global_id": row["id"]}
    name, steps = row["name"], row["steps"]
    _guard_figures(steps)
    with conn:
        conn.execute(
            "INSERT INTO playbooks (scope,name,steps,created_at) "
            "VALUES ('global',?,?,datetime('now','localtime')) "
            "ON CONFLICT(scope,name) DO UPDATE SET steps=excluded.steps",
            (name, steps),
        )
        gid = conn.execute("SELECT id FROM playbooks WHERE scope='global' AND name=?",
                           (name,)).fetchone()["id"]
        conn.execute("DELETE FROM playbooks WHERE id=?", (pid,))   # drop the chat copy
    conn.close()
    return {"promoted": True, "name": name, "global_id": gid}


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
