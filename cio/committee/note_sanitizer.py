"""LLM figures-sanitizer for committee memory notes (the smart layer of the
figures firewall).

The deterministic regex firewall in :mod:`cio.memory` can only block notes that
contain figures — and only the patterns someone thought to enumerate. This module
adds an LLM pass that *understands* "a number that goes stale" semantically, so no
keyword list is needed, and that **salvages** the qualitative insight by rewriting
the note without the number instead of rejecting it outright:

    "AAPL's 141% ROE and 27% margins prove a durable moat"
        -> "AAPL's exceptional, structurally high profitability proves a durable moat"

The regex firewall is kept as the *acceptance contract*: every rewrite is verified
against ``memory._looks_like_figure`` and, if it still carries a figure, retried
once with the leak fed back, then rejected. So the smart layer can transform freely
while the deterministic layer still guarantees what lands in the db.

Design:
* ``asker`` is injected (engine passes ``ask_role``) — no import cycle, and tests
  can supply a fake. Signature matches ``engine.ask_role``.
* Fail-safe: if the model is unavailable (empty response — offline, no key, budget),
  the ORIGINAL text is returned so the regex firewall downstream still gets the final
  say. A sanitizer hiccup never silently drops a note or breaks a committee run.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Awaitable, Callable, Optional

from .. import memory

log = logging.getLogger(__name__)

# Model for the sanitize pass. A constrained rewrite is well within Sonnet; bump to
# claude-opus-4-8 via env if you want maximum fidelity (marginal for this task).
SANITIZER_MODEL = os.getenv("CIO_SANITIZER_MODEL", "claude-sonnet-4-6")
SANITIZER_SERVICE = os.getenv("CIO_SANITIZER_SERVICE", "claude")

# Layer-1 prevention text — appended to each agent's system prompt so figures are
# never written in the first place. The sanitizer is the safety net for when the
# agent ignores this (LLMs do, sometimes).
FIGURE_RULE = (
    "MEMORY DISCIPLINE: the `memory_note` field is stored in long-term memory and "
    "must contain only DURABLE, QUALITATIVE insight. Never put a number that goes "
    "stale in it — no prices, $ amounts, percentages, ratios (ROE, margins, P/E, "
    "yield, EPS, multiples), growth rates, or counts. State the insight, not the "
    "figure. Bad: 'AAPL's 141% ROE proves the moat.' "
    "Good: 'AAPL's exceptional profitability proves the moat.'"
)

# Type of the injected dispatcher (engine.ask_role).
Asker = Callable[..., Awaitable[str]]
# Optional audit sink: audit(action, original, cleaned, removed). Injected by the
# engine to feed sanitizer_log / the dashboard. Kept injected (not imported) so this
# module stays pure and tests don't touch the committee db.
Audit = Callable[[str, str, str, list], None]

_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)

_SYS = (
    "You sanitize ONE memory note for an investment agent's long-term memory. "
    "Long-term memory may hold only durable, qualitative insight. Remove every "
    "figure that becomes stale over time: prices, $ amounts, percentages, financial "
    "ratios (ROE, ROA, margins, P/E, multiples, yield, EPS, EBITDA), growth rates, "
    "and counts. PRESERVE the underlying insight by restating it qualitatively "
    "without the number — do not just delete the sentence. If, after removing all "
    "figures, no meaningful insight remains, return an empty `clean` string. "
    'Respond with ONLY a JSON object: {"clean": "<rewritten note or empty>", '
    '"removed": ["<each figure you stripped>"]}'
)


def _parse(raw: str) -> Optional[dict]:
    """Pull the JSON object out of the model response; None if unparseable."""
    if not raw:
        return None
    m = _JSON_OBJ.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _noop_audit(action: str, original: str, cleaned: str, removed: list) -> None:
    pass


async def sanitize(text: str, symbol: str, asker: Asker,
                   role_key: str | None = None, max_retries: int = 1,
                   audit: Audit = _noop_audit) -> Optional[str]:
    """Return a figure-free rewrite of *text*, or None to reject the note.

    Outcomes:
      * cleaned str  — regex-verified figure-free rewrite (store this).
      * None         — note was entirely figures, or still dirty after retry (drop).
      * original text — model unavailable (empty response); let the downstream regex
                        firewall make the final call. Distinguished by identity to the
                        input only in the unavailable case.

    *audit* is called once with (action, original, cleaned, removed) when a figure
    note is actually transformed ('cleaned') or dropped ('rejected'). Fast-path and
    model-unavailable cases are not audited (no figure action taken here).
    """
    text = (text or "").strip()
    if not text:
        return None

    # Fast path: already clean by the deterministic check — skip the LLM entirely.
    if not memory._looks_like_figure(text):
        return text

    user = f"Symbol: {symbol}\n\nNote to sanitize:\n{text}"
    leaked: str | None = None

    for attempt in range(max_retries + 1):
        sys = _SYS
        if leaked:
            sys += (f"\n\nYour previous rewrite STILL contained a figure: "
                    f"'{leaked}'. Remove it and any other number.")
        try:
            raw = await asker(sys, user, role_key=None,
                              service=SANITIZER_SERVICE, model=SANITIZER_MODEL)
        except Exception as exc:
            log.warning("note_sanitizer: asker failed for %s/%s: %s", role_key, symbol, exc)
            return text  # unavailable → fall through to regex firewall

        if not raw:
            log.info("note_sanitizer: empty response for %s/%s; deferring to regex firewall",
                     role_key, symbol)
            return text  # unavailable → fall through to regex firewall

        obj = _parse(raw)
        clean = (obj.get("clean") if obj else "") or ""
        clean = clean.strip()

        removed = list(obj.get("removed") or []) if obj else []

        if not clean:
            log.info("note_sanitizer: nothing salvageable in note for %s/%s; rejecting",
                     role_key, symbol)
            audit("rejected", text, "", removed)
            return None  # all-figure note → drop

        # Acceptance contract: must pass the deterministic firewall.
        leak = memory._looks_like_figure(clean)
        if not leak:
            if removed:
                log.info("note_sanitizer: stripped %s from %s note for %s",
                         removed, role_key, symbol)
            audit("cleaned", text, clean, removed)
            return clean

        leaked = clean  # feed the leak back on retry
        log.info("note_sanitizer: rewrite still dirty (attempt %d) for %s/%s",
                 attempt + 1, role_key, symbol)

    log.warning("note_sanitizer: could not produce a figure-free note for %s/%s; rejecting",
                role_key, symbol)
    audit("rejected", text, "", [])
    return None
