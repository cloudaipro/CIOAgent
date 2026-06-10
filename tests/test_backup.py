"""Tests for the nightly DB backup (cio/backup.py).

A backup that was never restored from is a hope, not a backup — so the central
test restores a snapshot and reads the data back out of it.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from cio import backup, db, memory  # noqa: E402


def _tmpdb(tmp_path) -> Path:
    p = tmp_path / "src.db"
    db.init(p)
    return p


def test_backup_is_restorable(tmp_path):
    src = _tmpdb(tmp_path)
    memory.remember("survives the snapshot", key="k1", db_path=src)
    dest = tmp_path / "backups"
    snap = backup.backup_db(src, dest_dir=dest)
    assert snap and snap.exists()
    assert snap.name == f"src.{date.today().isoformat()}.db"
    # restore = read the snapshot like a live DB
    assert memory.recall("k1", db_path=snap) == "survives the snapshot"


def test_backup_idempotent_per_day(tmp_path):
    src = _tmpdb(tmp_path)
    dest = tmp_path / "backups"
    first = backup.backup_db(src, dest_dir=dest)
    mtime = first.stat().st_mtime_ns
    second = backup.backup_db(src, dest_dir=dest)        # boot one-shot re-fires
    assert second == first
    assert second.stat().st_mtime_ns == mtime            # untouched, not rewritten
    assert len(list(dest.glob("src.*.db"))) == 1


def test_prune_keeps_newest(tmp_path):
    src = _tmpdb(tmp_path)
    dest = tmp_path / "backups"
    dest.mkdir()
    for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
        (dest / f"src.{d}.db").write_bytes(b"old")
    snap = backup.backup_db(src, dest_dir=dest, keep=2)
    left = sorted(p.name for p in dest.glob("src.*.db"))
    assert snap.name in left and len(left) == 2
    assert "src.2026-01-01.db" not in left and "src.2026-01-02.db" not in left


def test_missing_source_and_disabled(tmp_path, monkeypatch):
    assert backup.backup_db(tmp_path / "nope.db", dest_dir=tmp_path / "b") is None
    src = _tmpdb(tmp_path)
    monkeypatch.setenv("CIO_BACKUP", "off")
    assert backup.backup_db(src, dest_dir=tmp_path / "b") is None
    assert not (tmp_path / "b").exists()


def test_failure_leaves_no_partial_snapshot(tmp_path, monkeypatch):
    src = _tmpdb(tmp_path)
    dest = tmp_path / "backups"
    import sqlite3

    class _FailingConn:                # mimics connect() creating the file, then
        def __init__(self, p):        # the copy dying mid-way ("disk full")
            Path(p).touch()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def backup(self, other): raise sqlite3.OperationalError("disk full")
        def close(self): pass

    monkeypatch.setattr(backup.sqlite3, "connect", _FailingConn)
    assert backup.backup_db(src, dest_dir=dest) is None
    assert not list(dest.glob("src.*.db"))               # half-file cleaned up


def test_backup_all_covers_both_dbs(tmp_path, monkeypatch):
    a, b = _tmpdb(tmp_path), tmp_path / "comm.db"
    db.init(b)
    from cio.committee import agent_memory
    monkeypatch.setattr(db, "DB_PATH", a)
    monkeypatch.setattr(agent_memory, "DB_PATH", b)
    monkeypatch.setenv("CIO_BACKUP_DIR", str(tmp_path / "backups"))
    snaps = backup.backup_all()
    assert {p.stem.split(".")[0] for p in snaps} == {"src", "comm"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
