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

# Statuses that mean "the host refused a bot", NOT "the page is dead". A stdlib
# urllib request announces a non-browser UA + no JS challenge solve, so anti-bot
# WAFs (DataDome/Cloudflare) answer these even when the page is perfectly live in
# a real browser. conv_turns 488/489: marketscreener returned 403 to the harness
# yet opened fine for the operator. We escalate these (and only these) to a real
# headless browser before deciding a URL is dead.
ANTI_BOT_STATUSES = frozenset({401, 403, 429, 503})


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
    """Default resolver: stdlib HEAD/GET, then escalate anti-bot refusals to a
    real headless browser. Returns an HTTP status code, or None if unreachable.

    The cheap stdlib path decides liveness for the common case. Only when it hits
    an ANTI_BOT_STATUS (a refusal, not a 404) do we pay the browser cost, so a
    normal turn never spins up Chromium. Browser escalation is off unless
    CIO_CITATION_BROWSER is truthy *and* playwright is importable; it always
    fails safe to the stdlib status. Replaceable in tests with a dict-backed
    fake (the injected resolver bypasses all of this)."""
    s = _stdlib_status(url, timeout)
    if s in ANTI_BOT_STATUSES and _browser_enabled():
        b = browser_resolver(url)
        if b is not None:
            return b
    return s


def _stdlib_status(url: str, timeout: float = 4.0) -> int | None:
    """HEAD then GET fallback via stdlib (some hosts reject HEAD). No deps."""
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


def _browser_enabled() -> bool:
    import os
    return os.getenv("CIO_CITATION_BROWSER", "").strip().lower() in {
        "1", "true", "yes", "on"}


# A real desktop-Chrome UA; the default headless string is itself a bot tell.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def browser_resolver(url: str, timeout: float = 20.0) -> int | None:
    """Resolve liveness with a headless Chromium (playwright) + light stealth.

    Returns the real navigation status (e.g. 200) when the page loads and yields
    content, else None. Runs in its own thread so it is safe to call from inside
    an asyncio event loop (playwright's sync API refuses a running loop, and the
    harness may be driven from async code). Any failure — playwright missing,
    launch error, timeout — returns None so the caller keeps the stdlib status.
    """
    import threading

    result: dict[str, "int | None"] = {"status": None}

    def _run() -> None:
        result["status"] = _playwright_status(url, timeout)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout + 5.0)
    if t.is_alive():  # hung navigation — abandon the thread, treat as unknown
        return None
    return result["status"]


def _playwright_status(url: str, timeout: float) -> int | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            # Real Chrome (channel="chrome") clears anti-bot WAFs that block the
            # bundled chromium (Akamai/DataDome); fall back if it isn't installed.
            _args = dict(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                browser = p.chromium.launch(channel="chrome", **_args)
            except Exception:
                browser = p.chromium.launch(**_args)
            try:
                ctx = browser.new_context(
                    user_agent=_BROWSER_UA,
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                # navigator.webdriver === true is the classic headless giveaway.
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                page = ctx.new_page()
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout * 1000)
                status = resp.status if resp else None
                # A WAF may serve a 200 challenge shell; require real body too.
                try:
                    body = page.content()
                except Exception:
                    body = ""
                if status is not None and 200 <= status < 400 and len(body) > 500:
                    return status
                return status
            finally:
                browser.close()
    except Exception:
        return None
