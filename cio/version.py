"""Boot version stamp — makes the *running* code version observable.

Motivation (2026-06-10 incident): the day-roll fix was committed at 21:56, all
tests were green, but the bot process serving overnight turns had started before
the commit — Python does not hot-reload, so production ran stale code for hours
and the misattribution bug stayed live. No test category can catch that; it is
an operations property. So the bot stamps the commit it booted from into `meta`,
the dashboard displays it, and the nightly invariant check compares it against
the repo's current HEAD — a mismatch means "restart needed" and is reported as a
violation instead of silently running old behavior.

All git access is best-effort: a deployment without git (or a copied tree)
degrades to "unknown" rather than failing boot.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

BOOT_VERSION_KEY = "boot_version"   # short commit hash the running process booted from
BOOT_TIME_KEY = "boot_time"         # local ISO timestamp of that boot
BOOT_PID_KEY = "boot_pid"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=_REPO, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def repo_commit() -> str | None:
    """Short HEAD hash of the source tree on disk right now (None if no git)."""
    return _git("rev-parse", "--short", "HEAD")


def repo_dirty() -> bool:
    """Whether the working tree differs from HEAD (uncommitted edits)."""
    status = _git("status", "--porcelain")
    return bool(status)


def describe() -> str:
    """Human-readable version of the source tree, e.g. '0c2e7d2+dirty'."""
    commit = repo_commit()
    if not commit:
        return "unknown"
    return commit + ("+dirty" if repo_dirty() else "")


def stamp_boot(db_path=None) -> dict:
    """Record the version this process booted from. Called once at bot startup.
    Never raises. Returns {'version', 'time', 'pid'}."""
    from . import db, memory
    info = {
        "version": describe(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": str(os.getpid()),
    }
    try:
        path = db_path or db.DB_PATH
        memory.set_meta(BOOT_VERSION_KEY, info["version"], db_path=path)
        memory.set_meta(BOOT_TIME_KEY, info["time"], db_path=path)
        memory.set_meta(BOOT_PID_KEY, info["pid"], db_path=path)
    except Exception:
        import logging
        logging.getLogger("cio.version").warning("could not stamp boot version",
                                                 exc_info=True)
    return info


def boot_info(db_path=None) -> dict:
    """The stamped boot version/time/pid (empty strings when never stamped)."""
    from . import db, memory
    path = db_path or db.DB_PATH
    try:
        return {
            "version": memory.get_meta(BOOT_VERSION_KEY, db_path=path) or "",
            "time": memory.get_meta(BOOT_TIME_KEY, db_path=path) or "",
            "pid": memory.get_meta(BOOT_PID_KEY, db_path=path) or "",
        }
    except Exception:
        return {"version": "", "time": "", "pid": ""}


def stale_process_check(db_path=None) -> str | None:
    """Return a violation string when the stamped boot commit differs from the
    repo's current HEAD (i.e. code changed since the process started — restart
    needed). Compares commit hashes only: the `+dirty` suffix is ignored because
    in-progress edits would otherwise flap the check on every dev save.
    None when fine or undeterminable (no git / never stamped)."""
    booted = boot_info(db_path)["version"]
    current = repo_commit()
    if not booted or booted == "unknown" or not current:
        return None
    booted_hash = booted.split("+", 1)[0]
    if booted_hash != current:
        return (f"stale process: booted from {booted}, repo HEAD is now {current} "
                f"— restart the bot to run the current code")
    return None
