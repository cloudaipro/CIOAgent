"""
engine.py — Committee orchestration engine.

Single LLM entry point: ask_role (monkeypatchable for tests).
Supports two backends: claude-agent-sdk ("claude") and NVIDIA NIM ("nim").
Round-1 specialists, debate cross-exam pairs, and round-3 revisions run in
parallel by default (CIO_PARALLEL=on); moderator + CIO stay serial.
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
from . import agent_memory
from .models import resolve as _resolve_model, nim_settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parallel / concurrency config
# ---------------------------------------------------------------------------

PARALLEL = os.getenv("CIO_PARALLEL", "on").lower() not in ("off", "0", "false", "no")
MAX_CONC = int(os.getenv("CIO_MAX_CONCURRENCY", "8"))


async def _gather_bounded(coros, parallel: bool) -> list:
    """
    Run *coros* in order.  When parallel=True, runs under a bounded semaphore
    (MAX_CONC) via asyncio.gather preserving result order.  When parallel=False,
    awaits sequentially.  Never raises (coros are expected to catch internally).
    """
    if not parallel:
        results = []
        for coro in coros:
            results.append(await coro)
        return results

    sem = asyncio.Semaphore(MAX_CONC)

    async def _bounded(coro):
        async with sem:
            return await coro

    return list(await asyncio.gather(*[_bounded(c) for c in coros]))


# ---------------------------------------------------------------------------
# Limit-notice detection
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


# ---------------------------------------------------------------------------
# Claude backend
# ---------------------------------------------------------------------------

async def _ask_claude(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """
    One-shot LLM query using the subscription claude-agent-sdk client.

    Returns the assistant text, or "" on any failure (offline-safe).
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    resolved_model = model or os.getenv("CIO_MODEL") or None

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
            log.warning("_ask_claude hit a limit notice; treating as empty")
            return ""
        return collected
    except Exception as e:
        log.warning("_ask_claude failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# NIM backend (NVIDIA NIM, OpenAI-compatible via httpx)
# ---------------------------------------------------------------------------

async def _ask_nim(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """
    One-shot LLM query via NVIDIA NIM (OpenAI-compatible REST API).

    Reads the API key from the env var named by nim_settings()["api_key_env"].
    If the key is absent → log.warning + return "" (graceful, never raises).
    On any HTTP/parse error → log.warning + return "".
    """
    import httpx

    settings = nim_settings()
    key = os.getenv(settings["api_key_env"])
    if not key:
        log.warning(
            "_ask_nim: %s not set; skipping NIM call (returning empty)",
            settings["api_key_env"],
        )
        return ""

    nim_model = model or "minimaxai/minimax-m2.7"
    url = settings["base_url"].rstrip("/") + "/chat/completions"

    payload = {
        "model": nim_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 2048,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        if _is_limit_notice(text):
            log.warning("_ask_nim hit a limit notice; treating as empty")
            return ""
        return text
    except Exception as e:
        log.warning("_ask_nim failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# ask_role — single entry point (monkeypatchable)
# ---------------------------------------------------------------------------

async def ask_role(
    system_prompt: str,
    user_prompt: str,
    role_key: str | None = None,
    service: str | None = None,
    model: str | None = None,
) -> str:
    """
    Route one LLM call to the correct backend.

    Resolution order:
      1. Explicit ``service``/``model`` args win.
      2. Config resolution via ``role_key`` (models.resolve).
      3. Fall back to ("claude", None) when role_key is None (legacy callers).

    Returns the assistant text, or "" on any failure (offline-safe).
    """
    if service is None:
        if role_key is not None:
            service, resolved_model = _resolve_model(role_key)
        else:
            service, resolved_model = "claude", None
    else:
        # service was explicit; model may still need resolving
        if model is None and role_key is not None:
            _, resolved_model = _resolve_model(role_key)
        else:
            resolved_model = model

    # Explicit model arg always wins over config-resolved
    effective_model = model if model is not None else resolved_model

    if service == "nim":
        return await _ask_nim(system_prompt, user_prompt, effective_model)
    return await _ask_claude(system_prompt, user_prompt, effective_model)


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
    memory_note stays in the dict for the agent but is NOT rendered in build_report.
    """
    fields_list = ", ".join(role["fields"])
    user_prompt = (
        f"You are analyzing: {symbol}\n\n"
        f"Required output fields: {fields_list}, vote, confidence, reason\n\n"
        f"DATA:\n{bundle_text}"
    )

    # Inject this agent's own scoped memory block (never another agent's)
    mem = agent_memory.recall_block(role["key"], symbol)
    system_prompt = role["system_prompt"] + ("\n\n" + mem if mem else "")

    raw = await ask_role(system_prompt, user_prompt, role_key=role["key"])
    parsed = parse_yaml_block(raw)

    # Strip memory_note from _raw so it never propagates to the report renderer
    import re as _re
    raw_clean = _re.sub(r"\nmemory_note:.*", "", raw)

    result = {
        "key": role["key"],
        "title": role["title"],
        "vote": parsed.get("vote", "HOLD"),
        "confidence": parsed.get("confidence", 50),
        "reason": parsed.get("reason", parsed.get("_raw", "")),
        "_raw": raw_clean,
    }
    # Merge role-specific fields (including memory_note — private, not rendered)
    for f in role["fields"]:
        result[f] = parsed.get(f)

    # Save the agent's private durable takeaway (figures firewall enforced inside)
    note_val = parsed.get("memory_note")
    if isinstance(note_val, str) and note_val.strip():
        agent_memory.save_note(role["key"], note_val.strip(), symbol)

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

async def run_committee(
    symbol: str,
    debate: bool | None = None,
    parallel: bool | None = None,
) -> CommitteeResult:
    """
    Run the full committee pipeline for *symbol*:
      1. gather_bundle
      2. specialists (parallel or sequential) — Round 1
      3. debate (Round 2 cross-exam + Round 3 revisions) — optional, bounded
      4. moderator consensus on Round 3 votes  [serial]
      5. CIO final decision on Round 3 votes   [serial]

    debate=None reads CIO_DEBATE env var (default "on").
    parallel=None reads CIO_PARALLEL env var (default "on" / parallel).
    Returns CommitteeResult; never raises.
    """
    # Resolve parallel flag
    use_parallel = PARALLEL if parallel is None else parallel

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

    # Step 2 — specialists (parallel or sequential)
    active_roles = [
        role for role in SPECIALISTS
        if not (role["key"] == "etf" and not is_etf)
    ]

    async def _run_specialist_safe(role: dict) -> dict:
        try:
            return await run_specialist(role, bundle_text, resolved)
        except Exception as e:
            log.warning("Specialist %s failed: %s", role["key"], e)
            fallback = {
                "key": role["key"],
                "title": role["title"],
                "vote": "HOLD",
                "confidence": 0,
                "reason": f"Error: {e}",
                "_raw": "",
            }
            for f in role["fields"]:
                fallback[f] = None
            return fallback

    opinions: list[dict] = await _gather_bounded(
        [_run_specialist_safe(role) for role in active_roles],
        parallel=use_parallel,
    )

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
            debate_result = await run_debate(
                opinions, bundle_text, resolved, roles_by_key, parallel=use_parallel
            )
            opinions = debate_result.get("round3_opinions", opinions)
        # else: all same vote — debate_result stays skipped

    # Step 4 — consensus (moderator LLM + deterministic tally) on final (Round 3) votes [serial]
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
    mod_raw = await ask_role(MODERATOR_SYSTEM, moderator_prompt, role_key="moderator")
    consensus = parse_yaml_block(mod_raw)

    # Step 5 — CIO (serial; uses config for model/service)
    cio_prompt = (
        f"Symbol: {resolved}\n\n"
        f"DATA SUMMARY:\n{bundle_text}\n\n"
        f"COMMITTEE OPINIONS:\n{opinions_summary}\n\n"
        f"CONSENSUS:\n{mod_raw}\n\n"
        f"Required output fields: final_recommendation, confidence_score, risk_rating, "
        f"time_horizon, base_case, bull_case, bear_case, scenarios"
    )
    # Inject CIO's own scoped memory block
    cio_mem = agent_memory.recall_block("cio", resolved)
    cio_system = CIO_SYSTEM + ("\n\n" + cio_mem if cio_mem else "")
    cio_raw = await ask_role(cio_system, cio_prompt, role_key="cio")
    cio = parse_yaml_block(cio_raw)

    # Save CIO's durable takeaway (figures firewall enforced inside save_note)
    cio_note = cio.get("memory_note")
    if isinstance(cio_note, str) and cio_note.strip():
        agent_memory.save_note("cio", cio_note.strip(), resolved)

    # Post-pipeline reflect: promote frequently-recalled warm notes to hot
    roles_that_ran = [role["key"] for role in active_roles]
    roles_that_ran.append("cio")
    for rk in roles_that_ran:
        try:
            agent_memory.reflect(rk)
        except Exception as _e:
            log.debug("reflect failed for %s: %s", rk, _e)

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
