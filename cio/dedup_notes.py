"""One-shot maintenance: collapse duplicate mem_notes rows in place.

Two passes, both scope-isolated (a note in one scope never merges with another):

  EXACT   — rows sharing identical (scope, value) text. Offline, no model needed.
            This is what the keyless-insert bug produced (see agent_memory.save_note).
  SEMANTIC — rows that are paraphrases: embedding distance <= --max-dist within a
            scope. Requires the fastembed model (same one recall.py uses); opt in
            with --semantic. Skipped by default so the tool runs fully offline.

For each duplicate cluster one survivor is kept (HOT beats warm, then most hits,
then lowest id). Survivor absorbs SUM(hits) and MAX(importance); the rest are
deleted along with their mem_vec rows. The survivor is given a deterministic key
so a future identical save upserts instead of re-duplicating.

DRY-RUN by default — prints the plan and changes nothing. Pass --apply to write.
Back up the db first (it's a single file): cp data/committee.db data/committee.db.bak

Usage:
    python -m cio.dedup_notes                          # dry-run committee.db, exact only
    python -m cio.dedup_notes --apply                  # apply exact merge
    python -m cio.dedup_notes --semantic --apply       # also collapse paraphrases
    python -m cio.dedup_notes --db data/cfo.db --apply
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sqlite3
from pathlib import Path

from . import db


def _key_for(value: str) -> str:
    """Stable backfill key so future identical-text saves upsert onto the survivor."""
    return f"dedup:{hashlib.sha1(value.strip().lower().encode('utf-8')).hexdigest()[:12]}"


def _pick_survivor(rows: list[dict]) -> dict:
    """HOT beats warm, then most hits, then lowest id (oldest/most stable)."""
    return sorted(
        rows,
        key=lambda r: (0 if r["tier"] == "hot" else 1, -int(r["hits"]), int(r["id"])),
    )[0]


def _collapse(conn, cluster: list[dict], apply: bool) -> int:
    """Merge a cluster (>=2 rows, same scope) onto one survivor. Returns rows removed."""
    survivor = _pick_survivor(cluster)
    losers = [r for r in cluster if r["id"] != survivor["id"]]
    if not losers:
        return 0
    total_hits = sum(int(r["hits"]) for r in cluster)
    max_imp = max(float(r["importance"]) for r in cluster)
    tier = "hot" if any(r["tier"] == "hot" for r in cluster) else survivor["tier"]
    if apply:
        with conn:
            conn.execute(
                "UPDATE mem_notes SET hits=?, importance=?, tier=?, key=?, "
                "updated_at=datetime('now') WHERE id=?",
                (total_hits, max_imp, tier, _key_for(survivor["value"]), survivor["id"]),
            )
            for r in losers:
                conn.execute("DELETE FROM mem_vec WHERE note_id=?", (r["id"],))
                conn.execute("DELETE FROM mem_notes WHERE id=?", (r["id"],))
    return len(losers)


def _exact_clusters(conn) -> list[list[dict]]:
    """Groups of rows with identical (scope, value)."""
    rows = conn.execute(
        "SELECT id, scope, tier, value, importance, hits FROM mem_notes "
        "ORDER BY scope, value, id"
    ).fetchall()
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["scope"], r["value"]), []).append(dict(r))
    return [g for g in groups.values() if len(g) > 1]


def _semantic_clusters(conn, max_dist: float) -> list[list[dict]]:
    """Within each scope, union notes whose embedding L2 distance <= max_dist.

    Brute-force pairwise per scope (note counts are small). Embeds via recall.py's
    model — this is the only path that needs the model downloaded."""
    from . import recall
    rows = [dict(r) for r in conn.execute(
        "SELECT id, scope, tier, value, importance, hits FROM mem_notes ORDER BY scope, id"
    ).fetchall()]
    by_scope: dict[str, list[dict]] = {}
    for r in rows:
        by_scope.setdefault(r["scope"], []).append(r)

    clusters: list[list[dict]] = []
    for scope, notes in by_scope.items():
        if len(notes) < 2:
            continue
        vecs = recall.embed([n["value"] for n in notes])
        parent = list(range(len(notes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a in range(len(notes)):
            for b in range(a + 1, len(notes)):
                dist = math.dist(vecs[a], vecs[b])  # L2, same metric as sqlite-vec
                if dist <= max_dist:
                    parent[find(a)] = find(b)
        buckets: dict[int, list[dict]] = {}
        for i, n in enumerate(notes):
            buckets.setdefault(find(i), []).append(n)
        clusters.extend(c for c in buckets.values() if len(c) > 1)
    return clusters


def backfill_keys(db_path: Path, apply: bool) -> int:
    """Give every keyless note a stable deterministic key (same `dedup:<hash>`
    scheme as merge survivors), so a future identical-text save upserts instead of
    inserting a twin. Idempotent — already-keyed rows are skipped. DRY-RUN unless
    *apply*. Returns the number of rows keyed."""
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, scope, value FROM mem_notes WHERE key IS NULL OR key=''"
    ).fetchall()
    print(f"db={db_path}  keyless notes={len(rows)}  "
          f"{'APPLY' if apply else 'DRY-RUN'}")
    done = skip = 0
    for r in rows:
        k = _key_for(r["value"])
        if not apply:
            done += 1
            continue
        try:
            with conn:
                conn.execute("UPDATE mem_notes SET key=? WHERE id=?", (k, r["id"]))
            done += 1
        except sqlite3.IntegrityError:
            # a keyed row in this scope already holds this hash — leave NULL, skip
            skip += 1
    conn.close()
    verb = "keyed" if apply else "would key"
    msg = f"{verb} {done} note(s)"
    if skip:
        msg += f", skipped {skip} (key already present in scope)"
    print(msg)
    if not apply and done:
        print("Re-run with --apply to write. Back up the db file first.")
    return done


def run(db_path: Path, apply: bool, semantic: bool, max_dist: float) -> int:
    conn = db.connect(db_path)
    before = conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"]
    mode = "EXACT + SEMANTIC" if semantic else "EXACT"
    print(f"db={db_path}  notes={before}  mode={mode}  "
          f"{'APPLY' if apply else 'DRY-RUN'}")

    clusters = _exact_clusters(conn)
    if semantic:
        # Run semantic after exact would still see the same rows (we collapse a single
        # combined cluster set). Compute on the live table; exact dups are a subset of
        # semantic ones (distance 0), so the union-find naturally absorbs them.
        clusters = _semantic_clusters(conn, max_dist)

    removed = 0
    for c in clusters:
        survivor = _pick_survivor(c)
        print(f"  [{c[0]['scope']}] keep id={survivor['id']} "
              f"(tier={survivor['tier']}), merge {len(c) - 1} twin(s): "
              f"{survivor['value'][:55]!r}")
        removed += _collapse(conn, c, apply)

    if apply:
        with conn:
            conn.execute("VACUUM")
    after = conn.execute("SELECT COUNT(*) c FROM mem_notes").fetchone()["c"]
    conn.close()
    verb = "removed" if apply else "would remove"
    print(f"\n{verb} {removed} duplicate row(s). notes {before} -> "
          f"{after if apply else before - removed}.")
    if not apply and removed:
        print("Re-run with --apply to write. Back up the db file first.")
    return removed


def main() -> None:
    ap = argparse.ArgumentParser(description="Collapse duplicate mem_notes rows.")
    ap.add_argument("--db", default="data/committee.db", type=Path,
                    help="SQLite db to clean (default: data/committee.db)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is dry-run)")
    ap.add_argument("--semantic", action="store_true",
                    help="also collapse paraphrases (needs the embedding model)")
    ap.add_argument("--max-dist", type=float, default=0.45,
                    help="semantic L2 distance threshold (default 0.45 ~ cos 0.90)")
    ap.add_argument("--backfill-keys", action="store_true",
                    help="give every keyless note a stable dedup:<hash> key (no merging)")
    args = ap.parse_args()
    if args.backfill_keys:
        backfill_keys(args.db, apply=args.apply)
    else:
        run(args.db, apply=args.apply, semantic=args.semantic, max_dist=args.max_dist)


if __name__ == "__main__":
    main()
