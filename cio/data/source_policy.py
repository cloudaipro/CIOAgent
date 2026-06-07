"""Source-trust policy — the single authoritative artifact for evidence integrity.

LOCKED owner decision (2026-06-06). Every consumer (prompt builder, web tools,
verifier) imports from here — there is no second copy. Changing trust rules means
changing THIS file, and the test suite asserts the load-bearing cases.

Why this exists: the conversational agent once relayed a fabricated clinical
endpoint (MADRS -35% p<0.001) from monexa.ai — an AI aggregator that confused a
drug+indication. The URL was real and the model honestly cited a page it scraped.
The failure was trusting the wrong KIND of source for a material fact. This policy
makes a material fact only assertable from a primary, claim-appropriate, corroborated
origin; everything else must be visibly labelled inference.
"""
from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    PRIMARY = 1     # may back any material fact on its own
    REPUTABLE = 2   # price/market/M&A narrative; material fact only if corroborated
    LOW_TRUST = 3   # leads only — can NEVER back a stated fact


# Domain → tier. Matched by exact host or registrable-suffix (see classify()).
# Issuer's own domain is added at runtime per-symbol (resolved from EDGAR/Finnhub).
TIER_1_PRIMARY = {
    "sec.gov", "data.sec.gov",
    "clinicaltrials.gov",
    "fda.gov", "accessdata.fda.gov",
}
TIER_2_REPUTABLE = {
    "reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com",
    "cnbc.com", "barrons.com", "finance.yahoo.com", "yahoo.com",
    # PR-wire: company-authored but wire-distributed -> Tier 2 (owner decision).
    # A wire release alone can't back a material fact; needs the matching primary.
    "prnewswire.com", "businesswire.com", "globenewswire.com",
}
TIER_3_LOW_TRUST = {
    "monexa.ai", "fool.com", "zacks.com",
    "reddit.com", "stocktwits.com", "seekingalpha.com",
}

# Owner decision: unknown/unlisted domain FAILS CLOSED to Tier 3. This is what
# blocks the monexa.ai class of error — an unlisted aggregator can't back a fact.
DEFAULT_TIER = Tier.LOW_TRUST


def classify(host: str, issuer_domains: set[str] | None = None) -> Tier:
    """Return the trust Tier for a hostname. Fail-closed: unknown -> LOW_TRUST.

    `issuer_domains` promotes the company's own domain(s) to PRIMARY for this call
    (resolved per-symbol from the EDGAR/Finnhub profile). Any `*.ai` host that is
    not explicitly listed is forced LOW_TRUST (AI-aggregator catch).
    """
    h = (host or "").strip().lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    if issuer_domains and _suffix_match(h, issuer_domains):
        return Tier.PRIMARY
    if _suffix_match(h, TIER_1_PRIMARY):
        return Tier.PRIMARY
    if _suffix_match(h, TIER_2_REPUTABLE):
        return Tier.REPUTABLE
    if _suffix_match(h, TIER_3_LOW_TRUST):
        return Tier.LOW_TRUST
    if h.endswith(".ai"):          # unlisted AI-aggregator -> fail closed
        return Tier.LOW_TRUST
    return DEFAULT_TIER


def _suffix_match(host: str, domains: set[str]) -> bool:
    """True if host == d or is a subdomain of d, for any d in domains."""
    return any(host == d or host.endswith("." + d) for d in domains)


# --- Material-fact taxonomy -------------------------------------------------
# A claim of these classes is MATERIAL: it may be STATED only from a Tier-1
# (or claim-appropriate) source, and must meet the corroboration rule. Anything
# not material may be asserted as [inference] when explicitly labelled.
class ClaimClass(IntEnum):
    CLINICAL = 1            # trial phase, indication, endpoint, p-value, approval
    FINANCIAL = 2          # revenue, EPS, margin, guidance, cash, debt
    CORPORATE_ACTION = 3   # M&A terms/price/date, dividend, buyback, split
    REGULATORY = 4         # FDA decision, PDUFA date
    ANALYST = 5            # rating, price target (sourced, not model-set)


MATERIAL_CLASSES = frozenset({
    ClaimClass.CLINICAL, ClaimClass.FINANCIAL,
    ClaimClass.CORPORATE_ACTION, ClaimClass.REGULATORY, ClaimClass.ANALYST,
})

# Claim class -> the source(s) that are authoritative for it. A material fact
# sourced from outside its required set is "wrong class" -> unverified, even if
# the page is Tier 1 for some other class. (This is what catches a clinical
# endpoint cited from a finance aggregator.)
REQUIRED_SOURCE = {
    ClaimClass.CLINICAL: "clinicaltrials.gov / fda.gov / company release",
    ClaimClass.FINANCIAL: "EDGAR filing / company earnings release",
    ClaimClass.CORPORATE_ACTION: "company IR + one Tier-2 wire",
    ClaimClass.REGULATORY: "fda.gov / company release",
    ClaimClass.ANALYST: "Finnhub recs / Yahoo analyst panel",
}

# Price / valuation FIGURES never come from the web at all — stock tools only.
# (Enforced in agent prompt; listed here for the single source of truth.)
FIGURES_FROM_STOCK_TOOLS_ONLY = True


def is_verified(tiers: list[Tier]) -> bool:
    """Corroboration rule (owner decision): a material fact counts as verified iff
    >=1 Tier-1 source OR >=2 independent Tier-2 sources back it. Tier-3 never
    contributes. `tiers` = the tiers of the sources actually cited for the claim.
    """
    t1 = sum(1 for t in tiers if t == Tier.PRIMARY)
    t2 = sum(1 for t in tiers if t == Tier.REPUTABLE)
    return t1 >= 1 or t2 >= 2
