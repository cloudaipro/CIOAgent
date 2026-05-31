"""MemCore verification suite — LLM-free, runs offline.

Proves the memory/context overhaul meets its goals (>= Hermes & OpenClaw):
schema+vectors, figures firewall, scope isolation, injected context budget,
hybrid (semantic) recall beating keyword-only, bounded growth (eviction +
rolling sessions), playbooks, and offline embedding from the local cache.

Run:  PYTHONPATH=. .venv/bin/python tests/test_memcore.py   (or: pytest -q)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

# Force offline: the cached fastembed model must work with no network.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from cfo import context, db, memory, recall  # noqa: E402


def _tmpdb() -> Path:
    p = Path(tempfile.mkdtemp()) / "t.db"
    db.init(p)
    return p


def test_schema_and_vectors():
    p = _tmpdb()
    conn = db.connect(p)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {"mem_notes", "user_profile", "session_digests", "conv_turns", "playbooks",
            "notes_fts", "turns_fts", "mem_vec", "turn_vec"} <= names
    conn.close()


def test_figures_firewall():
    p = _tmpdb()
    for bad in ["AAPL is worth 230", "set aside $5000", "P&L up 12%", "NVDA trading at 880"]:
        try:
            memory.remember(bad, db_path=p)
            raise AssertionError(f"figure not rejected: {bad}")
        except memory.FiguresFirewallError:
            pass
    for ok in ["prefers concise replies", "watching NVDA earnings 2026-06-01"]:
        memory.remember(ok, db_path=p)  # must not raise


def test_scope_isolation_and_profile():
    p = _tmpdb()
    memory.remember("chat-1 note", key="k", scope="chat:1", db_path=p)
    assert memory.recall("k", scope="chat:1", db_path=p) is not None
    assert memory.recall("k", scope="global", db_path=p) is None
    memory.set_profile("global", role="solo CFO", goals="bound risk", db_path=p)
    assert memory.get_profile("global", db_path=p)["role"] == "solo CFO"


def test_context_injection_budget():
    p = _tmpdb()
    memory.set_profile("global", role="solo CFO", db_path=p)
    for i in range(300):
        memory.remember(f"hot note {i} with several words for tokens", scope="global",
                        tier="hot", db_path=p)
    block = context.build_memory_block(chat_id=1, budget=400, db_path=p)
    toks = context.count_tokens(block)
    assert toks <= 400, toks            # hard bound — never exceeds the budget
    assert "Persistent memory" in block


def test_hybrid_recall_beats_keyword():
    p = _tmpdb()
    target = memory.remember("user prefers trimming oversized positions to cut portfolio risk",
                             scope="global", db_path=p)
    for d in ["likes charts on Mondays", "watching the earnings calendar", "dividend timing"]:
        memory.remember(d, scope="global", db_path=p)
    query = "reduce concentration in one holding"   # ~no shared keywords
    conn = db.connect(p)
    fts = [r["id"] for r in conn.execute(
        "SELECT m.id FROM notes_fts f JOIN mem_notes m ON m.id=f.rowid WHERE notes_fts MATCH ?",
        (recall._fts_query(query),)).fetchall()]
    conn.close()
    assert target not in fts                       # keyword alone misses
    hits = recall.search(query, k=3, scope="global", kinds=("note",), db_path=p)
    assert target in [h["id"] for h in hits]       # hybrid finds it (via vector)


def test_eviction_bounds_and_protects():
    p = _tmpdb()
    old = memory.MAX_NOTES_PER_SCOPE
    memory.MAX_NOTES_PER_SCOPE = 10
    try:
        memory.remember("PINNED", key="g", scope="s", tier="hot", importance=5, db_path=p)
        memory.remember("user said X", scope="s", source="user", db_path=p)
        for i in range(40):
            memory.remember(f"auto {i}", scope="s", source="auto", db_path=p)
        assert memory.count_notes("s", db_path=p) <= 10
        notes = memory.list_notes("s", limit=99, db_path=p)
        assert any(n["tier"] == "hot" for n in notes) and any(n["source"] == "user" for n in notes)
        vec = db.connect(p).execute("SELECT COUNT(*) c FROM mem_vec").fetchone()["c"]
        assert vec == memory.count_notes("s", db_path=p)   # vectors stay in sync
    finally:
        memory.MAX_NOTES_PER_SCOPE = old


def test_playbooks():
    p = _tmpdb()
    memory.add_playbook("monthly", "1. portfolio_summary\n2. list_positions", db_path=p)
    assert memory.list_playbooks("global", db_path=p)[0]["name"] == "monthly"
    try:
        memory.add_playbook("bad", "sell if worth $50000", db_path=p)
        raise AssertionError("figure in steps not rejected")
    except memory.FiguresFirewallError:
        pass


def test_rolling_session_cadence():
    """Drive ask() with stubs (no LLM): a checkpoint must fire every ROLL_TURNS."""
    import cfo.agent as agent
    old_roll, old_nudge = agent.ROLL_TURNS, agent.NUDGE_TURNS
    agent.ROLL_TURNS, agent.NUDGE_TURNS = 10, 0
    recorded = []
    orig_digest = agent.memory.add_digest
    agent.memory.add_digest = lambda *a, **k: recorded.append(1)

    class Dummy:
        async def disconnect(self): pass

    async def run():
        a = agent.CFOAgent(chat_id=42)
        async def fake_run(_): return ("ok", [])
        async def fake_ensure(): return None
        a._run_query = fake_run
        a._ensure = fake_ensure
        a._make_client = lambda resume: Dummy()
        a._client = Dummy()
        for _ in range(100):
            await a.ask("hi")
        return a._turns

    try:
        turns = asyncio.run(run())
        assert len(recorded) == 10, len(recorded)     # 100 turns / ROLL_TURNS(10)
        assert turns < agent.ROLL_TURNS                # counters reset after roll
    finally:
        agent.memory.add_digest = orig_digest
        agent.ROLL_TURNS, agent.NUDGE_TURNS = old_roll, old_nudge


def test_cold_boot_continuity():
    """After a 'reboot' (fresh process), durable memory is injected at session
    start AND old conversation is still hybrid-searchable."""
    p = _tmpdb()
    # --- previous run ---
    memory.set_profile("global", role="solo CFO", db_path=p)
    memory.remember("PINNED: reduce single-name risk", scope="chat:1", tier="hot",
                    importance=3.0, db_path=p)
    memory.add_digest(1, "s", "Earlier we reviewed allocation and trimmed exposure.", db_path=p)
    conn = db.connect(p)
    with conn:
        cur = conn.execute("INSERT INTO conv_turns(chat_id,session_id,role,content) VALUES(1,'s','user',?)",
                           ("I want to hedge my tech bets before the Fed meeting",))
    tid = cur.lastrowid
    conn.close()
    recall.index_turn(tid, "I want to hedge my tech bets before the Fed meeting", p)
    # --- cold boot: what a fresh CFOAgent assembles before the first message ---
    block = context.build_memory_block(chat_id=1, db_path=p)
    assert "reduce single-name risk" in block      # hot note injected
    assert "solo CFO" in block                      # profile injected
    assert "Earlier we reviewed" in block           # last digest injected
    hits = recall.search("protect against the interest-rate decision downside",
                          k=3, scope="chat:1", kinds=("turn",), db_path=p)
    assert tid in [h["id"] for h in hits]           # old turn still findable


def test_promote_hot():
    p = _tmpdb()
    nid = memory.remember("a frequently useful note", scope="s", tier="warm", db_path=p)
    memory.bump(nid, by=3, db_path=p)
    promoted = memory.promote_hot("s", hits_threshold=3, db_path=p)
    assert promoted == 1
    assert memory.get_note(nid, db_path=p)["tier"] == "hot"


def test_playbook_parser():
    from cfo.agent import _parse_playbook
    assert _parse_playbook("NONE") is None
    assert _parse_playbook("nothing reusable") is None
    name, steps = _parse_playbook("NAME: monthly_review\nSTEPS: 1. portfolio_summary\n2. list_positions")
    assert name == "monthly_review" and "portfolio_summary" in steps


def test_reflection_loop():
    """_checkpoint must auto-promote hot notes and auto-distill a playbook (stubbed)."""
    import cfo.agent as agent
    rec = {"digest": 0, "playbook": None, "promote": 0}
    orig = (agent.memory.add_digest, agent.memory.add_playbook, agent.memory.promote_hot)
    agent.memory.add_digest = lambda *a, **k: rec.__setitem__("digest", rec["digest"] + 1)
    agent.memory.add_playbook = lambda name, steps, scope: rec.__setitem__("playbook", name)
    agent.memory.promote_hot = lambda scope: (rec.__setitem__("promote", 2) or 2)

    class Dummy:
        async def disconnect(self): pass

    async def run():
        a = agent.CFOAgent(chat_id=99)
        async def fake_run(p):
            if "Reflect on this session" in p:
                return ("NAME: monthly_review\nSTEPS: 1. portfolio_summary\n2. list_positions", [])
            return ("a digest of the session", [])
        a._run_query = fake_run
        a._ensure = lambda: asyncio.sleep(0)
        a._make_client = lambda resume: Dummy()
        a._client = Dummy()
        await a._checkpoint()

    try:
        asyncio.run(run())
        assert rec["digest"] == 1, rec
        assert rec["promote"] == 2, rec
        assert rec["playbook"] == "monthly_review", rec
    finally:
        agent.memory.add_digest, agent.memory.add_playbook, agent.memory.promote_hot = orig


def test_offline_embedding():
    # env forced offline at import; embedding must still work from the local cache.
    assert recall.warmup() == db.EMBED_DIM


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL {len(tests)} MEMCORE TESTS PASSED (offline)")


if __name__ == "__main__":
    main()
