"""Nightly SQLite backups — the recoverability leg of the distrust posture.

Maintenance (TTL purge, turn pruning, hot-cap demotion) deletes data by design;
the dedup CLI writes `.bak` files only when *it* runs. This module gives every
maintenance night a restore point first: a consistent snapshot of each DB taken
with SQLite's online backup API (`Connection.backup`), which is safe against a
live writer under WAL — a plain file copy is not.

Layout: `data/backups/<stem>.<YYYY-MM-DD>.db`, newest `CIO_BACKUP_KEEP`
(default 7) kept per DB. One snapshot per local day — the scheduler's boot
one-shot can re-fire harmlessly. `CIO_BACKUP=off` disables. Never raises:
a failed backup logs and returns None rather than blocking maintenance.

Restore = stop the bot, copy the snapshot over the live file, start the bot.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date
from pathlib import Path

from . import db

log = logging.getLogger("cio.backup")

KEEP = int(os.getenv("CIO_BACKUP_KEEP", "7"))
_DEFAULT_DIR = db.DB_PATH.parent / "backups"


def enabled() -> bool:
    return os.getenv("CIO_BACKUP", "on").lower() not in ("off", "0", "false")


def _prune(dest_dir: Path, stem: str, keep: int) -> int:
    """Drop the oldest snapshots beyond *keep* for one DB stem. The date is in
    the filename, so lexicographic sort is chronological."""
    snaps = sorted(dest_dir.glob(f"{stem}.????-??-??.db"))
    excess = snaps[:-keep] if keep > 0 else []
    for p in excess:
        try:
            p.unlink()
        except Exception:
            log.warning("could not prune old backup %s", p, exc_info=True)
    return len(excess)


def backup_db(src: Path | str, dest_dir: Path | str | None = None,
              keep: int = KEEP) -> Path | None:
    """Snapshot *src* into the backup dir; returns the snapshot path.

    Idempotent per local day (an existing today-file is returned untouched).
    Returns None when disabled, src is missing, or the backup fails."""
    if not enabled():
        return None
    src = Path(src)
    if not src.exists():
        return None
    dest_dir = Path(dest_dir or os.getenv("CIO_BACKUP_DIR") or _DEFAULT_DIR)
    target = dest_dir / f"{src.stem}.{date.today().isoformat()}.db"
    if target.exists():
        return target                        # today's restore point already taken
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        src_conn = sqlite3.connect(src)
        dst_conn = sqlite3.connect(target)
        with dst_conn:
            src_conn.backup(dst_conn)        # online, WAL-safe, consistent
        src_conn.close()
        dst_conn.close()
        _prune(dest_dir, src.stem, keep)
        log.info("backed up %s -> %s (%.1f MB)", src, target,
                 target.stat().st_size / 1e6)
        return target
    except Exception:
        log.warning("backup of %s failed", src, exc_info=True)
        try:                                 # never leave a half-written snapshot
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def backup_all() -> list[Path]:
    """Snapshot both memory DBs (conversational + committee). Best-effort."""
    from .committee import agent_memory
    out = []
    for src in (db.DB_PATH, agent_memory.DB_PATH):
        p = backup_db(src)
        if p:
            out.append(p)
    return out
