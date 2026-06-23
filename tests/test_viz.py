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


def test_stock_indicators_tool_returns_image_to_model(tmp_path, monkeypatch):
    """The tool must return the rendered PNG as an image block so the model can
    SEE its own chart (conv#229-232: model was blind to its output), AND still
    queue the path for Telegram delivery."""
    import asyncio
    import cio.agent as a
    import cio.stock as stock

    png = render_indicator_png(make_ohlcv(n_rows=120, seed=50), symbol="T",
                               out_dir=str(tmp_path), filename="seen.png")
    monkeypatch.setattr(stock, "render_indicators", lambda *aa, **kw: png)

    a._PENDING.clear()
    tool = next(t for t in a.CIO_TOOLS if t.name == "stock_indicators")
    res = asyncio.run(tool.handler({"symbol": "T", "profile": "swing"}))

    types = [b["type"] for b in res["content"]]
    assert "image" in types and "text" in types
    img = next(b for b in res["content"] if b["type"] == "image")
    assert img["mimeType"] == "image/png" and len(img["data"]) > 1000
    assert png in a._PENDING            # still queued for Telegram
    a._PENDING.clear()


def test_emit_image_seen_missing_path():
    import cio.agent as a
    a._PENDING.clear()
    res = a._emit_image_seen(None, "ok", "no data")
    assert res == a._text("no data")
    assert a._PENDING == []


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
    # _NAV is hierarchical: (category, [(icon, label, href), ...]). Walk both levels.
    all_hrefs = "".join(
        href for _cat, items in views._NAV for _icon, _label, href in items
    )
    assert "/indicators" in all_hrefs


def test_dashboard_form_error():
    from cio.dashboard import views
    html = views.render_indicators_form(2, error="BAD: boom")
    assert "BAD: boom" in html


# --------------------------------------------------------------------------- #
# generic indicators-dict contract (autoplot-style flexibility)
# --------------------------------------------------------------------------- #
def _close(df):
    return df["Close"]


def test_dict_over_and_below_generic():
    df = make_ohlcv(n_rows=150, seed=20)
    ind = {
        "EMA20": {"type": "over", "data": _close(df).ewm(span=20).mean(), "color": "purple"},
        "Momentum": {"type": "below", "data": _close(df).pct_change(),
                     "levels": [0.0], "color": "teal"},
    }
    spec = build_spec(df, indicators=ind, symbol="T")
    assert [o.label for o in spec.price_overlays] == ["EMA20"]
    assert spec.price_overlays[0].color == "purple"
    assert [p.name for p in spec.panels] == ["Momentum"]
    assert any(h.y == 0.0 for h in spec.panels[0].hlines)


def test_dict_multi_panel():
    df = make_ohlcv(n_rows=150, seed=21)
    ind = {"OSC": {"type": "multi", "levels": [20, 80],
                   "A": {"data": _close(df).rolling(3).mean(), "color": "red"},
                   "B": {"data": _close(df).rolling(7).mean(), "color": "blue"}}}
    spec = build_spec(df, indicators=ind, symbol="T")
    p = spec.panels[0]
    assert [ln.label for ln in p.lines] == ["A", "B"]
    assert p.lines[0].color == "red" and p.lines[1].color == "blue"
    assert {h.y for h in p.hlines} == {20, 80}


def test_dict_macd_and_crossover_types():
    df = make_ohlcv(n_rows=150, seed=22)
    m = df.ta.macd()
    c = list(m.columns)
    ind = {
        "MACD": {"type": "MACD", "macd": m[c[0]], "signal": m[c[2]], "histogram": m[c[1]]},
        "X": {"type": "Crossover", "line1": _close(df).rolling(5).mean(),
              "line2": _close(df).rolling(20).mean()},
    }
    spec = build_spec(df, indicators=ind, symbol="T")
    macd = next(p for p in spec.panels if p.name == "MACD")
    assert macd.hist is not None and len(macd.lines) == 2
    xover = next(p for p in spec.panels if p.name == "X")
    assert len(xover.lines) == 2


def test_dict_flags_on_price_and_panel():
    df = make_ohlcv(n_rows=120, seed=23)
    bull = pd.Series(False, index=df.index); bull.iloc[-5] = True
    bear = pd.Series(False, index=df.index); bear.iloc[-8] = True
    ind = {
        "RSI": {"type": "RSI", "data": df.ta.rsi()},
        "BUY": {"type": "flags", "bull": bull},                       # -> price
        "DIV": {"type": "flags", "bear": bear, "target": "RSI"},      # -> RSI panel
    }
    spec = build_spec(df, indicators=ind, symbol="T", window=60)
    assert any(f.kind == "bull" for f in spec.price_flags)
    rsi = next(p for p in spec.panels if p.name == "RSI")
    assert any(f.kind == "bear" for f in rsi.flags)


def test_dict_swings_type():
    df = make_ohlcv(n_rows=160, seed=24)
    spec = build_spec(df, indicators={"SW": {"type": "Swings", "data": None}}, symbol="T")
    assert spec.swings  # populated from the OHLC frame


def test_below_cap_limits_panels():
    df = make_ohlcv(n_rows=120, seed=25)
    ind = {f"p{i}": {"type": "below", "data": _close(df) + i} for i in range(6)}
    spec = build_spec(df, indicators=ind, symbol="T", below_cap=2)
    assert len(spec.panels) == 2


def test_over_cap_limits_overlays():
    df = make_ohlcv(n_rows=120, seed=26)
    ind = {f"m{i}": {"type": "over", "data": _close(df) + i} for i in range(6)}
    spec = build_spec(df, indicators=ind, symbol="T", over_cap=3)
    assert len(spec.price_overlays) == 3


def test_user_dict_disables_auto_divergence(monkeypatch):
    # a supplied dict must NOT auto-inject committee divergence flags
    import cio.stock as stock

    class _Eng:
        def run(self, df, strat):
            out = pd.DataFrame(index=df.index)
            out[f"c_{strat.upper()}_DIVERGENCE_BEAR"] = True
            out[f"c_{strat.upper()}_DIVERGENCE_BULL"] = False
            return out

    monkeypatch.setattr(stock, "get_engine", lambda: _Eng())
    df = make_ohlcv(n_rows=120, seed=27)
    spec = build_spec(df, indicators={"RSI": {"type": "RSI", "data": df.ta.rsi()}},
                      symbol="T")
    assert spec.price_flags == []
    assert all(p.flags == [] for p in spec.panels)


def test_unknown_below_type_falls_back_to_line():
    df = make_ohlcv(n_rows=120, seed=28)
    spec = build_spec(df, indicators={"Z": {"type": "wat", "data": _close(df)}},
                      symbol="T")
    assert [p.name for p in spec.panels] == ["Z"]
    assert len(spec.panels[0].lines) == 1


def test_default_dict_helper():
    from cio.stock.viz.spec import default_indicator_dict
    df = make_ohlcv(n_rows=150, seed=29)
    d = default_indicator_dict(df, ["RSI"])
    assert d["RSI"]["type"] == "RSI"
    assert any(k.startswith("MA") for k in d)


def test_render_png_with_custom_dict(tmp_path):
    df = make_ohlcv(n_rows=150, seed=30)
    ind = {"EMA": {"type": "over", "data": _close(df).ewm(span=10).mean()},
           "MOM": {"type": "below", "data": _close(df).diff(), "levels": [0]}}
    out = render_indicator_png(df, indicators=ind, symbol="T",
                               out_dir=str(tmp_path), filename="cd.png")
    assert _is_png(out)


# --------------------------------------------------------------------------- #
# profile-driven default preset (conv#219-222 fix: panels follow the profile)
# --------------------------------------------------------------------------- #
def test_default_preset_follows_profile():
    df = make_ohlcv(n_rows=200, seed=40)
    committee = build_spec(df, "committee", symbol="T")
    swing = build_spec(df, "swing", symbol="T")
    cpanels = {p.name.upper() for p in committee.panels}
    spanels = {p.name.upper() for p in swing.panels}
    # committee = trix/kst/rsi/cmf/er ; swing = squeeze/kdj/fisher/efi (+vidya overlay)
    assert {"TRIX", "KST", "RSI", "CMF", "ER"} <= cpanels
    assert {"SQUEEZE", "KDJ", "FISHER", "EFI"} <= spanels
    assert cpanels != spanels                         # the bug: they were identical


def test_swing_has_fisher_squeeze_and_vidya_overlay():
    df = make_ohlcv(n_rows=200, seed=41)
    spec = build_spec(df, "swing", symbol="T")
    names = {p.name.upper() for p in spec.panels}
    assert "FISHER" in names and "SQUEEZE" in names      # the missing panels
    overlays = {o.label.upper() for o in spec.price_overlays}
    assert "VIDYA" in overlays                           # VIDYA is an adaptive MA overlay


def test_monitor_profile_panels():
    df = make_ohlcv(n_rows=200, seed=42)
    spec = build_spec(df, "monitor", symbol="T")
    names = {p.name.upper() for p in spec.panels}
    assert {"MACD", "STOCH", "PVO", "SQUEEZE"} <= names


def test_invalid_profile_falls_back():
    df = make_ohlcv(n_rows=150, seed=43)
    spec = build_spec(df, "no-such-profile", symbol="T")
    assert [p.name for p in spec.panels] == ["MACD", "RSI", "KDJ"]


def test_registry_covers_all_profile_indicators():
    from cio.stock.viz.spec import _REGISTRY, default_indicator_dict
    df = make_ohlcv(n_rows=200, seed=44)
    needed = {"trix", "kst", "rsi", "cmf", "er", "macd", "stoch", "pvo",
              "squeeze", "kdj", "fisher", "efi", "vidya"}
    assert needed <= set(_REGISTRY)
    d = default_indicator_dict(df, sorted(needed), include_ma=False)
    # every requested indicator produced an entry with a valid type; squeeze also
    # auto-adds the Bollinger + Keltner band overlays (BB-inside-KC visualization)
    assert all("type" in v for v in d.values())
    assert {"Bollinger", "Keltner"} <= set(d)
    assert len(d) == len(needed) + 2


def test_squeeze_panel_is_ttm_histogram():
    """Squeeze must render as a TTM momentum histogram (4-color bars + zero-line
    on/off dots), not a plain line."""
    from cio.stock.viz.spec import (_SQZ_POS_UP, _SQZ_POS_DN, _SQZ_NEG_UP,
                                    _SQZ_NEG_DN, _SQZ_DOT_ON, _SQZ_DOT_OFF)
    df = make_ohlcv(n_rows=200, seed=46)
    spec = build_spec(df, "swing", symbol="T", window=60)
    sq = next(p for p in spec.panels if p.name.upper() == "SQUEEZE")
    assert sq.lines == []                       # no line — it's a histogram
    assert sq.hist is not None
    assert sq.hist_colors is not None and len(sq.hist_colors) == spec.n
    assert set(sq.hist_colors) <= {_SQZ_POS_UP, _SQZ_POS_DN, _SQZ_NEG_UP, _SQZ_NEG_DN}
    assert len(sq.dots) == spec.n
    dot_colors = {c for _, c in sq.dots}
    assert dot_colors <= {_SQZ_DOT_ON, _SQZ_DOT_OFF}
    assert any(h.y == 0.0 for h in sq.hlines)   # zero reference line


def test_squeeze_dot_matches_on_state():
    """A red dot must appear exactly where pandas_ta SQZ_ON is set."""
    import pandas_ta  # noqa
    from cio.stock.viz.spec import _SQZ_DOT_ON
    df = make_ohlcv(n_rows=200, seed=47)
    s = df.ta.squeeze()
    on_tail = s["SQZ_ON"].iloc[-60:].astype(bool).values
    spec = build_spec(df, indicators={"SQZ": {"type": "squeeze",
                      "momentum": s[s.columns[0]], "on": s["SQZ_ON"]}},
                      symbol="T", window=60)
    p = spec.panels[0]
    red = [c == _SQZ_DOT_ON for _, c in p.dots]
    assert red == list(on_tail)


def test_squeeze_adds_bollinger_and_keltner_bands():
    """When a squeeze panel is shown, BB + KC must overlay the price panel."""
    df = make_ohlcv(n_rows=200, seed=48)
    swing = build_spec(df, "swing", symbol="T", window=60)
    labels = {b.label for b in swing.price_bands}
    assert {"Bollinger", "Keltner"} <= labels
    for b in swing.price_bands:
        assert len(b.upper) == swing.n and len(b.lower) == swing.n
    bb = next(b for b in swing.price_bands if b.label == "Bollinger")
    assert bb.mid is not None and len(bb.mid) == swing.n   # BB has a basis line
    # committee has no squeeze -> no bands
    assert build_spec(df, "committee", symbol="T").price_bands == []


def test_bands_type_via_custom_dict():
    df = make_ohlcv(n_rows=150, seed=49)
    bb = df.ta.bbands(length=20, std=2.0)
    c = list(bb.columns)
    ind = {"BB": {"type": "bands", "lower": bb[c[0]], "mid": bb[c[1]],
                  "upper": bb[c[2]], "color": "#2563eb", "fill_alpha": 0.1}}
    spec = build_spec(df, indicators=ind, symbol="T", window=60)
    assert len(spec.price_bands) == 1
    band = spec.price_bands[0]
    assert band.label == "BB" and band.fill_alpha == 0.1
    assert len(band.upper) == spec.n and len(band.lower) == spec.n


def test_render_swing_png_differs_from_committee(tmp_path):
    df = make_ohlcv(n_rows=200, seed=45)
    c = render_indicator_png(df, "committee", symbol="T",
                             out_dir=str(tmp_path), filename="c.png")
    s = render_indicator_png(df, "swing", symbol="T",
                             out_dir=str(tmp_path), filename="s.png")
    import os
    # different panel sets => different rendered bytes
    assert open(c, "rb").read() != open(s, "rb").read()
    assert _is_png(c) and _is_png(s)
