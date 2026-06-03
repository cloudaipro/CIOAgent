"""Hybrid recall: FTS5 (keyword/BM25) + sqlite-vec (semantic) merged with RRF.

This is the layer that beats keyword-only memory: a query phrased differently
from how a fact was stored still surfaces it via the vector side, while exact
terms still hit via FTS. Both are local and offline:

- embeddings: fastembed `BAAI/bge-small-en-v1.5` (ONNX, 384-dim), model cached in
  `data/models/` so the agent is offline-stable after first download;
- storage/ANN: sqlite-vec `vec0` tables (`mem_vec`, `turn_vec`) in the same DB.

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


def reindex_all(db_path=DB_PATH) -> tuple[int, int]:
    """Re-embed every note and turn into the vec tables. Run after an embedding
    dim/model change (db.connect flags `vec_reindex_needed`). Returns (notes, turns)."""
    conn = db.connect(db_path)
    notes = conn.execute("SELECT id, value FROM mem_notes").fetchall()
    turns = conn.execute("SELECT id, content FROM conv_turns").fetchall()
    conn.close()
    for r in notes:
        index_note(r["id"], r["value"], db_path)
    for r in turns:
        index_turn(r["id"], r["content"], db_path)
    conn = db.connect(db_path)
    with conn:
        conn.execute("DELETE FROM meta WHERE key='vec_reindex_needed'")
    conn.close()
    return len(notes), len(turns)


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
    # KNN runs on the vec table alone (proven pattern, same as search()); we then
    # filter to this scope and take the closest in-scope hit. An out-of-scope note
    # must never collapse ours, so scope filtering happens after the ANN lookup.
    knn = conn.execute(
        "SELECT note_id, distance FROM mem_vec WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT ?",
        (qvec, pool),
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
    """Hybrid search across notes and/or conversation turns; returns top-k hits
    [{kind, id, text, score}], best first. Notes are limited to `scope` + global
    (when include_global=True, the default); pass include_global=False to restrict
    hits strictly to `scope` only (used by per-agent committee recall for isolation).
    Turns are always limited to the chat of `scope` (if any)."""
    match = _fts_query(query)
    qvec = _ser(embed_one(query))
    pool = max(k * 4, 20)
    conn = db.connect(db_path)
    results: list[dict] = []

    if "note" in kinds:
        fts_ids = []
        if match:
            fts_ids = [r["id"] for r in conn.execute(
                "SELECT m.id FROM notes_fts f JOIN mem_notes m ON m.id=f.rowid "
                "WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
                (match, pool)).fetchall()]
        vec_ids = [r["note_id"] for r in conn.execute(
            "SELECT note_id FROM mem_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (qvec, pool)).fetchall()]
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
        fts_ids = []
        if match:
            fts_ids = [r["id"] for r in conn.execute(
                "SELECT c.id FROM turns_fts f JOIN conv_turns c ON c.id=f.rowid "
                "WHERE turns_fts MATCH ? ORDER BY bm25(turns_fts) LIMIT ?",
                (match, pool)).fetchall()]
        vec_ids = [r["turn_id"] for r in conn.execute(
            "SELECT turn_id FROM turn_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (qvec, pool)).fetchall()]
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

    conn.close()
    results.sort(key=lambda h: h["score"], reverse=True)
    return results[:k]
