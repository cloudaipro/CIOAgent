"""ClinicalTrials.gov primary-source data (API v2).

Fetches trial data directly from clinicaltrials.gov — the ONLY authoritative
registry for trial phase, indication, endpoint, and status. No API key required.

Config (env):
  CIO_CT_TIMEOUT  request timeout seconds (default 20s)

Offline-safe: any failure returns [] without raising; never breaks a turn.
"""
from __future__ import annotations

import logging
import os

from ._http import RateLimiter, get_json

log = logging.getLogger(__name__)

_BASE = "https://clinicaltrials.gov/api/v2/studies"
# Free public API — be polite: 1 req/s.
_limiter = RateLimiter(1.0)


def _timeout() -> float:
    try:
        return float(os.getenv("CIO_CT_TIMEOUT", "20"))
    except ValueError:
        return 20.0


def _parse_study(study: dict) -> dict | None:
    """Extract the fields we care about from one study node."""
    try:
        proto = study.get("protocolSection") or {}
        id_mod = proto.get("identificationModule") or {}
        status_mod = proto.get("statusModule") or {}
        desc_mod = proto.get("descriptionModule") or {}
        design_mod = proto.get("designModule") or {}
        cond_mod = proto.get("conditionsModule") or {}
        arms_mod = proto.get("armsInterventionsModule") or {}

        nct_id = id_mod.get("nctId") or ""
        title = (id_mod.get("briefTitle") or desc_mod.get("briefSummary") or "").strip()
        phase_raw = (design_mod.get("phases") or [])
        phase = ", ".join(phase_raw) if phase_raw else ""
        status = (status_mod.get("overallStatus") or "").strip()
        conditions = cond_mod.get("conditions") or []
        interventions = [
            (iv.get("name") or "").strip()
            for iv in (arms_mod.get("interventions") or [])
            if isinstance(iv, dict)
        ]
        if not nct_id:
            return None
        return {
            "nct_id": nct_id,
            "title": title,
            "phase": phase,
            "status": status,
            "conditions": conditions,
            "interventions": interventions,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
        }
    except Exception as e:
        log.debug("clinicaltrials parse failed: %s", e)
        return None


def search_trials(query: str, limit: int = 5) -> list[dict]:
    """Search clinicaltrials.gov for studies matching *query*.

    Returns a list of up to *limit* dicts:
      {nct_id, title, phase, status, conditions, interventions, url}

    Returns [] on any error (offline-safe, no key required).
    """
    if not query or not query.strip():
        return []
    try:
        limit = max(1, min(int(limit), 20))
    except (ValueError, TypeError):
        limit = 5

    params = {
        "query.term": query.strip(),
        "pageSize": limit,
        "format": "json",
    }
    data = get_json(_BASE, params=params, timeout=_timeout(), limiter=_limiter)
    if not isinstance(data, dict):
        return []

    studies = data.get("studies") or []
    out: list[dict] = []
    for s in studies:
        parsed = _parse_study(s)
        if parsed:
            out.append(parsed)
        if len(out) >= limit:
            break
    return out
