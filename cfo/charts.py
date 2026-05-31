"""Chart generation. Returns PNG file paths the bot can send as photos."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless, no display
import matplotlib.pyplot as plt

from . import portfolio

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "charts"


def _out(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / name


def allocation_pie(db_path=portfolio.db.DB_PATH) -> str | None:
    """Pie chart of market value by symbol. None if no priced positions."""
    pos = portfolio.positions(db_path)
    pos = pos.dropna(subset=["market_value"])
    if pos.empty:
        return None
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(pos["market_value"], labels=pos["symbol"], autopct="%1.1f%%", startangle=90)
    ax.set_title("Portfolio Allocation by Market Value")
    path = _out("allocation.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    return str(path)


def pl_bar(db_path=portfolio.db.DB_PATH) -> str | None:
    """Bar chart of unrealized P&L by symbol. None if nothing priced."""
    pos = portfolio.positions(db_path)
    pos = pos.dropna(subset=["unrealized_pl"])
    if pos.empty:
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in pos["unrealized_pl"]]
    ax.bar(pos["symbol"], pos["unrealized_pl"], color=colors)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Unrealized P&L")
    ax.set_title("Unrealized P&L by Holding")
    path = _out("pl.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    return str(path)
