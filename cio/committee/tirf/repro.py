"""
repro.py — Reproducibility framework (PRD §9 / proposal §11).

Pins the *inputs and method* of a committee run so it can be replayed and audited:
a canonical JSON snapshot of the data bundle + its sha256, plus the prompt, agent,
and research version stamps. Deterministic and never-raises.

Version constants live here (the reproducibility module owns versioning). Bump
PROMPT_VERSION whenever specialist/CIO prompts change; bump AGENT_VERSION whenever
the roster or pipeline logic changes — so an old report's pins explain any drift.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Bump these deliberately when prompts / pipeline change (PRD §9).
PROMPT_VERSION = "tirf-1.0"
AGENT_VERSION = "committee-1.0"

# Only the deterministic, decision-relevant parts of the bundle are snapshotted.
# Volatile/derived display fields are excluded so the hash is stable for identical
# market inputs.
_SNAPSHOT_KEYS = ("resolved", "quote", "fundamentals", "ta_signals",
                  "is_etf", "filings", "analyst", "earnings")


def _canonical(obj: Any) -> Any:
    """Recursively sort dict keys so JSON serialisation is order-independent."""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    return obj


def data_snapshot(bundle: dict) -> str:
    """Return a canonical JSON string of the decision-relevant bundle fields.

    Stable across dict ordering and re-runs for identical inputs. Never raises;
    on serialisation trouble falls back to a best-effort string.
    """
    try:
        sub = {k: bundle.get(k) for k in _SNAPSHOT_KEYS}
        return json.dumps(_canonical(sub), sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        log.debug("data_snapshot fallback", exc_info=True)
        try:
            return json.dumps({"resolved": str(bundle.get("resolved"))})
        except Exception:
            return "{}"


def data_hash(snapshot: str) -> str:
    """sha256 of a snapshot string (hex)."""
    try:
        return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def manifest(bundle: dict, research_version: int = 0) -> dict:
    """Build the reproducibility manifest for a run (PRD §9 table)."""
    snap = data_snapshot(bundle)
    return {
        "data_snapshot": snap,
        "data_hash": data_hash(snap),
        "prompt_version": PROMPT_VERSION,
        "agent_version": AGENT_VERSION,
        "research_version": research_version,
        "as_of": bundle.get("as_of", ""),
    }


def verify(bundle: dict, expected_hash: str) -> bool:
    """Recompute the snapshot hash for *bundle* and compare to *expected_hash*.

    The reproducibility check (PRD §9): identical inputs ⇒ identical hash. Never
    raises; returns False on any trouble or mismatch.
    """
    try:
        return bool(expected_hash) and data_hash(data_snapshot(bundle)) == expected_hash
    except Exception:
        return False
