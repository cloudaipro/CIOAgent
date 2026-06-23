"""F5 — news-spike detector tests (cio/watchlist_monitor/spike.py).

Fully offline: GDELT/Finnhub fns are injected. The detector is deterministic
(article counts), so each gate is exercised in isolation.
"""
import pytest

from cio.watchlist_monitor import spike


def _tone(vol_2h, vol_7d):
    def _fn(q, hours):
        return {"volume": vol_2h if hours == spike._WINDOW_H else vol_7d,
                "avg_tone": 0.0}
    return _fn


def _heads(domains):
    def _fn(q, hours=2, limit=20):
        return [{"title": f"news {i}", "url": f"http://{d}/{i}", "domain": d}
                for i, d in enumerate(domains)]
    return _fn


def _no_news(symbol):
    return []


def test_no_spike_below_min_count(monkeypatch):
    monkeypatch.delenv("CIO_SPIKE_MIN_COUNT", raising=False)
    # 4 articles/2h < default min 5 -> no spike, regardless of baseline.
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(4, 10),
                             headlines_fn=_heads(["a.com", "b.com"]),
                             news_fn=_no_news)
    assert out is None


def test_no_spike_when_not_multiple_of_baseline(monkeypatch):
    monkeypatch.delenv("CIO_SPIKE_MULT", raising=False)
    # 6 articles/2h but 7d=420 -> baseline 5/2h; 6 < 3*5=15 -> no spike.
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(6, 420),
                             headlines_fn=_heads(["a.com", "b.com"]),
                             news_fn=_no_news)
    assert out is None


def test_no_spike_single_source(monkeypatch):
    # Volume + multiplier pass, but only one distinct source -> no spike.
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(20, 0),
                             headlines_fn=_heads(["a.com", "a.com"]),
                             news_fn=_no_news)
    assert out is None


def test_spike_on_new_coverage_burst(monkeypatch):
    # No prior 7d coverage (baseline 0) + 12 articles/2h from 3 sources -> spike,
    # multiplier None (new coverage).
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(12, 0),
                             headlines_fn=_heads(["a.com", "b.com", "c.com"]),
                             news_fn=_no_news)
    assert out is not None
    assert out["count"] == 12
    assert out["sources"] == 3
    assert out["multiplier"] is None
    assert len(out["top_headlines"]) == 3


def test_spike_on_multiple_over_baseline(monkeypatch):
    # 7d=168 -> baseline 2/2h; 20 articles/2h = 10x >= 3x -> spike with multiplier.
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(20, 168),
                             headlines_fn=_heads(["a.com", "b.com"]),
                             news_fn=_no_news)
    assert out is not None
    assert out["baseline"] == 2.0
    assert out["multiplier"] == 10.0


def test_env_thresholds_respected(monkeypatch):
    monkeypatch.setenv("CIO_SPIKE_MIN_COUNT", "50")
    # 20 articles now below the raised min 50 -> no spike.
    out = spike.detect_spike("AAPL", query="x",
                             tone_fn=_tone(20, 0),
                             headlines_fn=_heads(["a.com", "b.com", "c.com"]),
                             news_fn=_no_news)
    assert out is None


def test_scan_offline_safe_no_sources(monkeypatch):
    # Real (disabled) GDELT/Finnhub -> zero volume -> no spikes, no raise.
    monkeypatch.setenv("CIO_GDELT_ENABLED", "0")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert spike.scan(["AAPL", "MSFT"]) == []


def test_format_spike_alert_deterministic():
    spikes = [{"symbol": "NVDA", "count": 18, "baseline": 2.0, "multiplier": 9.0,
               "sources": 4, "top_headlines": [
                   {"title": "NVDA guidance shock", "url": "http://x", "source": "reuters.com"}]}]
    text = spike.format_spike_alert(spikes)
    assert "NVDA" in text and "18 articles/2h" in text and "9.0×" in text
    assert "NVDA guidance shock" in text
