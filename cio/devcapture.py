"""Developer-capture configuration — the knobs feeding the dev dashboard.

A single env knob, ``CIO_CAPTURE_LEVEL`` (1-3, default 1), controls how much the
dashboard records. Capture is ON by default; the level only tunes scope and
retention. The dashboard verifies the committee/bot behave correctly, so it
stores the operator's own data on the operator's own machine.

  1  full capture, committee transcript pruned to KEEP_RUNS, Telegram history ON
  2  full capture, no pruning (keep everything forever)
  3  committee only — Telegram history OFF

All helpers are cheap and never raise.
"""
from __future__ import annotations

import os

DEFAULT_KEEP_RUNS = 200


def level() -> int:
    """Capture level 1-3 (clamped). Default 1. Invalid value → 1."""
    try:
        v = int(os.getenv("CIO_CAPTURE_LEVEL", "1"))
    except (TypeError, ValueError):
        return 1
    return min(3, max(1, v))


def prune_enabled() -> bool:
    """Level 2 keeps everything; levels 1 and 3 prune old committee runs."""
    return level() != 2


def keep_runs() -> int:
    """How many most-recent committee runs to retain when pruning."""
    try:
        return int(os.getenv("CIO_TRANSCRIPT_KEEP_RUNS", str(DEFAULT_KEEP_RUNS)))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_RUNS


def telegram_enabled() -> bool:
    """Levels 1 and 2 record Telegram turns; level 3 (committee only) does not."""
    return level() != 3
