"""F2 — data-source freshness heartbeat tests (cio/data/freshness.py).

Distinct from quote/bar staleness (test_quote_freshness.py): this tracks WHEN each
source last yielded data. The autouse _isolate_freshness fixture in conftest.py
keeps every test off the repo's real heartbeat file.
"""
import time

from cio.data import freshness as fr


def test_no_record_is_no_data():
    assert fr.status("yfinance") == "no_data"


def test_record_is_fresh():
    fr.record("finnhub", 3)
    assert fr.status("finnhub") == "fresh"


def test_age_buckets():
    fr.record("edgar", 2)
    entry = fr._load().get("edgar")
    now = time.time()
    assert fr._status_from_entry(entry, now=now) == "fresh"
    assert fr._status_from_entry(entry, now=now + 20 * 60) == "stale"       # > 15m
    assert fr._status_from_entry(entry, now=now + 3 * 3600) == "very_stale"  # > 2h


def test_error_record_surfaces_error():
    fr.record("gdelt", error="boom")
    assert fr.status("gdelt") == "error"


def test_success_after_error_clears_to_fresh():
    fr.record("gdelt", error="boom")
    fr.record("gdelt", 5)          # a later success clears the error
    assert fr.status("gdelt") == "fresh"


def test_summary_rollup_worst_required():
    # finnhub (required) fresh, yfinance (required) never recorded -> no_data.
    fr.record("finnhub", 1)
    s = fr.summary()
    assert s["overall"] == "no_data"        # worst status among REQUIRED sources
    rows = {r["id"]: r for r in s["sources"]}
    assert rows["finnhub"]["status"] == "fresh"
    assert rows["finnhub"]["required"] is True
    assert rows["yfinance"]["status"] == "no_data"


def test_summary_all_required_fresh_is_fresh():
    fr.record("finnhub", 1)
    fr.record("yfinance", 100)
    assert fr.summary()["overall"] == "fresh"


def test_optional_source_missing_does_not_redden_rollup():
    fr.record("finnhub", 1)
    fr.record("yfinance", 1)
    # edgar/gdelt/fred/ibkr (optional) are all no_data, but overall stays fresh.
    assert fr.summary()["overall"] == "fresh"


def test_offline_safe_on_unwritable_dir(monkeypatch, tmp_path):
    # A not-yet-existing parent dir must be created, not raise.
    monkeypatch.setenv("CIO_FRESHNESS_FILE", str(tmp_path / "sub" / "x.json"))
    fr.record("finnhub", 1)
    assert fr.status("finnhub") == "fresh"
