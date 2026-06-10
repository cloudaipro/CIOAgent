"""Runtime invariant checks — production as a continuous test.

Tests prove the presence of specified behavior, never the absence of bugs
(the 2026-06-09 memory-misattribution bug passed every unit test; the 2026-06-10
stale-process window passed all 455). So the properties the design promises are
re-verified nightly against the REAL database by `memory.maintain()`, and any
violation is logged, persisted to `meta.last_invariant_violations` (JSON), and
shown on the dashboard overview.

Checks are read-only, cheap (plain SQL), and best-effort: a check that errors
reports itself as a violation rather than aborting the sweep.

Invariants verified:
  I1  one session never spans more than one local day   (misattribution class)
  I2  every indexed row has its embedding, no orphans    (hybrid-recall parity)
  I3  per-scope caps hold: notes <= MAX_NOTES, non-user hot <= MAX_HOT
  I4  no expired note lingers after maintenance          (TTL actually enforced)
  I5  no financial figure sits in memory                 (firewall held)
  I6  the running process is not older than the repo     (stale-process class)
"""
from __future__ import annotations

import logging

from . import db, memory, version

log = logging.getLogger("cio.invariants")

# I1 only flags sessions still ACTIVE recently: the pre-fix history (e.g. the
# 8-day session of the misattribution incident) would otherwise nag forever
# even though the day-roll has long since retired it.
RECENT_DAYS = 2


def _check_session_day(conn) -> list[str]:
    """I1: no session_id with turns on >1 local day among recently active sessions."""
    rows = conn.execute(
        "SELECT session_id, COUNT(DISTINCT substr(ts,1,10)) days, MAX(ts) newest "
        "FROM conv_turns WHERE session_id IS NOT NULL GROUP BY session_id "
        "HAVING days > 1 AND newest >= datetime('now','localtime',?)",
        (f"-{RECENT_DAYS} days",)).fetchall()
    return [f"I1 session {r['session_id'][:12]}… spans {r['days']} local days "
            f"(newest turn {r['newest']}) — day-boundary roll did not fire"
            for r in rows]


def _check_vector_parity(conn) -> list[str]:
    """I2: vec rows ↔ source rows, both directions, all three kinds."""
    out = []
    pairs = [("mem_notes", "mem_vec", "note_id"),
             ("conv_turns", "turn_vec", "turn_id"),
             ("session_digests", "digest_vec", "digest_id")]
    for src, vec, col in pairs:
        missing = conn.execute(
            f"SELECT COUNT(*) c FROM {src} WHERE id NOT IN (SELECT {col} FROM {vec})"
        ).fetchone()["c"]
        orphans = conn.execute(
            f"SELECT COUNT(*) c FROM {vec} WHERE {col} NOT IN (SELECT id FROM {src})"
        ).fetchone()["c"]
        if missing:
            out.append(f"I2 {missing} {src} row(s) lack an embedding "
                       f"(semantically invisible; reindex_missing should heal)")
        if orphans:
            out.append(f"I2 {orphans} orphan row(s) in {vec} (source row deleted)")
    return out


def _check_scope_caps(conn) -> list[str]:
    """I3: per-scope note cap and non-user hot cap."""
    out = []
    for r in conn.execute("SELECT scope, COUNT(*) n FROM mem_notes GROUP BY scope "
                          "HAVING n > ?", (memory.MAX_NOTES_PER_SCOPE,)).fetchall():
        out.append(f"I3 scope {r['scope']} holds {r['n']} notes "
                   f"(cap {memory.MAX_NOTES_PER_SCOPE}) — eviction starving?")
    for r in conn.execute(
            "SELECT scope, COUNT(*) n FROM mem_notes "
            "WHERE tier='hot' AND source!='user' GROUP BY scope HAVING n > ?",
            (memory.MAX_HOT_PER_SCOPE,)).fetchall():
        out.append(f"I3 scope {r['scope']} has {r['n']} non-user hot notes "
                   f"(cap {memory.MAX_HOT_PER_SCOPE}) — demotion not applied?")
    return out


def _check_no_expired(conn) -> list[str]:
    """I4: maintenance purges expired notes; none should remain afterwards."""
    n = conn.execute(
        "SELECT COUNT(*) c FROM mem_notes "
        "WHERE expires_at IS NOT NULL AND expires_at <= datetime('now','localtime')"
    ).fetchone()["c"]
    return [f"I4 {n} expired note(s) still present after purge"] if n else []


def _check_no_figures(conn) -> list[str]:
    """I5: the figures firewall is the final write gate; nothing in mem_notes
    should look like a financial figure. Flags rows that predate the firewall
    or slipped through a since-fixed gap."""
    bad = [r["id"] for r in conn.execute("SELECT id, value FROM mem_notes").fetchall()
           if memory._looks_like_figure(r["value"])]
    if bad:
        shown = ", ".join(str(i) for i in bad[:10])
        return [f"I5 {len(bad)} note(s) contain figure-like content "
                f"(ids {shown}{'…' if len(bad) > 10 else ''}) — review/forget them"]
    return []


def check(db_path=None) -> list[str]:
    """Run every invariant against *db_path*; returns violation strings ([] = OK).
    Read-only and never raises — a broken check reports itself."""
    path = db_path or db.DB_PATH
    violations: list[str] = []
    checks = [_check_session_day, _check_vector_parity, _check_scope_caps,
              _check_no_expired, _check_no_figures]
    try:
        conn = db.connect(path)
    except Exception as exc:
        return [f"I0 cannot open {path}: {exc}"]
    for fn in checks:
        try:
            violations += fn(conn)
        except Exception as exc:
            violations.append(f"I0 check {fn.__name__} failed: {exc}")
    conn.close()
    # I6 is repo-level, not per-DB; only meaningful for the main DB where the
    # boot stamp lives (committee.db has no stamp -> check returns None there).
    try:
        stale = version.stale_process_check(db_path=path)
        if stale:
            violations.append("I6 " + stale)
    except Exception as exc:
        violations.append(f"I0 stale-process check failed: {exc}")
    for v in violations:
        log.warning("invariant violation: %s", v)
    return violations
