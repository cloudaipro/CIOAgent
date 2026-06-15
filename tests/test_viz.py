"""
Offline pytest suite for cio.stock.viz — the indicator-visualization (指標視覺化)
package added to close the conv_turns#210 gap (panel had no indicator overlay).

Covers the shared spec core, the matplotlib PNG adapter, the optional bokeh HTML
adapter, divergence sourcing (geometric + committee-strategy flags), and the four
integration surfaces (agent tool, panel flag, committee-PDF appendix, dashboard
route). No network: DataFrames are synthetic; the symbol path is monkeypatched.
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import struct

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_ohlcv
from cio.stock.viz import (
    build_spec,
    divergence_markers,
    render_indicator_png,
    bokeh_available,
    DEFAULT_INDICATORS,
)
from cio.stock.viz import spec as spec_mod


# --------------------------------------------------------------------------- #
# divergence detector
# --------------------------------------------------------------------------- #
def test_divergence_bearish_detected():
    # price: higher high; indicator: lower high  -> bearish divergence
    price = np.array([10, 12, 11, 10, 11, 14, 13, 12, 13], dtype=float)
    ind = np.array([30, 60, 45, 35, 45, 55, 48, 42, 47], dtype=float)
    mk = divergence_markers(price, ind, lookback=20, left=1, right=1)
    kinds = [m.kind for m in mk]
    assert "bear" in kinds


def test_divergence_bullish_detected():
    # price: lower low; indicator: higher low -> bullish divergence
    price = np.array([20, 15, 18, 22, 12, 16, 20], dtype=float)
    ind = np.array([40, 25, 35, 50, 30, 40, 52], dtype=float)
    mk = divergence_markers(price, ind, lookback=20, left=1, right=1)
    assert "bull" in [m.kind for m in mk]


def test_divergence_none_on_monotonic():
    x = np.arange(40, dtype=float)
    assert divergence_markers(x, x, lookback=40) == []


def test_divergence_short_series_safe():
    assert divergence_markers(np.array([1.0, 2.0]), np.array([1.0, 2.0])) == []


# --------------------------------------------------------------------------- #
# spec core
# --------------------------------------------------------------------------- #
def test_build_spec_default_panels():
    df = make_ohlcv(n_rows=180, seed=3)
    spec = build_spec(df, "TEST", symbol="TEST", window=60)
    names = [p.name for p in spec.panels]
    assert names == list(DEFAULT_INDICATORS) == ["MACD", "RSI", "KDJ"]
    assert spec.n == 60
    # every line is trimmed to the display window
    for p in spec.panels:
        for ln in p.lines:
            assert len(ln.values) == spec.n
    assert set(spec.ma) <= {"MA20", "MA60", "MA120"}
    assert spec.x.tolist() == list(range(60))


def test_build_spec_subset_indicators():
    df = make_ohlcv(n_rows=120, seed=5)
    spec = build_spec(df, "T", symbol="T", indicators=["RSI"])
    assert [p.name for p in spec.panels] == ["RSI"]
    rsi = spec.panels[0]
    assert rsi.ylim == (0, 100)
    assert any(h.y == 70 for h in rsi.hlines)


def test_build_spec_unknown_indicator_ignored():
    df = make_ohlcv(n_rows=120, seed=6)
    spec = build_spec(df, "T", symbol="T", indicators=["RSI", "NOPE"])
    assert [p.name for p in spec.panels] == ["RSI"]


def test_build_spec_window_larger_than_data():
    df = make_ohlcv(n_rows=40, seed=7)
    spec = build_spec(df, "T", symbol="T", window=60)
    assert spec.n == 40  # clamps to available rows, never out of range


def test_coerce_ohlc_missing_column_raises():
    df = make_ohlcv(n_rows=30).drop(columns=["High"])
    with pytest.raises(ValueError):
        build_spec(df, "T", symbol="T")


def test_coerce_ohlc_empty_raises():
    with pytest.raises(ValueError):
        build_spec(pd.DataFrame(), "T", symbol="T")


def test_coerce_ohlc_multiindex_columns():
    df = make_ohlcv(n_rows=120, seed=8)
    df2 = df.copy()
    df2.columns = pd.MultiIndex.from_tuples([(c, "X") for c in df.columns])
    spec = build_spec(df2, "T", symbol="T")
    assert spec.panels  # coerced and rendered


def test_swings_are_bounded():
    df = make_ohlcv(n_rows=200, seed=9)
    spec = build_spec(df, "T", symbol="T", window=60)
    # capped at keep=4 highs + 4 lows = 8, all within the window
    assert len(spec.swings) <= 8
    assert all(0 <= x < spec.n for x, _, _ in spec.swings)


# --------------------------------------------------------------------------- #
# committee-strategy divergence flags (the conv#210 RSI/MACD divergence path)
# --------------------------------------------------------------------------- #
class _StubEngine:
    """Yields a deterministic RSI bear-divergence flag on the 5th-from-last bar."""

    def run(self, df, strat):
        out = pd.DataFrame(index=df.index)
        bear = pd.Series(False, index=df.index)
        if len(df) >= 5:
            bear.iloc[-5] = True
        out[f"c_{strat.upper()}_DIVERGENCE_BEAR"] = bear
        out[f"c_{strat.upper()}_DIVERGENCE_BULL"] = False
        return out


def test_strategy_divergence_flags_wired(monkeypatch):
    import cio.stock as stock
    monkeypatch.setattr(stock, "get_engine", lambda: _StubEngine())
    df = make_ohlcv(n_rows=120, seed=11)
    spec = build_spec(df, "T", symbol="T", indicators=["RSI", "MACD"], window=60)
    rsi = next(p for p in spec.panels if p.name == "RSI")
    assert any(f.kind == "bear" for f in rsi.flags)
    # flag projected onto the price panel and within the display window
    assert spec.price_flags and all(0 <= f.x < spec.n for f in spec.price_flags)


def test_strategy_divergence_engine_failure_is_safe(monkeypatch):
    import cio.stock as stock

    class _Boom:
        def run(self, *a, **k):
            raise RuntimeError("engine down")

    monkeypatch.setattr(stock, "get_engine", lambda: _Boom())
    df = make_ohlcv(n_rows=120, seed=12)
    spec = build_spec(df, "T", symbol="T")  # must not raise
    assert all(p.flags == [] for p in spec.panels)


# --------------------------------------------------------------------------- #
# matplotlib PNG adapter
# --------------------------------------------------------------------------- #
def _is_png(path):
    with open(path, "rb") as f:
        sig = f.read(8)
    return sig == b"\x89PNG\r\n\x1a\n"


def test_render_png_valid(tmp_path):
    df = make_ohlcv(n_rows=180, seed=13)
    out = render_indicator_png(df, "committee", symbol="TEST",
                               out_dir=str(tmp_path), filename="t.png")
    assert _is_png(out)
    import os
    assert os.path.getsize(out) > 5_000


def test_render_png_subset(tmp_path):
    df = make_ohlcv(n_rows=120, seed=14)
    out = render_indicator_png(df, "committee", symbol="T",
                               indicators=["MACD"], out_dir=str(tmp_path),
                               filename="m.png")
    assert _is_png(out)


def test_render_png_short_series(tmp_path):
    df = make_ohlcv(n_rows=35, seed=15)
    out = render_indicator_png(df, "committee", symbol="T",
                               out_dir=str(tmp_path), filename="s.png")
    assert _is_png(out)


def test_render_png_no_volume(tmp_path):
    df = make_ohlcv(n_rows=120, seed=16).drop(columns=["Volume"])
    out = render_indicator_png(df, "committee", symbol="T",
                               out_dir=str(tmp_path), filename="nv.png")
    assert _is_png(out)


# --------------------------------------------------------------------------- #
# bokeh HTML adapter (optional dep)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not bokeh_available(), reason="bokeh not installed")
def test_render_html(tmp_path):
    from cio.stock.viz import render_indicator_html
    df = make_ohlcv(n_rows=150, seed=17)
    out = render_indicator_html(df, "committee", symbol="ZTEST",
                                out_dir=str(tmp_path), filename="z.html")
    html = open(out, encoding="utf-8").read()
    assert "ZTEST" in html and "bokeh" in html.lower()


def test_html_importerror_message_when_bokeh_absent(monkeypatch):
    # Simulate bokeh missing: drop cached modules and block the bokeh import so
    # the adapter re-import fails and __init__ wraps it with a friendly message.
    import sys
    import builtins

    for mod in list(sys.modules):
        if mod == "bokeh" or mod.startswith("bokeh.") or mod == "cio.stock.viz.bokeh_plot":
            monkeypatch.delitem(sys.modules, mod, raising=False)

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "bokeh" or name.startswith("bokeh."):
            raise ImportError("no bokeh")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from cio.stock.viz import render_indicator_html
    with pytest.raises(ImportError) as ei:
        render_indicator_html(make_ohlcv(n_rows=60), symbol="T")
    assert "bokeh" in str(ei.value).lower()


# --------------------------------------------------------------------------- #
# integration surfaces
# --------------------------------------------------------------------------- #
def test_agent_tool_registered():
    from cio.agent import CIO_TOOLS
    names = {t.name for t in CIO_TOOLS}
    assert "stock_indicators" in names
    assert "stock_panel" in names


def test_stock_public_api():
    import cio.stock as stock
    assert hasattr(stock, "render_indicators")
    assert "render_indicators" in stock.__all__


def test_pdf_appendix_embed(tmp_path):
    from cio.committee.render_pdf import _figures_html
    df = make_ohlcv(n_rows=120, seed=18)
    png = render_indicator_png(df, symbol="T", out_dir=str(tmp_path), filename="p.png")
    html = _figures_html([("cap", png)], "技術指標")
    assert "data:image/png;base64," in html
    assert "技術指標" in html and "figure" in html


def test_pdf_appendix_missing_file_skipped():
    from cio.committee.render_pdf import _figures_html
    assert _figures_html([("x", "/nonexistent/zzz.png")], "T") == ""


def test_dashboard_form_and_nav():
    from cio.dashboard import views
    html = views.render_indicators_form(2)
    assert "指標視覺化" in html and "/indicators" in html
    assert "/indicators" in "".join(href for _, href in views._NAV)


def test_dashboard_form_error():
    from cio.dashboard import views
    html = views.render_indicators_form(2, error="BAD: boom")
    assert "BAD: boom" in html
