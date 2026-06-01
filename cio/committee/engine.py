"""
engine.py — Committee orchestration engine.

Single LLM entry point: ask_role (monkeypatchable for tests).
All specialists run sequentially; no asyncio.gather (subscription limit).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from .bundle import gather_bundle, format_bundle
from .roles import SPECIALISTS, MODERATOR_SYSTEM, CIO_SYSTEM

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM entry point — THE single point tests can monkeypatch
# ---------------------------------------------------------------------------

def _is_limit_notice(text: str) -> bool:
    """
    Return True only for short rate-limit / session-limit notices from Claude.

    The length guard (>400 chars → False) ensures a real analyst answer that merely
    contains the word "limit" is never silently dropped.
    """
    t = text.strip().lower()
    if len(t) > 400:                      # real analyst answers are long; notices are short
        return False
    return any(p in t for p in (
        "you've hit your", "session limit", "usage limit",
        "rate limit", "resets ", "try again later"))


async def ask_role(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """
    One-shot LLM query using the subscription claude-agent-sdk client.

    Returns the assistant text, or "" on any failure (offline-safe).
    No mcp_servers; no tools — specialists reason over the injected DATA block only.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    resolved_model = (
        model
        or os.getenv("CIO_COMMITTEE_MODEL")
        or os.getenv("CIO_MODEL")
        or None
    )

    opts = ClaudeAgentOptions(
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        allowed_tools=[],
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
        model=resolved_model,
    )

    try:
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        await client.query(user_prompt)
        parts: list[str] = []
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for blk in msg.content:
                    if isinstance(blk, TextBlock):
                        parts.append(blk.text)
        await client.disconnect()
        collected = "\n".join(parts).strip()
        if _is_limit_notice(collected):
            log.warning("ask_role hit a limit notice; treating as empty")
            return ""
        return collected
    except Exception as e:
        log.warning("ask_role failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# YAML parsing — tolerant
# ---------------------------------------------------------------------------

def parse_yaml_block(text: str) -> dict:
    """
    Extract the last ```yaml fenced block from *text* and parse it.

    On any parse error, or if no yaml block is found, returns {"_raw": text}.
    Never raises.
    """
    try:
        # Find all yaml fenced blocks
        import re
        blocks = re.findall(r"```yaml\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if not blocks:
            return {"_raw": text}
        raw_yaml = blocks[-1].strip()
        result = yaml.safe_load(raw_yaml)
        if isinstance(result, dict):
            return result
        return {"_raw": text}
    except Exception:
        return {"_raw": text}


# ---------------------------------------------------------------------------
# Specialist runner
# ---------------------------------------------------------------------------

async def run_specialist(role: dict, bundle_text: str, symbol: str) -> dict:
    """
    Run one specialist and parse its yaml output.

    Returns a dict containing: key, title, vote, confidence, reason,
    plus all role-specific fields, and _raw for debugging.
    """
    fields_list = ", ".join(role["fields"])
    user_prompt = (
        f"You are analyzing: {symbol}\n\n"
        f"Required output fields: {fields_list}, vote, confidence, reason\n\n"
        f"DATA:\n{bundle_text}"
    )

    raw = await ask_role(role["system_prompt"], user_prompt)
    parsed = parse_yaml_block(raw)

    result = {
        "key": role["key"],
        "title": role["title"],
        "vote": parsed.get("vote", "HOLD"),
        "confidence": parsed.get("confidence", 50),
        "reason": parsed.get("reason", parsed.get("_raw", "")),
        "_raw": raw,
    }
    # Merge role-specific fields
    for f in role["fields"]:
        result[f] = parsed.get(f)

    return result


# ---------------------------------------------------------------------------
# Vote tally helper (deterministic, Python-side cross-check)
# ---------------------------------------------------------------------------

def _compute_vote_tally(opinions: list[dict]) -> dict:
    """Count BUY/HOLD/SELL votes and compute confidence-weighted mean."""
    counts: dict[str, int] = {"BUY": 0, "HOLD": 0, "SELL": 0}
    total_weight = 0.0
    weighted_score = 0.0  # BUY=1, HOLD=0, SELL=-1

    score_map = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0,
                 "STRONG BUY": 1.0, "STRONG SELL": -1.0}

    for op in opinions:
        vote = str(op.get("vote", "HOLD")).upper()
        conf = float(op.get("confidence", 50))
        # Normalise vote key
        base_vote = vote.replace("STRONG ", "")
        if base_vote not in counts:
            base_vote = "HOLD"
        counts[base_vote] += 1
        weight = conf / 100.0
        total_weight += weight
        weighted_score += weight * score_map.get(vote, 0.0)

    avg_conf_score = weighted_score / total_weight if total_weight else 0.0

    # Map avg_conf_score to recommendation
    if avg_conf_score >= 0.6:
        rec = "Strong Buy"
    elif avg_conf_score >= 0.2:
        rec = "Buy"
    elif avg_conf_score >= -0.2:
        rec = "Hold"
    elif avg_conf_score >= -0.6:
        rec = "Sell"
    else:
        rec = "Strong Sell"

    return {
        "buy_count": counts["BUY"],
        "hold_count": counts["HOLD"],
        "sell_count": counts["SELL"],
        "confidence_weighted_score": round(avg_conf_score, 3),
        "tally_recommendation": rec,
    }


# ---------------------------------------------------------------------------
# CommitteeResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class CommitteeResult:
    symbol: str
    resolved: str | None
    as_of: str
    bundle: dict
    opinions: list[dict] = field(default_factory=list)
    consensus: dict = field(default_factory=dict)
    vote_tally: dict = field(default_factory=dict)
    cio: dict = field(default_factory=dict)
    error: str | None = None
    round1_opinions: list[dict] = field(default_factory=list)
    debate: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_committee(symbol: str, debate: bool | None = None) -> CommitteeResult:
    """
    Run the full committee pipeline for *symbol*:
      1. gather_bundle
      2. specialists (sequential) — Round 1
      3. debate (Round 2 cross-exam + Round 3 revisions) — optional, bounded
      4. moderator consensus on Round 3 votes
      5. CIO final decision on Round 3 votes

    debate=None reads CIO_DEBATE env var (default "on").
    Returns CommitteeResult; never raises.
    """
    # Step 1 — data
    bundle = gather_bundle(symbol)
    if bundle.get("resolved") is None:
        return CommitteeResult(
            symbol=symbol,
            resolved=None,
            as_of=bundle.get("as_of", ""),
            bundle=bundle,
            error=f"no data for {symbol}",
        )

    resolved = bundle["resolved"]
    as_of = bundle.get("as_of", "")
    bundle_text = format_bundle(bundle)
    is_etf = bundle.get("is_etf", False)

    # Step 2 — specialists (sequential)
    opinions: list[dict] = []
    for role in SPECIALISTS:
        if role["key"] == "etf" and not is_etf:
            continue  # Skip ETF specialist for non-ETF securities
        try:
            opinion = await run_specialist(role, bundle_text, resolved)
        except Exception as e:
            log.warning("Specialist %s failed: %s", role["key"], e)
            opinion = {
                "key": role["key"],
                "title": role["title"],
                "vote": "HOLD",
                "confidence": 0,
                "reason": f"Error: {e}",
                "_raw": "",
            }
            for f in role["fields"]:
                opinion[f] = None
        opinions.append(opinion)

    # Step 3 — debate (Round 2 cross-exam + Round 3 revisions)
    if debate is None:
        debate = os.getenv("CIO_DEBATE", "on").lower() != "off"

    round1_opinions = list(opinions)
    debate_result: dict = {"skipped": True, "pairs": [], "exchanges": [], "round3_opinions": opinions}

    if debate:
        # Check genuine disagreement before importing debate module
        unique_base = {str(op.get("vote", "HOLD")).upper().replace("STRONG ", "") for op in opinions}
        if len(unique_base) > 1:
            from .debate import run_debate
            roles_by_key = {r["key"]: r for r in SPECIALISTS}
            debate_result = await run_debate(opinions, bundle_text, resolved, roles_by_key)
            opinions = debate_result.get("round3_opinions", opinions)
        # else: all same vote — debate_result stays skipped

    # Step 4 — consensus (moderator LLM + deterministic tally) on final (Round 3) votes
    vote_tally = _compute_vote_tally(opinions)

    opinions_summary = "\n\n".join(
        f"[{op['title']}]\nvote: {op.get('vote')}\nconfidence: {op.get('confidence')}\nreason: {op.get('reason')}"
        for op in opinions
    )
    moderator_prompt = (
        f"Symbol: {resolved}\n\n"
        f"Specialist votes and reasoning:\n{opinions_summary}\n\n"
        f"Required output fields: committee_recommendation, agreement_score, "
        f"majority_view, minority_view, key_disagreements"
    )
    mod_raw = await ask_role(MODERATOR_SYSTEM, moderator_prompt)
    consensus = parse_yaml_block(mod_raw)

    # Step 4 — CIO (strongest available model)
    cio_model = os.getenv("CIO_FINAL_MODEL") or os.getenv("CIO_MODEL") or None

    cio_prompt = (
        f"Symbol: {resolved}\n\n"
        f"DATA SUMMARY:\n{bundle_text}\n\n"
        f"COMMITTEE OPINIONS:\n{opinions_summary}\n\n"
        f"CONSENSUS:\n{mod_raw}\n\n"
        f"Required output fields: final_recommendation, confidence_score, risk_rating, "
        f"time_horizon, base_case, bull_case, bear_case, scenarios"
    )
    cio_raw = await ask_role(CIO_SYSTEM, cio_prompt, model=cio_model)
    cio = parse_yaml_block(cio_raw)

    return CommitteeResult(
        symbol=symbol,
        resolved=resolved,
        as_of=as_of,
        bundle=bundle,
        opinions=opinions,
        consensus=consensus,
        vote_tally=vote_tally,
        cio=cio,
        round1_opinions=round1_opinions,
        debate=debate_result,
    )
