"""Session-start context assembly (the injection layer).

Hermes/OpenClaw inject a bounded "hot" memory file into the prompt at session
start so the agent *knows* its context before turn one. CIOAgent does the same,
but adaptively: it ranks notes by importance×recency and packs the operator
profile + pinned notes + latest session digest into a **token budget** (measured
with tiktoken, with headroom), so injected context can never blow the window.

`compose_system_prompt` is called when (re)building the SDK options for a chat,
so every connect — including each rolling-session fork — refreshes the memory.
"""
from __future__ import annotations

from . import memory
from .db import DB_PATH

# Token budget for the injected memory block (NOT the whole prompt). Conservative
# so estimator error (tiktoken approximates Claude's tokenizer) can't overflow.
DEFAULT_BUDGET = 1000

_ENC = None


def _enc():
    global _ENC
    if _ENC is None:
        import tiktoken  # required dep; one consistent local estimator
        _ENC = tiktoken.get_encoding("cl100k_base")
    return _ENC


def count_tokens(text: str) -> int:
    return len(_enc().encode(text or ""))


def _truncate_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    toks = _enc().encode(text)
    if len(toks) <= max_tokens:
        return text
    return _enc().decode(toks[:max_tokens]) + "…"


def build_memory_block(chat_id: int | None = None, budget: int = DEFAULT_BUDGET,
                       db_path=DB_PATH) -> str:
    """Assemble the injected memory section within `budget` tokens. Empty if no
    profile/notes/digest exist yet."""
    scope_chat = f"chat:{chat_id}" if chat_id is not None else None
    profile = memory.get_profile("global", db_path=db_path)

    notes = memory.list_notes("global", tier="hot", limit=50, db_path=db_path)
    if scope_chat:
        notes += memory.list_notes(scope_chat, tier="hot", limit=50, db_path=db_path)
    notes.sort(key=lambda n: (n["importance"], n["updated_at"]), reverse=True)

    digest = memory.latest_digest(chat_id, db_path=db_path) if chat_id is not None else None

    header = "## Persistent memory (you already know this — do NOT re-ask the user)"
    lines = [header]

    def fits(*extra: str) -> bool:
        # Measure the ACTUAL joined block (newlines included) so it never exceeds budget.
        return count_tokens("\n".join(lines + list(extra))) <= budget

    if profile:
        pl = "**Operator:** " + " | ".join(f"{k}: {v}" for k, v in profile.items())
        if fits(pl):
            lines.append(pl)

    tag = "**Pinned notes:**"
    have_tag = False
    for n in notes:
        ln = f"- {n['value']}"
        trial = ([tag, ln] if not have_tag else [ln])
        if not fits(*trial):
            break
        if not have_tag:
            lines.append(tag)
            have_tag = True
        lines.append(ln)

    pbs = memory.list_playbooks(scope_chat or "global", db_path=db_path)
    if pbs:
        pl = "**Saved playbooks (use list_playbooks for steps):** " + ", ".join(p["name"] for p in pbs)
        if fits(pl):
            lines.append(pl)

    if digest:
        prefix = "**Last session digest:** "
        remaining = budget - count_tokens("\n".join(lines)) - count_tokens(prefix) - 1
        if remaining > 16:  # only if a useful amount fits
            lines.append(prefix + _truncate_tokens(digest, remaining))

    return "\n".join(lines) if len(lines) > 1 else ""


def compose_system_prompt(base: str, chat_id: int | None = None,
                          budget: int = DEFAULT_BUDGET, db_path=DB_PATH) -> str:
    """Base system prompt + injected memory block (if any)."""
    block = build_memory_block(chat_id, budget, db_path)
    return f"{base}\n\n{block}" if block else base
