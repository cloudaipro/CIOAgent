"""Tests for committee transcript capture + capture-level retention.

No network, no LLM — exercises the DB layer directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cio import devcapture
from cio.committee import transcript


@pytest.fixture
def tdb(tmp_path, monkeypatch):
    """Point transcript at a throwaway DB."""
    p = tmp_path / "committee.db"
    monkeypatch.setattr(transcript, "DB_PATH", p)
    return p


def _rec(run_id, role="risk", svc="openai", **kw):
    transcript.record(
        role_key=role, service=svc, model="gpt-x",
        system_prompt="SYS " + run_id, user_prompt="USR " + run_id,
        response="ANS " + run_id, tokens=kw.get("tokens", 7),
        run_id=run_id, symbol=kw.get("symbol", "AAPL"),
    )


def test_record_and_get_run(tdb):
    _rec("run1", role="valuation")
    _rec("run1", role="risk")
    calls = transcript.get_run("run1")
    assert [c["role_key"] for c in calls] == ["valuation", "risk"]
    assert calls[0]["system_prompt"] == "SYS run1"
    assert calls[0]["response"] == "ANS run1"


def test_list_runs_summarizes(tdb):
    _rec("run1"); _rec("run1"); _rec("run2")
    runs = {r["run_id"]: r for r in transcript.list_runs()}
    assert runs["run1"]["calls"] == 2
    assert runs["run1"]["tokens"] == 14
    assert runs["run2"]["calls"] == 1
    assert runs["run1"]["symbol"] == "AAPL"


def test_never_raises_on_bad_path(monkeypatch):
    # A directory where a file is expected → record swallows the error.
    monkeypatch.setattr(transcript, "DB_PATH", Path("/proc/should-not-write/x.db"))
    transcript.record("risk", "openai", "m", "s", "u", "r", run_id="z")  # no raise
    assert transcript.list_runs() == []


def test_level1_prunes_old_runs(tdb, monkeypatch):
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "1")
    monkeypatch.setenv("CIO_TRANSCRIPT_KEEP_RUNS", "2")
    assert devcapture.prune_enabled() is True
    for i in range(5):
        _rec(f"run{i}")
    kept = {r["run_id"] for r in transcript.list_runs()}
    assert kept == {"run3", "run4"}  # only the newest 2 survive


def test_level2_keeps_everything(tdb, monkeypatch):
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "2")
    monkeypatch.setenv("CIO_TRANSCRIPT_KEEP_RUNS", "2")
    assert devcapture.prune_enabled() is False
    for i in range(5):
        _rec(f"run{i}")
    assert len(transcript.list_runs()) == 5


def test_capture_level_clamped(monkeypatch):
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "9")
    assert devcapture.level() == 3
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "0")
    assert devcapture.level() == 1
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "junk")
    assert devcapture.level() == 1


def test_telegram_disabled_at_level3(monkeypatch):
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "3")
    assert devcapture.telegram_enabled() is False
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "1")
    assert devcapture.telegram_enabled() is True
