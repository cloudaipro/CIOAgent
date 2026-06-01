"""agent_memory.py — Per-agent MemCore facade for the Investment Committee.

Each committee agent (8 specialists + CIO) gets its own isolated persistent
memory under the scope `committee:{role_key}`.  Memory from one agent NEVER
surfaces for another agent; committee scopes are also isolated from the
conversational `global` / `chat:*` scopes.

Public API:
    scope_for(role_key)        → scope string
    recall_block(role_key, symbol, ...)  → str (inject into system prompt)
    save_note(role_key, value, symbol, ...)  → int | None
    reflect(role_key, ...)     → int (notes promoted to hot)
"""
from __future__ import annotations

import logging
import os

from .. import context, db, memory, recall

log = logging.getLogger(__name__)

# Token budget per agent's memory injection — lean to keep prompts tight.
MEM_BUDGET: int = int(os.getenv("CIO_AGENT_MEM_BUDGET", "400"))

# Module-level DB path — tests monkeypatch this to a temp db.
DB_PATH = db.DB_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def scope_for(role_key: str) -> str:
    """Return the MemCore scope string for a committee agent."""
    return f"committee:{role_key}"


# ---------------------------------------------------------------------------
# recall_block — build the injected memory section for one agent
# ---------------------------------------------------------------------------

def recall_block(role_key: str, symbol: str, k: int = 5,
                 budget: int = MEM_BUDGET) -> str:
    """Return a memory injection string for *role_key* agent, scoped to *symbol*.

    1. Hot notes for this agent's scope (build_scope_block).
    2. Warm/cold recall for *symbol*, restricted to THIS agent's scope only
       (include_global=False enforces isolation).
    3. Bumps each recalled hit to drive the auto-promotion loop.
    4. Composes both sections within *budget* tokens.
    Returns '' on any error so a failure never breaks a committee run.
    """
    try:
        scope = scope_for(role_key)

        # 1. Hot notes (injected at session start equivalent)
        hot = context.build_scope_block(scope, budget=budget, db_path=DB_PATH)

        # 2. Symbol-relevant recall — STRICT scope only (no global leakage)
        hits = recall.search(
            symbol, k=k, scope=scope, kinds=("note",),
            db_path=DB_PATH, include_global=False,
        )

        # 3. Bump each hit to feed the self-improving promotion loop
        for hit in hits:
            try:
                memory.bump(hit["id"], db_path=DB_PATH)
            except Exception:
                pass  # bump failure must not abort recall

        if not hot and not hits:
            return ""

        # 4. Compose within budget
        from .. import context as _ctx
        parts: list[str] = []
        used = 0

        if hot:
            parts.append(hot)
            used = _ctx.count_tokens(hot)

        if hits:
            recall_lines: list[str] = []
            header = f"**Recalled for {symbol}:**"
            header_tokens = _ctx.count_tokens(header + "\n")
            remaining = budget - used - header_tokens
            for hit in hits:
                line = f"- {hit['text']}"
                lt = _ctx.count_tokens(line + "\n")
                if remaining - lt < 0:
                    break
                recall_lines.append(line)
                remaining -= lt
            if recall_lines:
                parts.append(header + "\n" + "\n".join(recall_lines))

        return "\n\n".join(parts) if parts else ""

    except Exception as exc:
        log.warning("agent_memory.recall_block failed for %s/%s: %s", role_key, symbol, exc)
        return ""


# ---------------------------------------------------------------------------
# save_note — write the agent's private durable takeaway
# ---------------------------------------------------------------------------

def save_note(role_key: str, value: str, symbol: str,
              importance: float = 1.0) -> int | None:
    """Persist *value* as a WARM note in this agent's scope.

    Figures firewall: a note containing a price / $ amount / P&L is rejected
    and logged (not crashed).  Returns the note id on success, None on rejection
    or any other error.
    """
    if not value or not value.strip():
        return None
    try:
        return memory.remember(
            value,
            scope=scope_for(role_key),
            tier="warm",
            source="committee",
            importance=importance,
            db_path=DB_PATH,
        )
    except memory.FiguresFirewallError:
        log.warning(
            "agent_memory.save_note: figures firewall rejected note for %s (%s): %.60s…",
            role_key, symbol, value,
        )
        return None
    except Exception as exc:
        log.warning("agent_memory.save_note failed for %s/%s: %s", role_key, symbol, exc)
        return None


# ---------------------------------------------------------------------------
# reflect — promote frequently-recalled warm notes to hot
# ---------------------------------------------------------------------------

def reflect(role_key: str) -> int:
    """Promote WARM notes with hits >= PROMOTE_HITS to HOT for *role_key*'s scope.

    HOT notes are injected at the start of the next run via recall_block.
    Returns the count promoted.  Never raises.
    """
    try:
        return memory.promote_hot(scope_for(role_key), db_path=DB_PATH)
    except Exception as exc:
        log.warning("agent_memory.reflect failed for %s: %s", role_key, exc)
        return 0
