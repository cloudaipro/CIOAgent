"""
translate.py — Optional Traditional Chinese translation for committee reports.

normalize_lang  — maps various TC aliases to "tc", everything else to "en".
translate_report — async; translates md to TC when lang=="tc", no-op for "en".
                   On empty/failed LLM result falls back to the original English md
                   so the report is never lost.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language alias set → "tc"
# ---------------------------------------------------------------------------

LANG_ALIASES: frozenset[str] = frozenset({
    "zh", "tc", "zh-tw", "zh_tw", "中文", "繁中", "繁體", "繁體中文",
})

_TRANSLATOR_SYSTEM = (
    "You are a professional financial translator. "
    "Translate the following investment-committee report from English into "
    "Traditional Chinese (繁體中文, Taiwan usage). "
    "PRESERVE all markdown structure exactly (headers, tables, pipes, bold, lists), "
    "all numbers, percentages, dates, and stock tickers/proper nouns "
    "(keep AAPL, P/E, ROE, WWDC as-is). "
    "Output ONLY the translated markdown — no preamble."
)


def normalize_lang(token: "str | None") -> str:
    """
    Return "tc" if *token* matches any Traditional-Chinese alias, else "en".

    Case-insensitive for ASCII tokens; Chinese tokens matched exactly.
    None / empty string / unrecognised → "en".
    """
    if not token:
        return "en"
    normalised = token.strip().lower()
    if normalised in LANG_ALIASES or token.strip() in LANG_ALIASES:
        return "tc"
    return "en"


async def translate_report(md: str, lang: str) -> str:
    """
    Translate *md* to Traditional Chinese when lang=="tc"; return unchanged otherwise.

    Routes through engine.ask_role(role_key="translator") so the model is config-driven.
    On empty / failed LLM result → returns the original English md (never breaks the report).
    """
    if lang != "tc":
        return md

    from . import engine  # local import avoids circular at module load

    try:
        result = await engine.ask_role(
            system_prompt=_TRANSLATOR_SYSTEM,
            user_prompt=md,
            role_key="translator",
        )
    except Exception as exc:
        log.warning("translate_report: ask_role raised %s; returning original md", exc)
        return md

    if not result or not result.strip():
        log.warning("translate_report: got empty result from ask_role; returning original md")
        return md

    return result
