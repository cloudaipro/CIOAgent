"""Hybrid recall: FTS5 (keyword/BM25) + sqlite-vec (semantic) merged with RRF.

This is the layer that beats keyword-only memory: a query phrased differently
from how a fact was stored still surfaces it via the vector side, while exact
terms still hit via FTS. Both are local and offline:

- embeddings: fastembed `BAAI/bge-base-en-v1.5` (ONNX, 768-dim), model cached in
  `data/models/` so the agent is offline-stable after first download;
- storage/ANN: sqlite-vec `vec0` tables (`mem_vec`, `turn_vec`, `digest_vec`) in the same DB.

Both are required (no FTS-only mode). Results from the keyword and vector rankers
are fused with Reciprocal Rank Fusion (RRF), the same technique Hermes/Milvus use.
"""
from __future__ import annotations

import re

import sqlite_vec

from . import db
from .db import DB_PATH, EMBED_DIM

MODEL_NAME = "BAAI/bge-base-en-v1.5"   # 768-dim, full precision (higher recall fidelity)
_CACHE_DIR = str((db.Path(__file__).resolve().parent.parent / "data" / "models"))
_RRF_K = 60

_model = None


def _embedder():
    """Lazy-load the fastembed model from the local cache (offline after first run)."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=_CACHE_DIR)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    return [list(map(float, v)) for v in _embedder().embed(list(texts))]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def warmup() -> int:
    """Download (first run) and load the embedding model; returns its dimension.
    Run once after install so the agent is offline-stable:
        python -c "from cio import recall; print(recall.warmup())"
    """
    return len(embed_one("warmup"))


def _ser(vec: list[float]) -> bytes:
    assert len(vec) == EMBED_DIM, (len(vec), EMBED_DIM)
    return sqlite_vec.serialize_float32(vec)


# ----- indexing (called from the write path) --------------------------------

def index_note(note_id: int, text: str, db_path=DB_PATH) -> None:
    blob = _ser(embed_one(text))
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM mem_vec WHERE note_id=?", (note_id,))
        conn.execute("INSERT INTO mem_vec(note_id, embedding) VALUES(?,?)", (note_id, blob))
    conn.close()


def deindex_note(note_id: int, db_path=DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM mem_vec WHERE note_id=?", (note_id,))
    conn.close()


def index_turn(turn_id: int, text: str, db_path=DB_PATH) -> None:
    blob = _ser(embed_one(text))
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM turn_vec WHERE turn_id=?", (turn_id,))
        conn.execute("INSERT INTO turn_vec(turn_id, embedding) VALUES(?,?)", (turn_id, blob))
    conn.close()


def index_digest(digest_id: int, text: str, db_path=DB_PATH) -> None:
    blob = _ser(embed_one(text))
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM digest_vec WHERE digest_id=?", (digest_id,))
        conn.execute("INSERT INTO digest_vec(digest_id, embedding) VALUES(?,?)", (digest_id, blob))
    conn.close()


def reindex_all(db_path=DB_PATH) -> tuple[int, int]:
    """Re-embed every note and turn into the vec tables. Run after an embedding
    dim/model change (db.connect flags `vec_reindex_needed`). Returns (notes, turns)."""
    conn = db.connect(db_path)
    notes = conn.execute("SELECT id, value FROM mem_notes").fetchall()
    turns = conn.execute("SELECT id, content FROM conv_turns").fetchall()
    digests = conn.execute("SELECT id, summary FROM session_digests").fetchall()
    conn.close()
    for r in notes:
        index_note(r["id"], r["value"], db_path)
    for r in turns:
        index_turn(r["id"], r["content"], db_path)
    for r in digests:
        index_digest(r["id"], r["summary"], db_path)
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM meta WHERE key='vec_reindex_needed'")
    conn.close()
    return len(notes), len(turns)


def reindex_missing(db_path=DB_PATH, limit: int = 500) -> tuple[int, int, int]:
    """Embed rows that have no vector (a write-path embedding hiccup leaves a note
    semantically invisible). Repairs up to *limit* rows per kind per run — called
    from memory.maintain() so drift self-heals. Returns (notes, turns, digests)."""
    conn = db.connect(db_path)
    notes = conn.execute(
        "SELECT id, value FROM mem_notes WHERE id NOT IN (SELECT note_id FROM mem_vec) "
        "LIMIT ?", (limit,)).fetchall()
    turns = conn.execute(
        "SELECT id, content FROM conv_turns WHERE id NOT IN (SELECT turn_id FROM turn_vec) "
        "LIMIT ?", (limit,)).fetchall()
    digests = conn.execute(
        "SELECT id, summary FROM session_digests "
        "WHERE id NOT IN (SELECT digest_id FROM digest_vec) LIMIT ?", (limit,)).fetchall()
    conn.close()
    for r in notes:
        index_note(r["id"], r["value"], db_path)
    for r in turns:
        index_turn(r["id"], r["content"], db_path)
    for r in digests:
        index_digest(r["id"], r["summary"], db_path)
    return len(notes), len(turns), len(digests)


# ----- scope pre-filtering ---------------------------------------------------
# Ranking across ALL scopes and filtering afterwards starves recall as the DB
# fills: a small fixed KNN/FTS pool gets dominated by other scopes' rows and the
# target scope surfaces nothing (and semantic dedup stops collapsing twins, which
# compounds the crowding). Both rankers therefore restrict candidates to the
# allowed scopes IN SQL — sqlite-vec 0.1.9 supports `note_id IN (subquery)`
# KNN pre-filtering, and the FTS join takes a plain WHERE.

_NOT_EXPIRED = "(expires_at IS NULL OR expires_at > datetime('now','localtime'))"


def _note_scope_filter(scope: str | None, include_global: bool) -> tuple[str, list]:
    """SQL predicate (against mem_notes columns) + params for the allowed scopes,
    always excluding expired notes."""
    if scope and include_global:
        return f"scope IN (?, 'global') AND {_NOT_EXPIRED}", [scope]
    if scope:
        return f"scope = ? AND {_NOT_EXPIRED}", [scope]
    return _NOT_EXPIRED, []


# ----- dedup ----------------------------------------------------------------

def nearest_in_scope(text: str, scope: str, db_path=DB_PATH,
                     pool: int = 20) -> dict | None:
    """Closest existing note to *text* within *scope*, by embedding distance.

    Returns {'id', 'text', 'distance'} for the nearest note in the SAME scope, or
    None if the scope has no indexed notes. `distance` is the sqlite-vec L2 metric
    (smaller = more similar); for the normalized bge vectors, distance² ≈ 2(1−cos).
    Used by the write path to collapse semantic duplicates before insert.
    """
    qvec = _ser(embed_one(text))
    conn = db.connect(db_path)
    # KNN is pre-filtered to THIS scope in SQL, so a crowded DB (many scopes) can
    # never push the true in-scope neighbour out of the candidate pool.
    knn = conn.execute(
        "SELECT note_id, distance FROM mem_vec WHERE embedding MATCH ? "
        f"AND note_id IN (SELECT id FROM mem_notes WHERE scope = ? AND {_NOT_EXPIRED}) "
        "ORDER BY distance LIMIT ?",
        (qvec, scope, pool),
    ).fetchall()
    best = None
    for r in knn:
        row = conn.execute(
            "SELECT value FROM mem_notes WHERE id = ? AND scope = ?",
            (r["note_id"], scope),
        ).fetchone()
        if row:
            best = {"id": r["note_id"], "text": row["value"], "distance": r["distance"]}
            break  # knn is distance-sorted, so the first in-scope hit is the closest
    conn.close()
    return best


# ----- search ---------------------------------------------------------------

def _fts_query(text: str) -> str | None:
    """Build a safe FTS5 MATCH expression: OR of quoted alnum tokens."""
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", text.lower()) if len(t) >= 3]
    return " OR ".join(f'"{t}"' for t in toks) if toks else None


def _rrf(ranked_ids: list[list[int]]) -> dict[int, float]:
    """Reciprocal Rank Fusion over several ranked id-lists (best first)."""
    score: dict[int, float] = {}
    for ids in ranked_ids:
        for rank, _id in enumerate(ids):
            score[_id] = score.get(_id, 0.0) + 1.0 / (_RRF_K + rank)
    return score


def _scope_chat_id(scope: str | None):
    if scope and scope.startswith("chat:"):
        try:
            return int(scope.split(":", 1)[1])
        except ValueError:
            return None
    return None


def search(query: str, k: int = 5, scope: str | None = None,
           kinds: tuple[str, ...] = ("note", "turn"), db_path=DB_PATH, *,
           include_global: bool = True) -> list[dict]:
    """Hybrid search across notes, conversation turns and/or session digests; returns
    top-k hits [{kind, id, text, score}], best first. Notes are limited to `scope` +
    global (when include_global=True, the default); pass include_global=False to
    restrict hits strictly to `scope` only (used by per-agent committee recall for
    isolation). Turns and digests are limited to the chat of `scope` (if any). Pass
    `kinds=("note","turn","digest")` to include long-term digest summaries."""
    match = _fts_query(query)
    qvec = _ser(embed_one(query))
    pool = max(k * 4, 20)
    conn = db.connect(db_path)
    results: list[dict] = []

    if "note" in kinds:
        scope_sql, scope_args = _note_scope_filter(scope, include_global)
        fts_ids = []
        if match:
            fts_ids = [r["id"] for r in conn.execute(
                "SELECT m.id FROM notes_fts f JOIN mem_notes m ON m.id=f.rowid "
                f"WHERE notes_fts MATCH ? AND {scope_sql} "
                "ORDER BY bm25(notes_fts) LIMIT ?",
                (match, *scope_args, pool)).fetchall()]
        vec_ids = [r["note_id"] for r in conn.execute(
            "SELECT note_id FROM mem_vec WHERE embedding MATCH ? "
            f"AND note_id IN (SELECT id FROM mem_notes WHERE {scope_sql}) "
            "ORDER BY distance LIMIT ?",
            (qvec, *scope_args, pool)).fetchall()]
        scores = _rrf([fts_ids, vec_ids])
        if scores:
            rows = {r["id"]: r for r in conn.execute(
                f"SELECT id, value, scope FROM mem_notes WHERE id IN "
                f"({','.join('?'*len(scores))})", tuple(scores)).fetchall()}
            for _id, sc in scores.items():
                r = rows.get(_id)
                if not r:
                    continue
                if scope:
                    if include_global:
                        if r["scope"] not in (scope, "global"):
                            continue
                    else:
                        if r["scope"] != scope:
                            continue
                results.append({"kind": "note", "id": _id, "text": r["value"], "score": sc})

    if "turn" in kinds:
        cid = _scope_chat_id(scope)
        # Same pre-filter idea as notes: restrict candidates to this chat (or
        # chat-less rows) in SQL so other chats can't crowd the pool.
        chat_sql = "(chat_id IS NULL OR chat_id = ?)" if cid is not None else "1=1"
        chat_args = [cid] if cid is not None else []
        fts_ids = []
        if match:
            fts_ids = [r["id"] for r in conn.execute(
                "SELECT c.id FROM turns_fts f JOIN conv_turns c ON c.id=f.rowid "
                f"WHERE turns_fts MATCH ? AND {chat_sql.replace('chat_id', 'c.chat_id')} "
                "ORDER BY bm25(turns_fts) LIMIT ?",
                (match, *chat_args, pool)).fetchall()]
        vec_ids = [r["turn_id"] for r in conn.execute(
            "SELECT turn_id FROM turn_vec WHERE embedding MATCH ? "
            f"AND turn_id IN (SELECT id FROM conv_turns WHERE {chat_sql}) "
            "ORDER BY distance LIMIT ?",
            (qvec, *chat_args, pool)).fetchall()]
        scores = _rrf([fts_ids, vec_ids])
        if scores:
            rows = {r["id"]: r for r in conn.execute(
                f"SELECT id, role, content, chat_id FROM conv_turns WHERE id IN "
                f"({','.join('?'*len(scores))})", tuple(scores)).fetchall()}
            for _id, sc in scores.items():
                r = rows.get(_id)
                if not r:
                    continue
                if cid is not None and r["chat_id"] is not None and r["chat_id"] != cid:
                    continue
                results.append({"kind": "turn", "id": _id,
                                "text": f"[{r['role']}] {r['content']}", "score": sc})

    if "digest" in kinds:
        cid = _scope_chat_id(scope)
        chat_sql = "(chat_id IS NULL OR chat_id = ?)" if cid is not None else "1=1"
        chat_args = [cid] if cid is not None else []
        fts_ids = []
        if match:
            fts_ids = [r["id"] for r in conn.execute(
                "SELECT d.id FROM digests_fts f JOIN session_digests d ON d.id=f.rowid "
                f"WHERE digests_fts MATCH ? AND {chat_sql.replace('chat_id', 'd.chat_id')} "
                "ORDER BY bm25(digests_fts) LIMIT ?",
                (match, *chat_args, pool)).fetchall()]
        vec_ids = [r["digest_id"] for r in conn.execute(
            "SELECT digest_id FROM digest_vec WHERE embedding MATCH ? "
            f"AND digest_id IN (SELECT id FROM session_digests WHERE {chat_sql}) "
            "ORDER BY distance LIMIT ?",
            (qvec, *chat_args, pool)).fetchall()]
        scores = _rrf([fts_ids, vec_ids])
        if scores:
            rows = {r["id"]: r for r in conn.execute(
                f"SELECT id, summary, chat_id, created_at FROM session_digests WHERE id IN "
                f"({','.join('?'*len(scores))})", tuple(scores)).fetchall()}
            for _id, sc in scores.items():
                r = rows.get(_id)
                if not r:
                    continue
                if cid is not None and r["chat_id"] is not None and r["chat_id"] != cid:
                    continue
                day = (r["created_at"] or "")[:10]
                results.append({"kind": "digest", "id": _id,
                                "text": f"[digest {day}] {r['summary']}", "score": sc})

    conn.close()
    results.sort(key=lambda h: h["score"], reverse=True)
    return results[:k]
