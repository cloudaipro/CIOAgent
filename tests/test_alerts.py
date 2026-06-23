"""F9 — alert dedup/cooldown tests (cio/alerts.py + alert_cooldown table)."""
import pytest

from cio import alerts, db


@pytest.fixture
def dbp(tmp_path):
    p = tmp_path / "t.db"
    db.connect(str(p)).close()   # create schema incl. alert_cooldown
    return str(p)


def test_make_key_normalizes_punct_and_case(dbp):
    k1 = alerts.make_key("Apple Surges 5%!", "Reuters", "https://reuters.com/x")
    k2 = alerts.make_key("apple surges 5", "reuters", "https://reuters.com/y")
    assert k1 == k2                      # punctuation/case stripped; same source+host


def test_claim_then_duplicate_within_cooldown(dbp):
    assert alerts.claim("NVDA halted on news", "GDELT", db_path=dbp, now=1000) is True
    assert alerts.claim("NVDA halted on news", "GDELT", db_path=dbp, now=1500) is False


def test_claim_again_after_cooldown(dbp):
    assert alerts.claim("X", "S", cooldown_s=100, db_path=dbp, now=1000) is True
    assert alerts.claim("X", "S", cooldown_s=100, db_path=dbp, now=1050) is False  # <100s
    assert alerts.claim("X", "S", cooldown_s=100, db_path=dbp, now=1200) is True   # >100s


def test_distinct_events_independent(dbp):
    assert alerts.claim("AAA breaking", "S", db_path=dbp, now=1000) is True
    assert alerts.claim("BBB breaking", "S", db_path=dbp, now=1000) is True


def test_global_claim_rate_limits(dbp):
    assert alerts.global_claim(cooldown_s=60, db_path=dbp, now=1000) is True
    assert alerts.global_claim(cooldown_s=60, db_path=dbp, now=1030) is False
    assert alerts.global_claim(cooldown_s=60, db_path=dbp, now=1100) is True


def test_offline_safe_on_bad_db():
    # Unopenable path: dedup degrades to "not duplicate" and claim still returns True
    # (never swallow a real alert), without raising.
    bad = "/nonexistent/dir/should/not/exist/x.db"
    assert alerts.is_duplicate("k", db_path=bad, now=1) is False
    assert alerts.claim("h", "s", db_path=bad, now=1) is True


def test_prune_removes_old_rows(dbp):
    alerts.mark_fired("old", now=1000, db_path=dbp)
    alerts.mark_fired("new", now=10_000, db_path=dbp)
    assert alerts.prune(older_than_s=1000, now=10_000, db_path=dbp) == 1   # cutoff 9000
    assert alerts.is_duplicate("old", cooldown_s=10**9, now=10_000, db_path=dbp) is False
    assert alerts.is_duplicate("new", cooldown_s=10**9, now=10_000, db_path=dbp) is True
