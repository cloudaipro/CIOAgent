"""Layer 2 — Quality Filter (FR-003).

ALL minimums must hold to PASS; a field that can't be measured fails closed (we
never pass on missing data). Pure: takes a fundamentals dict + an OHLCV DataFrame.

  market cap            > $2B
  avg daily $-volume    > $50M   (20-day, Close*Volume)
  revenue growth        > 15%
  forward EPS growth    > 15%    ((fwd_eps - trailing_eps)/|trailing_eps|)
  free cash flow        > 0
"""
from __future__ import annotations

MIN_MKT_CAP = 2_000_000_000.0
MIN_DOLLAR_VOL = 50_000_000.0
MIN_REV_GROWTH = 15.0
MIN_FWD_EPS_GROWTH = 15.0


def forward_eps_growth(fund: dict) -> float | None:
    """% growth from trailing to forward EPS. None when it can't be computed
    reliably (missing fields, or non-positive trailing base)."""
    eps = fund.get("eps")
    fwd = fund.get("forward_eps")
    if eps is None or fwd is None or eps <= 0:
        return None
    return (fwd - eps) / abs(eps) * 100.0


def avg_dollar_volume(df, window: int = 20) -> float | None:
    """20-day average daily dollar volume (Close*Volume). None if data too short."""
    if df is None or "Close" not in df or "Volume" not in df or len(df) < window:
        return None
    tail = df.iloc[-window:]
    return float((tail["Close"] * tail["Volume"]).mean())


def evaluate(fund: dict, df) -> dict:
    """Return {pass, market_cap, dollar_vol, revenue_growth, fwd_eps_growth,
    free_cash_flow, reasons}. PASS requires every minimum to be met."""
    cap = fund.get("market_cap")
    rev = fund.get("revenue_growth_pct")
    fcf = fund.get("free_cash_flow")
    dvol = avg_dollar_volume(df)
    fwd_g = forward_eps_growth(fund)

    reasons: list[str] = []
    def need(ok: bool, msg: str):
        if not ok:
            reasons.append(msg)

    need(cap is not None and cap > MIN_MKT_CAP, "cap<=2B/na")
    need(dvol is not None and dvol > MIN_DOLLAR_VOL, "$vol<=50M/na")
    need(rev is not None and rev > MIN_REV_GROWTH, "rev<=15%/na")
    need(fwd_g is not None and fwd_g > MIN_FWD_EPS_GROWTH, "fwdEPS<=15%/na")
    need(fcf is not None and fcf > 0, "fcf<=0/na")

    return {
        "pass": not reasons,
        "market_cap": cap,
        "dollar_vol": dvol,
        "revenue_growth": rev,
        "fwd_eps_growth": fwd_g,
        "free_cash_flow": fcf,
        "reasons": reasons,
    }
