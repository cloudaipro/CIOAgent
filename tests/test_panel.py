"""
Offline pytest suite for cio.stock.panel — render_panel, related_links, agent tool registration.

All tests run WITHOUT network access.
  - load_or_download_stock_data  → synthetic OHLCV via monkeypatch
  - fundamentals                 → fake dict via monkeypatch
  - normalize_symbol             → returns symbol unchanged (monkeypatched)
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import os
import pytest
import pandas as pd
import numpy as np

from tests.conftest import make_ohlcv

# ---------------------------------------------------------------------------
# Shared fake data
# ---------------------------------------------------------------------------

FAKE_FUND = {
    "name": "測試公司 Test Co",
    "pe": 15.3,
    "pb": 2.1,
    "yield_pct": 3.85,
    "eps": 8.5,
    "roe_pct": 12.4,
    "margin_pct": None,      # deliberate None → should render "—"
    "market_cap": 50_000_000_000,
    "wk52_high": 520.0,
    "wk52_low": 300.0,
    "short_ratio": None,
    "shares_short": None,
    "revenue_q": [
        {"period": "2023-Q1", "value": 1_000_000_000, "yoy_pct": None},
        {"period": "2023-Q2", "value": 1_100_000_000, "yoy_pct": 10.0},
        {"period": "2023-Q3", "value": 1_050_000_000, "yoy_pct": -4.5},
        {"period": "2023-Q4", "value": 1_200_000_000, "yoy_pct": 14.3},
        {"period": "2024-Q1", "value": 1_150_000_000, "yoy_pct": 15.0},
    ],
}


def _patch_stock_data(monkeypatch):
    """Monkeypatch load_or_download_stock_data and fundamentals to offline values."""
    import cio.stock.data as data_mod

    synthetic_ohlcv = make_ohlcv(n_rows=300)
    monkeypatch.setattr(data_mod, "load_or_download_stock_data",
                        lambda *a, **kw: synthetic_ohlcv)
    monkeypatch.setattr(data_mod, "fundamentals",
                        lambda *a, **kw: dict(FAKE_FUND))
    # normalize_symbol: pass through unchanged so no network call occurs
    monkeypatch.setattr(data_mod, "normalize_symbol", lambda s: s)

    # Also patch the panel module's imported references if already bound
    try:
        import cio.stock.panel as panel_mod
        monkeypatch.setattr(panel_mod, "load_or_download_stock_data",
                            lambda *a, **kw: synthetic_ohlcv, raising=False)
        monkeypatch.setattr(panel_mod, "fundamentals",
                            lambda *a, **kw: dict(FAKE_FUND), raising=False)
        monkeypatch.setattr(panel_mod, "normalize_symbol",
                            lambda s: s, raising=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. render_panel writes a non-empty PNG and returns an existing path
# ---------------------------------------------------------------------------

def test_render_panel_writes_png(tmp_path, monkeypatch):
    """render_panel("3293.TWO") must write a non-empty .png and return its path."""
    _patch_stock_data(monkeypatch)

    from cio.stock.panel import render_panel
    path = render_panel("3293.TWO", out_dir=str(tmp_path))

    assert os.path.exists(path), f"PNG not found at: {path}"
    assert path.endswith(".png"), f"Expected .png extension, got: {path}"
    size = os.path.getsize(path)
    assert size > 1024, f"PNG suspiciously small ({size} bytes); likely empty or stub."


# ---------------------------------------------------------------------------
# 2. Panel import does not require network; CJK font setting does not raise
# ---------------------------------------------------------------------------

def test_panel_import_no_network():
    """Importing cio.stock.panel must not trigger any network call."""
    import cio.stock.panel  # noqa: F401 — just confirm import succeeds
    import matplotlib.pyplot as plt
    sans_serif = plt.rcParams.get("font.sans-serif", [])
    assert "Noto Sans CJK JP" in sans_serif or "DejaVu Sans" in sans_serif, (
        f"CJK font not set; font.sans-serif = {sans_serif}"
    )


# ---------------------------------------------------------------------------
# 3. related_links shape — TW ticker
# ---------------------------------------------------------------------------

def test_related_links_tw():
    """related_links('3293.TWO') must return TW-specific links with correct keys."""
    from cio.stock.panel import related_links
    links = related_links("3293.TWO")
    assert isinstance(links, dict), f"Expected dict, got {type(links)}"
    expected_keys = {"Yahoo", "Google", "TradingView", "Goodinfo", "Wantgoo"}
    assert set(links.keys()) == expected_keys, (
        f"TW links keys mismatch. Got: {set(links.keys())}"
    )
    # Spot-check code extraction: should use "3293" not "3293.TWO"
    assert "3293" in links["Yahoo"], f"Yahoo URL missing code: {links['Yahoo']}"
    assert "3293" in links["Goodinfo"], f"Goodinfo URL missing code: {links['Goodinfo']}"


# ---------------------------------------------------------------------------
# 4. related_links shape — US ticker
# ---------------------------------------------------------------------------

def test_related_links_us():
    """related_links('AAPL') must return US-specific links with correct keys."""
    from cio.stock.panel import related_links
    links = related_links("AAPL")
    assert isinstance(links, dict), f"Expected dict, got {type(links)}"
    expected_keys = {"Yahoo", "Google", "TradingView", "Finviz"}
    assert set(links.keys()) == expected_keys, (
        f"US links keys mismatch. Got: {set(links.keys())}"
    )
    assert "AAPL" in links["Yahoo"], f"Yahoo URL missing symbol: {links['Yahoo']}"
    assert "AAPL" in links["Finviz"], f"Finviz URL missing symbol: {links['Finviz']}"


# ---------------------------------------------------------------------------
# 5. stock_panel in CIO_TOOLS; count == 22
# ---------------------------------------------------------------------------

def test_stock_panel_in_cio_tools():
    """'stock_panel' must be in CIO_TOOLS and the total count must be 22."""
    import cio.agent as agent_mod
    tool_names = [t.name for t in agent_mod.CIO_TOOLS]
    assert "stock_panel" in tool_names, (
        f"'stock_panel' not found in CIO_TOOLS. Current tools: {tool_names}"
    )
    assert len(agent_mod.CIO_TOOLS) == 22, (
        f"Expected 22 tools in CIO_TOOLS, got {len(agent_mod.CIO_TOOLS)}: {tool_names}"
    )


# ---------------------------------------------------------------------------
# 6. render_panel with some None fundamentals (robustness)
# ---------------------------------------------------------------------------

def test_render_panel_partial_fundamentals(tmp_path, monkeypatch):
    """render_panel must succeed even when most fundamentals are None."""
    import cio.stock.data as data_mod

    synthetic_ohlcv = make_ohlcv(n_rows=100)
    all_none_fund = {k: None for k in FAKE_FUND}
    monkeypatch.setattr(data_mod, "load_or_download_stock_data",
                        lambda *a, **kw: synthetic_ohlcv)
    monkeypatch.setattr(data_mod, "fundamentals",
                        lambda *a, **kw: dict(all_none_fund))
    monkeypatch.setattr(data_mod, "normalize_symbol", lambda s: s)

    from cio.stock.panel import render_panel
    path = render_panel("AAPL", out_dir=str(tmp_path))
    assert os.path.exists(path), f"PNG not found: {path}"
    assert os.path.getsize(path) > 1024, "PNG too small with all-None fundamentals"


# ---------------------------------------------------------------------------
# 7. render_panel with no OHLCV data (df=None)
# ---------------------------------------------------------------------------

def test_render_panel_no_ohlcv(tmp_path, monkeypatch):
    """render_panel must succeed even when load_or_download_stock_data returns None."""
    import cio.stock.data as data_mod

    monkeypatch.setattr(data_mod, "load_or_download_stock_data",
                        lambda *a, **kw: None)
    monkeypatch.setattr(data_mod, "fundamentals",
                        lambda *a, **kw: dict(FAKE_FUND))
    monkeypatch.setattr(data_mod, "normalize_symbol", lambda s: s)

    from cio.stock.panel import render_panel
    path = render_panel("3293.TWO", out_dir=str(tmp_path))
    assert os.path.exists(path), f"PNG not found: {path}"
