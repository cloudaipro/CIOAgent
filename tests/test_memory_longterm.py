"""Long-term memory-architecture hardening tests (LLM-free, offline).

Covers the fixes for weaknesses found in the 2026-06 memory review:

1. Scoped recall — KNN/FTS candidates are pre-filtered to the allowed scopes in
   SQL, so a crowded DB (many committee scopes) can no longer starve a scope's
   recall or its semantic dedup.
2. TTL — `expires_at` is enforced on every read path and purged by maintenance.
3. Hot cap — promotion can no longer ratchet a scope into an all-hot
   (unevictable) state; the lowest-scoring non-user hot notes demote to warm.
4. Eviction protects monthly rollups and warns on starvation.
5. COLD-store retention — conv_turns/turn_vec are bounded by age and row count.
6. Vector-index self-healing — notes saved during an embedding hiccup are
   repaired by reindex_missing(), and the save itself no longer fails.
7. maintain() — single daily-guarded upkeep entry point.
8. db.connect() init cache — schema/migrations run once per process per path.

Run:  pytest -q tests/test_memory_longterm.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from cio import db, memory, recall  # noqa: E402


def _tmpdb() -> Path:
    p = Path(tempfile.mkdtemp()) / "t.db"
    db.init(p)
    return p


# ----- 1. scoped recall under crowding ---------------------------------------

def test_scoped_recall_survives_crowding():
    """An in-scope note must surface even when other scopes flood the KNN pool
    with near-identical text (the old post-filter design returned nothing)."""
    p = _tmpdb()
    target = "committee:risk"
    memory.remember("NVDA datacenter growth thesis remains intact", scope=target,
                    db_path=p)
    # 30 near-identical notes across other scopes — more than the KNN pool (20).
    for i in range(30):
        memory.remember(f"NVDA datacenter growth thesis remains intact v{i}",
                        scope=f"committee:other{i % 6}", db_path=p)
    hits = recall.search("NVDA datacenter growth", k=5, scope=target,
                         kinds=("note",), db_path=p, include_global=False)
    assert hits, "in-scope note crowded out of recall"
    assert all("v" not in h["text"].split()[-1] or not h["text"].endswith(tuple(
        f"v{i}" for i in range(30))) for h in hits)  # only the target-scope note


def test_scoped_recall_includes_global():
    p = _tmpdb()
    memory.remember("operator prefers swing trades", scope="global", db_path=p)
    for i in range(25):
        memory.remember(f"operator prefers swing trades alt {i}",
                        scope=f"chat:{100 + i}", db_path=p)
    hits = recall.search("swing trade preference", k=3, scope="chat:1",
                         kinds=("note",), db_path=p)
    assert any("operator prefers swing trades" == h["text"] for h in hits)


def test_nearest_in_scope_survives_crowding():
    """Semantic dedup must find the in-scope twin even when 30 other scopes hold
    the same text (otherwise duplicates re-accumulate and worsen the crowding)."""
    p = _tmpdb()
    text = "AAPL services moat is widening"
    nid = memory.remember(text, scope="committee:equity", db_path=p)
    for i in range(30):
        memory.remember(text + f" ({i})", scope=f"committee:x{i}", db_path=p)
    near = recall.nearest_in_scope(text, "committee:equity", db_path=p)
    assert near and near["id"] == nid and near["distance"] < 0.05


# ----- 2. TTL enforcement -----------------------------------------------------

def _expire_note(p: Path, nid: int) -> None:
    conn = db.connect(p)
    with conn:
        conn.execute("UPDATE mem_notes SET expires_at=datetime('now','localtime','-1 day') "
                     "WHERE id=?", (nid,))
    conn.close()


def test_ttl_sets_expiry_and_hides_expired():
    p = _tmpdb()
    nid = memory.remember("watch FOMC reaction next week", key="fomc",
                          ttl_days=7, db_path=p)
    note = memory.get_note(nid, db_path=p)
    assert note["expires_at"] is not None
    assert memory.recall("fomc", db_path=p) is not None
    _expire_note(p, nid)
    # hidden from every read path
    assert memory.recall("fomc", db_path=p) is None
    assert all(n["id"] != nid for n in memory.list_notes("global", db_path=p))
    assert any(n["id"] == nid
               for n in memory.list_notes("global", include_expired=True, db_path=p))
    hits = recall.search("FOMC reaction", k=5, scope="global", kinds=("note",),
                         db_path=p)
    assert all(h["id"] != nid for h in hits)


def test_purge_expired_removes_row_and_vector():
    p = _tmpdb()
    nid = memory.remember("temporary plan", ttl_days=1, db_path=p)
    _expire_note(p, nid)
    assert memory.purge_expired(db_path=p) == 1
    assert memory.get_note(nid, db_path=p) is None
    conn = db.connect(p)
    assert conn.execute("SELECT 1 FROM mem_vec WHERE note_id=?", (nid,)).fetchone() is None
    conn.close()


def test_remember_rejects_nothing_for_no_ttl():
    p = _tmpdb()
    nid = memory.remember("durable preference", db_path=p)
    assert memory.get_note(nid, db_path=p)["expires_at"] is None


def test_firewall_pe_keyword_is_word_bounded():
    """Regression: unanchored 'p/?e' matched the 'pe' inside ordinary words
    ('operator', 'open'), blocking any note that also contained a digit."""
    assert not memory._looks_like_figure("operator prefers swing trades alt 0")
    assert not memory._looks_like_figure("open 2 positions on breakout")
    assert memory._looks_like_figure("P/E of 31 is rich")        # real ratio still blocked
    assert memory._looks_like_figure("pe near 28 screens cheap")


# ----- 3. hot cap & demotion ---------------------------------------------------

def test_enforce_hot_cap_demotes_lowest_score():
    p = _tmpdb()
    scope = "chat:9"
    ids = []
    for i in range(8):
        ids.append(memory.remember(f"hot note {i}", scope=scope, tier="hot",
                                   importance=1.0 + i, db_path=p))
    demoted = memory.enforce_hot_cap(scope, max_hot=3, db_path=p)
    assert demoted == 5
    hot = memory.list_notes(scope, tier="hot", db_path=p)
    assert len(hot) == 3
    # the highest-importance notes survive as hot
    assert {n["value"] for n in hot} == {"hot note 5", "hot note 6", "hot note 7"}


def test_hot_cap_never_demotes_user_notes():
    p = _tmpdb()
    scope = "chat:10"
    for i in range(4):
        memory.remember(f"user pin {i}", scope=scope, tier="hot", source="user",
                        db_path=p)
    for i in range(4):
        memory.remember(f"agent hot {i}", scope=scope, tier="hot", db_path=p)
    memory.enforce_hot_cap(scope, max_hot=2, db_path=p)
    hot = memory.list_notes(scope, tier="hot", db_path=p)
    user_hot = [n for n in hot if n["source"] == "user"]
    agent_hot = [n for n in hot if n["source"] != "user"]
    assert len(user_hot) == 4          # untouched, outside the cap
    assert len(agent_hot) == 2


def test_promote_hot_enforces_cap():
    p = _tmpdb()
    scope = "committee:quant"
    for i in range(40):
        nid = memory.remember(f"useful pattern {i}", scope=scope, db_path=p)
        memory.bump(nid, by=5, db_path=p)          # all eligible for promotion
    promoted = memory.promote_hot(scope, db_path=p)
    assert promoted == 40
    hot = memory.list_notes(scope, tier="hot", limit=100, db_path=p)
    assert len(hot) <= memory.MAX_HOT_PER_SCOPE    # cap applied right after


# ----- 4. eviction protections --------------------------------------------------

def test_evict_protects_monthly_rollup():
    p = _tmpdb()
    scope = "chat:11"
    rid = memory.remember("May was about derisking into earnings",
                          key="monthly_rollup:2026-05", scope=scope, tier="warm",
                          source="auto", db_path=p)
    for i in range(10):
        memory.remember(f"filler note {i}", scope=scope, db_path=p)
    memory.evict(scope, max_notes=5, db_path=p)
    assert memory.get_note(rid, db_path=p) is not None


def test_evict_drops_expired_first():
    p = _tmpdb()
    scope = "chat:12"
    dead = memory.remember("expired filler", scope=scope, ttl_days=1, db_path=p)
    _expire_note(p, dead)
    keep = [memory.remember(f"live note {i}", scope=scope, importance=5.0, db_path=p)
            for i in range(5)]
    memory.evict(scope, max_notes=5, db_path=p)
    assert memory.get_note(dead, db_path=p) is None
    assert all(memory.get_note(k, db_path=p) for k in keep)


# ----- 5. COLD-store retention ---------------------------------------------------

def _age_turn(p: Path, turn_id: int, days: int) -> None:
    conn = db.connect(p)
    with conn:
        conn.execute("UPDATE conv_turns SET ts=datetime('now','localtime',?) WHERE id=?",
                     (f"-{days} days", turn_id))
    conn.close()


def _insert_turn(p: Path, content: str) -> int:
    conn = db.connect(p)
    with conn:
        cur = conn.execute(
            "INSERT INTO conv_turns (chat_id,session_id,role,content) VALUES (1,'s','user',?)",
            (content,))
        tid = cur.lastrowid
    conn.close()
    recall.index_turn(tid, content, p)
    return tid


def test_prune_turns_by_age_and_rowcount():
    p = _tmpdb()
    old = _insert_turn(p, "ancient question")
    _age_turn(p, old, 400)
    fresh = [_insert_turn(p, f"recent turn {i}") for i in range(6)]
    # age pass
    removed = memory.prune_turns(retain_days=365, max_rows=0, db_path=p)
    assert removed == 1
    conn = db.connect(p)
    assert conn.execute("SELECT 1 FROM conv_turns WHERE id=?", (old,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM turn_vec WHERE turn_id=?", (old,)).fetchone() is None
    conn.close()
    # row-cap pass: keep newest 4
    removed = memory.prune_turns(retain_days=0, max_rows=4, db_path=p)
    assert removed == 2
    conn = db.connect(p)
    left = [r["id"] for r in conn.execute("SELECT id FROM conv_turns ORDER BY id").fetchall()]
    conn.close()
    assert left == fresh[2:]


def test_prune_turns_disabled_by_zero():
    p = _tmpdb()
    _insert_turn(p, "any turn")
    assert memory.prune_turns(retain_days=0, max_rows=0, db_path=p) == 0


# ----- 5b. episodic recency decay (turns/digests) -------------------------------

def test_recency_decay_demotes_stale_turn_below_fresh():
    """A stale episodic turn ("6/16 FOMC binding") must rank below a fresh turn of
    equal relevance — turns/digests carry no TTL, so age-decay is what keeps past
    catalysts from surfacing at full weight next to current context."""
    p = _tmpdb()
    stale = _insert_turn(p, "FOMC eve trim still binding for the 6/16 decision")
    _age_turn(p, stale, 30)                       # ~3 half-lives → ~0.125x
    fresh = _insert_turn(p, "FOMC eve trim rule applies before the decision")
    hits = recall.search("FOMC eve trim binding", k=5, scope="chat:1",
                         kinds=("turn",), db_path=p)
    ids = [h["id"] for h in hits]
    assert stale in ids and fresh in ids
    assert ids.index(fresh) < ids.index(stale), "stale turn outranked the fresh one"


def test_recency_decay_no_penalty_for_today():
    """A turn from now keeps essentially its full RRF score (decay ~1.0)."""
    p = _tmpdb()
    tid = _insert_turn(p, "fresh thesis on datacenter demand")
    hits = recall.search("datacenter demand thesis", k=3, scope="chat:1",
                         kinds=("turn",), db_path=p)
    assert any(h["id"] == tid for h in hits)
    assert recall._recency_decay(None) == 1.0     # missing ts → no penalty


# ----- 6. vector-index self-healing ----------------------------------------------

def test_remember_survives_embedding_failure(monkeypatch):
    p = _tmpdb()
    def boom(*a, **k):
        raise RuntimeError("embedder offline")
    monkeypatch.setattr(recall, "index_note", boom)
    nid = memory.remember("saved during an embedding outage", db_path=p)
    assert memory.get_note(nid, db_path=p) is not None          # save committed
    monkeypatch.undo()
    fixed = recall.reindex_missing(db_path=p)
    assert fixed[0] == 1                                        # note repaired
    hits = recall.search("embedding outage", k=3, scope="global",
                         kinds=("note",), db_path=p)
    assert any(h["id"] == nid for h in hits)


# ----- 7. maintain() --------------------------------------------------------------

def test_maintain_runs_once_per_day():
    p = _tmpdb()
    nid = memory.remember("short-lived", ttl_days=1, db_path=p)
    _expire_note(p, nid)
    first = memory.maintain(db_path=p)
    assert first["ran"] and first["purged"] == 1
    second = memory.maintain(db_path=p)
    assert second == {"ran": False}                 # daily guard
    third = memory.maintain(db_path=p, force=True)
    assert third["ran"]


def test_maintain_demotes_over_cap_hot():
    p = _tmpdb()
    scope = "chat:13"
    for i in range(memory.MAX_HOT_PER_SCOPE + 5):
        memory.remember(f"hot {i}", scope=scope, tier="hot", db_path=p)
    out = memory.maintain(db_path=p, force=True)
    assert out["demoted"] == 5
    assert len(memory.list_notes(scope, tier="hot", limit=100, db_path=p)) \
        == memory.MAX_HOT_PER_SCOPE


# ----- 8. connect() init cache ------------------------------------------------------

def test_connect_initializes_once_and_recovers_from_deletion():
    p = _tmpdb()
    key = str(Path(p).resolve())
    assert key in db._INITIALIZED
    # cached connect still works
    conn = db.connect(p)
    assert conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"] == 0
    conn.close()
    # deleting the file forces re-initialization instead of failing
    Path(p).unlink()
    conn = db.connect(p)
    assert conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"] == 0
    conn.close()
    assert key in db._INITIALIZED


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
