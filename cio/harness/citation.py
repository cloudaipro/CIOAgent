"""citation.py — V2: fetch-before-cite / URL liveness verification.

Why this exists: in conv_turns 347 the agent cited a CNBC URL that 404'd — a
plausible-looking source it effectively invented ("CNBC URL 猜錯 404"). The
Evidence Integrity Policy (source_policy.py) already classifies the *kind* of a
source, but a fabricated URL on a reputable host (cnbc.com) would still pass
tiering. The missing check is liveness: does the cited page actually resolve?

This module resolves every cited URL and fails closed: a dead URL can neither be
cited nor contribute to material-fact corroboration, even if its host is Tier-2.
It REUSES source_policy for tiering — there is no second copy of the trust rules.
The HTTP resolver is injected so the core is deterministic and offline-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from cio.data import source_policy
from cio.data.source_policy import Tier, ClaimClass
from .models import CitationVerdict, CitationReport, Finding, Severity

Resolver = Callable[[str], "int | None"]


@dataclass
class Citation:
    url: str = ""
    claim_class: ClaimClass | None = None
    backs_material: bool = False


def verify_citations(
    citations: list[Citation],
    resolver: Resolver | None = None,
    issuer_domains: set[str] | None = None,
) -> CitationReport:
    """Verify a set of citations. Returns a CitationReport (never raises).

    A citation is OK iff its URL is live AND its tier is acceptable for how it is
    used. Material facts apply the source_policy corroboration rule over LIVE
    sources only.
    """
    resolve = resolver or http_resolver
    verdicts: list[CitationVerdict] = []
    findings: list[Finding] = []

    for cit in citations or []:
        host = _host(cit.url)
        tier = source_policy.classify(host, issuer_domains)
        try:
            status = resolve(cit.url)
        except Exception:
            status = None
        live = status is not None and 200 <= status < 400
        ok = live and tier != Tier.LOW_TRUST if cit.backs_material else live

        reason = ""
        if not live:
            reason = f"dead/unreachable (status={status})"
            findings.append(Finding(
                code="C_DEAD_URL",
                severity=Severity.BLOCK,
                message=f"Cited URL does not resolve: {cit.url} (status={status}).",
                fix="Drop the citation and re-source from a live page, or remove the claim it backs.",
                detail={"url": cit.url, "status": status, "host": host},
            ))
        elif cit.backs_material and tier == Tier.LOW_TRUST:
            reason = "live but Tier-3 cannot back a material fact"

        verdicts.append(CitationVerdict(
            url=cit.url, host=host, tier=int(tier), tier_label=tier.name,
            http_status=status, live=live, ok=ok,
            backs_material=cit.backs_material, reason=reason,
        ))

    material_verified = _check_material(citations, verdicts, findings)
    return CitationReport(verdicts=verdicts, findings=findings,
                          material_verified=material_verified)


def _check_material(citations, verdicts, findings) -> bool:
    """Apply source_policy.is_verified over LIVE sources backing material facts.

    Returns True if there are no material claims, or if the live sources satisfy
    corroboration. If material claims exist but live sources don't satisfy it,
    append a BLOCK finding and return False.
    """
    live_material_tiers = [
        Tier(v.tier) for v in verdicts if v.backs_material and v.live
    ]
    has_material = any(c.backs_material for c in (citations or []))
    if not has_material:
        return True
    verified = source_policy.is_verified(live_material_tiers)
    if not verified:
        findings.append(Finding(
            code="C_MATERIAL_UNVERIFIED",
            severity=Severity.BLOCK,
            message=("Material fact not corroborated by live sources "
                     "(need >=1 Tier-1 or >=2 Tier-2, dead URLs excluded)."),
            fix="Add a live primary source or a second independent live Tier-2 source.",
            detail={"live_material_tiers": [int(t) for t in live_material_tiers]},
        ))
    return verified


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def http_resolver(url: str, timeout: float = 4.0) -> int | None:
    """Default resolver: stdlib only, HEAD then GET fallback. Returns the HTTP
    status code, or None if unreachable. No third-party deps; replaceable in
    tests with a dict-backed fake. (Liveness ~ a HEAD; some hosts reject HEAD,
    hence the GET fallback.)"""
    import urllib.request
    import urllib.error

    def _try(method: str) -> int | None:
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": "CIOAgent-harness/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None

    s = _try("HEAD")
    if s is None or s >= 400:
        g = _try("GET")
        if g is not None:
            return g
    return s
