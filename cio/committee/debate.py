"""
debate.py — Bounded multi-round debate engine (PRD §7.2 rounds 2-3).

Round 1 opinions come from engine.py specialists (already built).
Round 2: targeted cross-examination — challenger rebuts opponent, opponent defends.
Round 3: all specialists may revise their vote/confidence/reason.

All LLM calls go through engine.ask_role.  Cross-exam PAIRS and round-3 revisions
run in parallel by default (mirroring the engine's CIO_PARALLEL setting).
Bounded: max_pairs (default 2) cross-exams + ≤8 revision calls.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ask_role / parse_yaml_block — imported at module level so tests can
# monkeypatch cio.committee.debate.ask_role without a circular-import issue.
# engine.py imports debate lazily (inside run_committee body), so this top-level
# import is safe once the package is initialised.
# ---------------------------------------------------------------------------
from cio.committee.engine import ask_role, parse_yaml_block, _gather_bounded  # noqa: E402


# ---------------------------------------------------------------------------
# Vote scoring (BUY=+1, HOLD=0, SELL=-1)
# ---------------------------------------------------------------------------

_SCORE: dict[str, float] = {
    "BUY": 1.0, "STRONG BUY": 1.0,
    "HOLD": 0.0,
    "SELL": -1.0, "STRONG SELL": -1.0,
}


def _vote_score(opinion: dict) -> float:
    vote = str(opinion.get("vote", "HOLD")).upper()
    score = _SCORE.get(vote, 0.0)
    # Tiebreak: higher confidence is "more extreme" (abs direction amplified by conf)
    conf = float(opinion.get("confidence", 50)) / 100.0
    return score * (1.0 + conf * 0.01)  # tiny nudge so conf breaks ties only


# ---------------------------------------------------------------------------
# Pair selection — deterministic, no LLM
# ---------------------------------------------------------------------------

def select_debate_pairs(
    opinions: list[dict],
    max_pairs: int | None = None,
) -> list[tuple[dict, dict]]:
    """
    Build (challenger=bear, target=bull) pairs.

    1. Core pair: most-bearish vs most-bullish.
    2. PRD-mandated pair: risk challenges valuation — only if both present and votes differ.
    Dedupe; skip self-pairs and identical-vote pairs.  Cap at max_pairs.

    Returns [] when all votes are identical (debate degrades to no-op).
    """
    if max_pairs is None:
        max_pairs = int(os.getenv("CIO_DEBATE_MAX_PAIRS", "2"))

    if not opinions or max_pairs <= 0:
        return []

    # Check for genuine disagreement
    unique_base_votes = {str(op.get("vote", "HOLD")).upper().replace("STRONG ", "") for op in opinions}
    if len(unique_base_votes) <= 1:
        return []

    # Sort ascending by score: most bearish first, most bullish last
    scored = sorted(opinions, key=_vote_score)
    most_bearish = scored[0]
    most_bullish = scored[-1]

    pairs: list[tuple[dict, dict]] = []
    seen: set[tuple[str, str]] = set()

    def _add(bear: dict, bull: dict) -> None:
        bk = bear.get("key", "")
        tk = bull.get("key", "")
        if bk == tk:
            return
        if (bk, tk) in seen:
            return
        # Skip if same vote
        if str(bear.get("vote", "HOLD")).upper() == str(bull.get("vote", "HOLD")).upper():
            return
        seen.add((bk, tk))
        pairs.append((bear, bull))

    # 1. Core pair
    _add(most_bearish, most_bullish)

    # 2. PRD-mandated: risk challenges valuation
    risk_op = next((op for op in opinions if op.get("key") == "risk"), None)
    val_op = next((op for op in opinions if op.get("key") == "valuation"), None)
    if risk_op and val_op:
        rv_score = _vote_score(risk_op)
        vv_score = _vote_score(val_op)
        if rv_score != vv_score:
            # risk challenges valuation: risk=challenger, valuation=target
            _add(risk_op, val_op)

    return pairs[:max_pairs]


# ---------------------------------------------------------------------------
# Round 2 — cross-examination (free text, no yaml)
# ---------------------------------------------------------------------------

async def run_cross_exam(
    pair: tuple[dict, dict],
    bundle_text: str,
    symbol: str,
    roles_by_key: dict[str, dict] | None = None,
) -> dict:
    """
    Run one cross-examination exchange between challenger and target.

    *pair* holds opinion dicts (vote/confidence/reason). The role system prompts
    live in *roles_by_key* (opinions do not carry them) — resolve via the opinion
    "key". Returns {challenger_key, challenger_title, target_key, target_title,
             challenge, response}.  Never raises.

    Challenge and response are serial within the pair (response depends on challenge).
    """
    challenger, target = pair
    roles_by_key = roles_by_key or {}

    def _sys(opinion: dict) -> str:
        role = roles_by_key.get(opinion.get("key", ""), {})
        return role.get("system_prompt", "")

    try:
        challenge_prompt = (
            f"Symbol under analysis: {symbol}\n\n"
            f"Your opposing committee member is the {target['title']} analyst.\n"
            f"Their position:\n"
            f"  Vote: {target.get('vote', 'HOLD')}\n"
            f"  Confidence: {target.get('confidence', 50)}\n"
            f"  Reason: {target.get('reason', '')}\n\n"
            f"DATA SUMMARY:\n{bundle_text}\n\n"
            f"Deliver a pointed rebuttal (≤120 words) citing DATA where possible. "
            f"Free text — no yaml needed."
        )
        challenge = await ask_role(_sys(challenger), challenge_prompt, role_key=challenger.get("key"))
    except Exception as e:
        log.warning("Challenge call failed (%s→%s): %s", challenger.get("key"), target.get("key"), e)
        challenge = ""

    try:
        response_prompt = (
            f"Symbol under analysis: {symbol}\n\n"
            f"Your opposing committee member ({challenger['title']}) has challenged your position:\n\n"
            f"{challenge}\n\n"
            f"Defend or concede (≤120 words). Free text — no yaml needed."
        )
        response = await ask_role(_sys(target), response_prompt, role_key=target.get("key"))
    except Exception as e:
        log.warning("Response call failed (%s defends): %s", target.get("key"), e)
        response = ""

    return {
        "challenger_key": challenger.get("key", ""),
        "challenger_title": challenger.get("title", ""),
        "target_key": target.get("key", ""),
        "target_title": target.get("title", ""),
        "challenge": challenge,
        "response": response,
    }


# ---------------------------------------------------------------------------
# Round 3 — revision (yaml vote contract, same shape as Round 1)
# ---------------------------------------------------------------------------

async def revise_opinion(
    role: dict,
    round1_opinion: dict,
    debate_text: str,
    bundle_text: str,
    symbol: str,
) -> dict:
    """
    Re-poll one specialist with the full debate transcript.

    Returns a fresh opinion dict in the same shape as Round 1.
    Falls back to round1_opinion on any failure (never raises).
    """
    fields_list = ", ".join(role.get("fields", []))
    revision_prompt = (
        f"You are analyzing: {symbol}\n\n"
        f"DEBATE TRANSCRIPT:\n{debate_text}\n\n"
        f"DATA:\n{bundle_text}\n\n"
        f"Your Round 1 position:\n"
        f"  Vote: {round1_opinion.get('vote', 'HOLD')}\n"
        f"  Confidence: {round1_opinion.get('confidence', 50)}\n"
        f"  Reason: {round1_opinion.get('reason', '')}\n\n"
        f"Having seen the full committee debate, you may revise your position or hold it. "
        f"Output your FINAL position using the same yaml contract as Round 1.\n"
        f"Required output fields: {fields_list}, vote, confidence, reason"
    )

    try:
        raw = await ask_role(role["system_prompt"], revision_prompt, role_key=role.get("key"))
    except Exception as e:
        log.warning("revise_opinion failed for %s: %s", role.get("key"), e)
        return round1_opinion

    if not raw:
        return round1_opinion

    parsed = parse_yaml_block(raw)
    if "_raw" in parsed and len(parsed) == 1:
        # Parse failed — keep Round 1
        return round1_opinion

    result = {
        "key": role.get("key", round1_opinion.get("key", "")),
        "title": role.get("title", round1_opinion.get("title", "")),
        "vote": parsed.get("vote", round1_opinion.get("vote", "HOLD")),
        "confidence": parsed.get("confidence", round1_opinion.get("confidence", 50)),
        "reason": parsed.get("reason", round1_opinion.get("reason", "")),
        "_raw": raw,
        # Preserve the revised TIRF deliverables for the research report; fall back
        # to the Round-1 parse if this revision omitted them.
        "_parsed": parsed if parsed and "_raw" not in parsed else round1_opinion.get("_parsed", parsed),
    }
    for f in role.get("fields", []):
        result[f] = parsed.get(f, round1_opinion.get(f))

    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_debate(
    round1_opinions: list[dict],
    bundle_text: str,
    symbol: str,
    roles_by_key: dict[str, dict],
    parallel: bool = True,
) -> dict:
    """
    Orchestrate the bounded debate:
      1. select_debate_pairs
      2. If no pairs → skip.
      3. Cross-exam PAIRS run in parallel (each pair internally serial).
      4. Build debate_text transcript.
      5. Round-3 revisions run in parallel.
      6. Return result dict.

    Never raises.  On sub-failure keeps the Round 1 opinion for that role.
    """
    max_pairs = int(os.getenv("CIO_DEBATE_MAX_PAIRS", "2"))
    pairs = select_debate_pairs(round1_opinions, max_pairs)

    if not pairs:
        return {
            "pairs": [],
            "exchanges": [],
            "round3_opinions": round1_opinions,
            "skipped": True,
        }

    # Round 2: cross-exam pairs — pairs are independent; each pair is internally serial
    exchanges: list[dict] = await _gather_bounded(
        [run_cross_exam(pair, bundle_text, symbol, roles_by_key) for pair in pairs],
        parallel=parallel,
    )

    # Build debate transcript
    transcript_parts: list[str] = []
    for ex in exchanges:
        transcript_parts.append(
            f"[{ex['challenger_title']} challenges {ex['target_title']}]\n"
            f"{ex['challenge']}\n\n"
            f"[{ex['target_title']} responds]\n"
            f"{ex['response']}"
        )
    debate_text = "\n\n---\n\n".join(transcript_parts)

    # Lightweight pair info for result (avoid storing full opinion dicts twice)
    pairs_info = [
        {
            "challenger_key": ch.get("key"),
            "challenger_title": ch.get("title"),
            "target_key": tg.get("key"),
            "target_title": tg.get("title"),
        }
        for ch, tg in pairs
    ]

    # Round 3: revisions — all Round 1 specialists in parallel
    async def _revise_safe(op: dict) -> dict:
        key = op.get("key", "")
        role = roles_by_key.get(key)
        if role is None:
            return op
        return await revise_opinion(role, op, debate_text, bundle_text, symbol)

    round3_opinions: list[dict] = await _gather_bounded(
        [_revise_safe(op) for op in round1_opinions],
        parallel=parallel,
    )

    return {
        "pairs": pairs_info,
        "exchanges": exchanges,
        "round3_opinions": round3_opinions,
        "skipped": False,
    }
