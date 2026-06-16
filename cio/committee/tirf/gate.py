"""Four-layer gate (swing upgrade #2, 2026-06).

Splits a specialist's evidence into four causal layers and scores each one
*independently*, then applies an AND-gate so one strong layer cannot mask a weak
one. This is the structural fix for the ROKU failure: a full-bull execution layer
(5/5 oscillators green) appeared right after an exhausting spike while the catalyst
was already spent — a blended composite hid that, layered scoring would not.

  catalyst  — WHY price should move (earnings, guidance, filings, regulatory)
  behavior  — is the move NOT yet priced (analyst revision delta, flow, positioning)
  momentum  — the one surviving academic "technical" alpha (RS, trend, 6-12m)
  execution — entry timing / stop / size (RSI, MACD, KDJ, Squeeze, Fisher) — never
              a signal, only a timing tool

Causal order matters: you enter on catalyst+behavior (the move is real and
un-priced) and merely *time* with momentum+execution. Pure, deterministic,
never-raises.
"""
from __future__ import annotations

LAYERS = ("catalyst", "behavior", "momentum", "execution")

# Per-layer pass thresholds (0..100 mean item score). Catalyst & behavior — the
# "why" layers — are held to a higher bar than the timing layers.
DEFAULT_THRESHOLDS = {
    "catalyst": 60.0,
    "behavior": 50.0,
    "momentum": 50.0,
    "execution": 40.0,
}
# Layers that MUST be present (not just clear a threshold). A trade with no
# catalyst evidence at all is the indicator-soup trap, regardless of timing.
_MANDATORY = ("catalyst",)


def layer_scores(items) -> dict:
    """Mean item_score per layer over a list of EvidenceItem. Missing layer omitted.

    Accepts EvidenceItem objects or plain dicts (``layer``/``item_score`` keys).
    """
    buckets: dict[str, list] = {ly: [] for ly in LAYERS}
    for it in items or []:
        layer = _get(it, "layer", "catalyst")
        score = _get(it, "item_score", 0)
        if layer not in buckets:
            layer = "catalyst"
        try:
            buckets[layer].append(float(score))
        except (TypeError, ValueError):
            continue
    out = {}
    for ly in LAYERS:
        vals = buckets[ly]
        if vals:
            out[ly] = round(sum(vals) / len(vals), 1)
    return out


def evaluate(scores: dict, thresholds: dict | None = None) -> dict:
    """AND-gate the per-layer scores. Returns a verdict dict.

    {pass: bool, blocked_by: [layers below threshold or missing-mandatory],
     missing: [layers with no evidence], scores: {...}, thresholds: {...}}

    A green layer NEVER compensates a red one — every present layer must clear its
    own threshold AND every mandatory layer must be present.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    scores = scores or {}
    missing = [ly for ly in LAYERS if ly not in scores]
    blocked: list[str] = []
    # Mandatory layers must exist.
    for ly in _MANDATORY:
        if ly not in scores:
            blocked.append(ly)
    # Every present layer must clear its threshold.
    for ly, val in scores.items():
        if ly in th and val < th[ly]:
            blocked.append(ly)
    blocked = sorted(set(blocked), key=LAYERS.index)
    return {
        "pass": not blocked,
        "blocked_by": blocked,
        "missing": missing,
        "scores": dict(scores),
        "thresholds": th,
    }


def gate_evidence(items, thresholds: dict | None = None) -> dict:
    """Convenience: layer_scores(items) -> evaluate(...). Carries scores through."""
    scores = layer_scores(items)
    verdict = evaluate(scores, thresholds)
    verdict["layer_scores"] = scores
    return verdict


def _get(obj, key, default):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
