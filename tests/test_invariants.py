"""Unit tests for the runtime invariant checker (cio/invariants.py).

Each test crafts a violating DB state directly (bypassing the write-path guards,
exactly like a bug would) and asserts the nightly check reports it — and that a
clean DB reports nothing.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from cio import db, invariants, memory, version  # noqa: E402


def _tmpdb() -> Path:
    p = Path(tempfile.mkdtemp()) / "t.db"
    db.init(p)
    return p


def _insert_turn(p, session_id, ts_offset_days=0, day=None):
    conn = db.connect(p)
    with conn:
        if day:
            ts = f"{day} 12:00:00"
        else:
            ts = conn.execute("SELECT datetime('now','localtime',?) t",
                              (f"-{ts_offset_days} days",)).fetchone()["t"]
        conn.execute("INSERT INTO conv_turns (chat_id,session_id,role,content,ts) "
                     "VALUES (1,?,?,?,?)", (session_id, "user", "x", ts))
    conn.close()


def test_clean_db_has_no_violations():
    p = _tmpdb()
    memory.remember("qualitative note", db_path=p)
    assert invariants.check(p) == []


def test_i1_flags_recent_multiday_session():
    p = _tmpdb()
    _insert_turn(p, "sess-live", ts_offset_days=1)
    _insert_turn(p, "sess-live", ts_offset_days=0)
    out = invariants.check(p)
    assert any("I1" in v and "sess-live"[:8] in v for v in out)


def test_i1_ignores_old_history():
    """A pre-fix multi-day session must stop nagging once it is inactive."""
    p = _tmpdb()
    _insert_turn(p, "sess-old", day="2026-01-01")
    _insert_turn(p, "sess-old", day="2026-01-05")
    assert not [v for v in invariants.check(p) if "I1" in v]


def test_i2_flags_missing_and_orphan_vectors():
    p = _tmpdb()
    conn = db.connect(p)
    with conn:
        # note without embedding (write-path hiccup)
        conn.execute("INSERT INTO mem_notes (scope,value) VALUES ('global','no vec')")
        # orphan vector (note deleted without cleanup)
        import sqlite_vec
        conn.execute("INSERT INTO mem_vec(note_id, embedding) VALUES (999, ?)",
                     (sqlite_vec.serialize_float32([0.0] * db.EMBED_DIM),))
    conn.close()
    out = invariants.check(p)
    assert any("I2" in v and "lack an embedding" in v for v in out)
    assert any("I2" in v and "orphan" in v for v in out)


def test_i3_flags_hot_over_cap():
    p = _tmpdb()
    conn = db.connect(p)
    with conn:
        for i in range(memory.MAX_HOT_PER_SCOPE + 2):
            conn.execute("INSERT INTO mem_notes (scope,tier,value) "
                         "VALUES ('chat:5','hot',?)", (f"h{i}",))
    conn.close()
    out = invariants.check(p)
    assert any("I3" in v and "hot" in v for v in out)


def test_i4_flags_lingering_expired():
    p = _tmpdb()
    conn = db.connect(p)
    with conn:
        conn.execute("INSERT INTO mem_notes (scope,value,expires_at) "
                     "VALUES ('global','dead', datetime('now','localtime','-1 day'))")
    conn.close()
    assert any("I4" in v for v in invariants.check(p))
    memory.purge_expired(db_path=p)
    assert not [v for v in invariants.check(p) if "I4" in v]


def test_i5_flags_smuggled_figure():
    p = _tmpdb()
    conn = db.connect(p)
    with conn:   # direct SQL bypasses the firewall, like a legacy/manual row
        conn.execute("INSERT INTO mem_notes (scope,value) "
                     "VALUES ('global','AAPL price is $230 and ROE 141%')")
    conn.close()
    assert any("I5" in v for v in invariants.check(p))


def test_i6_stale_process_detection(monkeypatch):
    p = _tmpdb()
    # never stamped -> undeterminable -> no violation
    assert version.stale_process_check(db_path=p) is None
    # stamped with the current commit -> fine (dirty suffix ignored)
    cur = version.repo_commit()
    if cur is None:  # environment without git: nothing more to verify
        return
    memory.set_meta(version.BOOT_VERSION_KEY, cur + "+dirty", db_path=p)
    assert version.stale_process_check(db_path=p) is None
    # stamped with an older commit -> violation, and check() carries it as I6
    memory.set_meta(version.BOOT_VERSION_KEY, "0000000", db_path=p)
    assert "restart" in (version.stale_process_check(db_path=p) or "")
    assert any(v.startswith("I6") for v in invariants.check(p))


def test_maintain_persists_violations():
    p = _tmpdb()
    _insert_turn(p, "sess-bad", ts_offset_days=1)
    _insert_turn(p, "sess-bad", ts_offset_days=0)
    out = memory.maintain(db_path=p, force=True)
    assert any("I1" in v for v in out["violations"])
    stored = json.loads(memory.get_meta("last_invariant_violations", db_path=p))
    assert stored == out["violations"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
