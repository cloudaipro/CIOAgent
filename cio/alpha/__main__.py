"""Alpha Hunter CLI (PRD §8).

    python -m cio.alpha [--universe FILE] [--no-publish] [--json]

Runs the funnel, prints the regime + Top-20, and (unless --no-publish) publishes
the Alpha-<date> watchlist.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import run, store


def _fmt(result) -> str:
    r = result.regime
    lines = [
        f"Market Regime: {r.get('status')}  ({r.get('detail')})",
        f"  QQQ {r.get('qqq')}  50MA {r.get('ma50')}  200MA {r.get('ma200')}",
        "",
        "Sector ranking (RS = 0.5*3M + 0.5*6M):",
    ]
    for s in result.sectors:
        lines.append(f"  {s['ticker']:<5} RS {s['rs']:>7}  3M {s['ret_3m']}  6M {s['ret_6m']}")
    lines += ["", f"Top candidates ({len(result.candidates)} passed quality, "
                  f"universe {result.universe_size}):",
              f"  {'#':>2}  {'TICK':<6} {'SECT':<5} {'FINAL':>7} {'MOM':>7} {'TRND':>7}"
              f" {'EARN':>7} {'REV%':>8} {'fEPS%':>8} {'SURP':>6}"]
    for c in result.top():
        lines.append(
            f"  {c.get('rank',0):>2}  {c['ticker']:<6} {c['sector']:<5} "
            f"{_n(c['final']):>7} {_n(c['momentum']):>7} {_n(c['trend']):>7} "
            f"{_n(c['earnings']):>7} {_n(c['revenue_growth']):>8} "
            f"{_n(c['fwd_eps_growth']):>8} {_n(c['surprise']):>6}")
    return "\n".join(lines)


def _n(x):
    return "-" if x is None else (f"{x:g}" if isinstance(x, float) else str(x))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cio.alpha", description="Alpha Hunter funnel")
    ap.add_argument("--universe", help="path to a ticker list (overrides config/env)")
    ap.add_argument("--no-publish", action="store_true", help="don't create/refresh the watchlist")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    result = run(universe_path=args.universe)

    if args.no_publish:
        meta = {"run_id": None, "watchlist_id": None, "watchlist_name": None}
    else:
        meta = store.save_run(result)

    if args.json:
        print(json.dumps({
            "run_date": result.run_date, "regime": result.regime,
            "sectors": result.sectors, "candidates": result.candidates,
            "universe_size": result.universe_size, **meta,
        }, indent=2, default=str))
    else:
        print(_fmt(result))
        if meta.get("watchlist_name"):
            print(f"\nPublished watchlist: {meta['watchlist_name']} "
                  f"(id {meta['watchlist_id']}, set active)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
