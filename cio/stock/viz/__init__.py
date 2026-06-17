"""
Indicator visualization (指標視覺化) for CIOAgent.

Closes the gap noted in conv_turns#210: the stock panel shows price +
fundamentals but no indicator overlay, so users were sent to TradingView to
apply RSI / MACD / KDJ and read divergence by hand. This package renders those
indicators — with divergence + swing markers — as an image for messages and the
committee PDF, and (optionally) as an interactive bokeh HTML for the dashboard.

Design: one shared core (``spec.build_spec``) feeds two thin render adapters —
matplotlib (PNG, always available) and bokeh (HTML, optional).
"""
from __future__ import annotations

from .spec import (
    ChartSpec, build_spec, divergence_markers, indicator_states, DEFAULT_INDICATORS,
)


def render_indicator_png(symbol_or_df, profile: str = "committee", **kw) -> str:
    """Render the indicator chart as a PNG; returns the file path."""
    from .mpl_plot import render
    return render(symbol_or_df, profile, **kw)


def render_indicator_html(symbol_or_df, profile: str = "committee", **kw) -> str:
    """
    Render the indicator chart as standalone bokeh HTML; returns the file path.
    Requires the optional ``bokeh`` dependency (HTML only — no selenium).
    """
    try:
        from .bokeh_plot import render_html
    except ImportError as e:  # pragma: no cover - exercised when bokeh absent
        raise ImportError(
            "Interactive HTML chart needs the optional 'bokeh' package "
            "(pip install bokeh). PNG output via render_indicator_png needs nothing extra."
        ) from e
    return render_html(symbol_or_df, profile, **kw)


def bokeh_available() -> bool:
    try:
        import bokeh  # noqa: F401
        return True
    except Exception:
        return False


__all__ = [
    "build_spec",
    "ChartSpec",
    "divergence_markers",
    "indicator_states",
    "DEFAULT_INDICATORS",
    "render_indicator_png",
    "render_indicator_html",
    "bokeh_available",
]
