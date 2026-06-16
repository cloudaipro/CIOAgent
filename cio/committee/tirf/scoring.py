"""
scoring.py — Evidence Scoring Engine (PRD §6 / proposal §8).

Pure, deterministic, never-raises. Classifies an evidence item's free-text
source into a reliability tier, scores recency against the report's as_of date,
scores relevance, and blends them into a 0-100 item score.

Single source of truth for the weights (0.50 reliability / 0.30 relevance /
0.20 recency) and the tier tables — referenced verbatim by the PRD.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from . import gate
from .models import EvidenceItem, SourceRef, SpecialistResearch

log = logging.getLogger(__name__)

# --- Source reliability tiers (most specific keywords first) -----------------
# Order matters: the first tier whose keyword is found wins.
_RELIABILITY_TIERS: list[tuple[str, int, tuple[str, ...]]] = [
    ("SEC Filing", 100, ("10-k", "10-q", "8-k", "20-f", "6-k", "s-1",
                          "sec ", "sec\t", "edgar", "filing", "prospectus")),
    ("Earnings Call", 90, ("earnings call", "transcript", "conference call", "earnings transcript")),
    ("Company Guidance", 85, ("guidance", "investor day", "management", "press release", "company press")),
    ("Industry Research", 80, ("industry", "research report", "analyst", "semianalysis",
                               "gartner", "idc", "morningstar", "sell-side")),
    ("News Source", 60, ("news", "reuters", "bloomberg", "cnbc", "wsj", "ft ", "article",
                         "financial times", "barron")),
    ("Social Media", 20, ("twitter", "x.com", "reddit", "stocktwits", "social", "forum",
                          "discord", "telegram")),
]
_UNKNOWN_RELIABILITY = ("Unknown", 50)


def classify_source(source: str) -> tuple[str, int]:
    """Map a free-text source string to (tier_label, reliability_score).

    Case-insensitive keyword match, first tier wins. Empty/None → Unknown (50).
    """
    if not source:
        return _UNKNOWN_RELIABILITY
    s = f" {str(source).lower()} "
    for label, score, keys in _RELIABILITY_TIERS:
        for k in keys:
            if k in s:
                return (label, score)
    return _UNKNOWN_RELIABILITY


# --- Four-layer causal classification (swing upgrade #2) ----------------------
# Checked most-specific first; whatever doesn't match a timing/flow layer is, by
# default, a fundamental catalyst fact. Keep execution before momentum so a named
# oscillator ("RSI breakout") tags as the timing tool it is, not as momentum.
_LAYER_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("execution", ("rsi", "macd", "kdj", "stochastic", "squeeze", "fisher", "efi",
                   "vidya", "bollinger", "keltner", "atr", "oscillator", "stop-loss",
                   "stop loss", "overbought", "oversold", "golden cross", "death cross",
                   "moving average cross", "entry timing")),
    ("behavior", ("analyst revision", "estimate revision", "upgrade", "downgrade",
                  "price target", "institutional", "ownership", "13f", "short interest",
                  "fund flow", "positioning", "sentiment", "insider", "accumulation",
                  "distribution", "buy-side", "sell-side", "coverage", "re-rating",
                  "rerating")),
    ("momentum", ("relative strength", "rs rating", "momentum", "52-week", "52 week",
                  "breakout", "trend continuation", "uptrend", "new high",
                  "outperform", "price strength", "leadership")),
]
_DEFAULT_LAYER = "catalyst"


def classify_layer(*parts: str) -> str:
    """Tag evidence into catalyst|behavior|momentum|execution from its free text.

    Pass any text fragments (finding, source, impact...). First matching layer
    wins; no match → 'catalyst' (a fundamental fact). Never raises.
    """
    s = " " + " ".join(str(p or "").lower() for p in parts) + " "
    for layer, keys in _LAYER_KEYWORDS:
        for k in keys:
            if k in s:
                return layer
    return _DEFAULT_LAYER


# --- Recency -----------------------------------------------------------------

def _parse_date(value: str) -> date | None:
    """Tolerant ISO-ish date parse. Returns None on anything unparseable."""
    if not value:
        return None
    txt = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt[:len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    # Last resort: leading YYYY-MM-DD
    try:
        return datetime.strptime(txt[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def recency_score(evidence_date: str, as_of: str) -> int:
    """Score 100/80/60/30 by age of evidence relative to as_of (PRD §6).

    Undated or unparseable, or evidence dated *after* as_of (future) → 30.
    """
    d = _parse_date(evidence_date)
    ref = _parse_date(as_of) or date.today()
    if d is None:
        return 30
    age = (ref - d).days
    if age < 0:
        return 30          # future-dated relative to snapshot → treat as untrusted
    if age < 7:
        return 100
    if age < 30:
        return 80
    if age < 90:
        return 60
    return 30


# --- Relevance ---------------------------------------------------------------

_RELEVANCE_SCORE = {"direct": 100, "related": 70, "indirect": 40}


def relevance_score(level: str) -> int:
    """Map direct/related/indirect → 100/70/40 (default related=70)."""
    return _RELEVANCE_SCORE.get(str(level or "related").strip().lower(), 70)


# --- Composite ---------------------------------------------------------------

# Weights — the single documented source of truth (PRD §6).
W_RELIABILITY = 0.50
W_RELEVANCE = 0.30
W_RECENCY = 0.20


def score_item(item: EvidenceItem, as_of: str) -> EvidenceItem:
    """Fill an EvidenceItem's score fields in place and return it. Never raises."""
    try:
        tier, rel = classify_source(item.source)
        rec = recency_score(item.date, as_of)
        rlv = relevance_score(item.relevance)
        item.source_tier = tier
        item.reliability_score = rel
        item.recency_score = rec
        item.relevance_score = rlv
        item.item_score = round(W_RELIABILITY * rel + W_RELEVANCE * rlv + W_RECENCY * rec)
        item.layer = classify_layer(item.finding, item.source, item.impact)
    except Exception:
        log.debug("score_item failed for %r", getattr(item, "source", "?"), exc_info=True)
        item.source_tier = item.source_tier or "Unknown"
    return item


def score_specialist(sp: SpecialistResearch, as_of: str) -> SpecialistResearch:
    """Score every evidence item + each source ref, set aggregates. Never raises."""
    try:
        for ev in sp.evidence:
            score_item(ev, as_of)
        for sr in sp.sources:
            tier, rel = classify_source(sr.reference)
            sr.source_tier = tier
            sr.reliability_score = rel
        sp.evidence_count = len(sp.evidence)
        sp.counter_count = len(sp.counterarguments)
        if sp.evidence:
            sp.evidence_quality = round(
                sum(e.item_score for e in sp.evidence) / len(sp.evidence), 1)
        else:
            sp.evidence_quality = 0.0
        sp.layer_scores = gate.layer_scores(sp.evidence)
    except Exception:
        log.debug("score_specialist failed for %s", getattr(sp, "role_key", "?"), exc_info=True)
    return sp
