"""
cio.committee — Investment Committee engine.

Public API:
  run_committee(symbol)     -> CommitteeResult   (async)
  build_report(symbol, result) -> str            (sync markdown)
  gather_bundle(symbol)     -> dict              (sync)
"""
from .engine import run_committee, CommitteeResult
from .report import build_report
from .bundle import gather_bundle

__all__ = ["run_committee", "build_report", "gather_bundle", "CommitteeResult"]
