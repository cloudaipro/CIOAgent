"""
translate.py — English, Traditional Chinese, and mixed-language report output.

normalize_lang  — maps language aliases to "en", "tc", or "mix".
translate_report — async; translates md to TC or interleaved English/TC; no-op for "en".
                   On empty/failed LLM result falls back to the original English md
                   so the report is never lost.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language aliases
# ---------------------------------------------------------------------------

TC_LANG_ALIASES: frozenset[str] = frozenset({
    "zh", "tc", "zh-tw", "zh_tw", "中文", "繁中", "繁體", "繁體中文",
})
EN_LANG_ALIASES: frozenset[str] = frozenset({"en", "english", "英文", "英語"})
MIX_LANG_ALIASES: frozenset[str] = frozenset({
    "mix", "mixed", "bilingual", "en-zh", "en_zh", "中英", "英中", "中英雙語",
})

# Backward-compatible public name used by older callers.
LANG_ALIASES = TC_LANG_ALIASES


def _to_traditional(text: str) -> str:
    """
    Force Simplified→Traditional (Taiwan) with OpenCC s2twp.

    Idempotent on text that is already Traditional, so it is safe to always apply
    as a guarantee regardless of which model produced *text*. If OpenCC is missing
    or conversion fails, return *text* unchanged (translation is never lost).
    """
    try:
        import opencc  # pip: opencc-python-reimplemented
        converted = opencc.OpenCC("s2twp").convert(text)
        # Observed gpt-5.4-mini artifact for the market-regime value "mixed":
        # a stray Devanagari prefix attached to a Chinese suffix.
        return converted.replace("मिश्र合", "混合")
    except Exception as exc:
        log.warning("_to_traditional: OpenCC unavailable/failed (%s); returning text as-is", exc)
        return text


_TRANSLATOR_SYSTEM = (
    "You are a professional financial translator. "
    "Translate the following investment-committee report from English into "
    "Traditional Chinese (繁體中文, Taiwan usage). "
    "PRESERVE all markdown structure exactly (headers, tables, pipes, bold, lists), "
    "all numbers, percentages, dates, and stock tickers/proper nouns "
    "(keep AAPL, P/E, ROE, WWDC as-is). "
    "Translate the market-regime label 'mixed' as '混合'. Never emit Devanagari, "
    "Japanese kana, Hangul, or other non-Chinese scripts. "
    "Output ONLY the translated markdown — no preamble."
)

_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_LIST_LINE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")


def _contains_han(text: str) -> bool:
    """Whether *text* contains CJK unified ideographs (not just punctuation)."""
    return any("\u3400" <= char <= "\u9fff" for char in text)


def _nonempty_lines(text: str) -> list[str]:
    return [line for line in text.strip().splitlines() if line.strip()]


def _interleave_translation(english: str, traditional: str) -> str | None:
    """Deterministically pair equivalent English and Chinese markdown lines.

    The normal translation prompt requires markdown structure to be preserved, so
    corresponding non-empty lines are stable anchors. Tables are emitted whole in
    each language because alternating rows would produce invalid markdown. Returns
    ``None`` when the translation changed structure and cannot be paired safely.
    """
    en_lines = _nonempty_lines(english)
    tc_lines = _nonempty_lines(traditional)
    if not en_lines or len(en_lines) != len(tc_lines):
        return None

    out: list[str] = []
    i = 0
    while i < len(en_lines):
        if _TABLE_LINE.match(en_lines[i]):
            end = i + 1
            while end < len(en_lines) and _TABLE_LINE.match(en_lines[end]):
                end += 1
            if not all(_TABLE_LINE.match(line) for line in tc_lines[i:end]):
                return None
            out.extend(en_lines[i:end])
            out.append("")
            out.extend(tc_lines[i:end])
            out.append("")
            i = end
            continue

        if _LIST_LINE.match(en_lines[i]):
            end = i + 1
            while end < len(en_lines) and _LIST_LINE.match(en_lines[end]):
                end += 1
            if not all(_LIST_LINE.match(line) for line in tc_lines[i:end]):
                return None
            for en_line, tc_line in zip(en_lines[i:end], tc_lines[i:end]):
                out.extend((en_line, tc_line))
            out.append("")
            i = end
            continue

        # A blank line is required between ordinary Markdown lines; a single
        # newline is only a soft wrap and would put both languages in one <p>.
        out.extend((en_lines[i], "", tc_lines[i], ""))
        i += 1

    return "\n".join(out).rstrip() + "\n"


def normalize_lang(token: "str | None") -> str:
    """
    Normalize *token* to "en", "tc", or "mix".

    Case-insensitive for ASCII tokens; Chinese tokens matched exactly.
    None / empty string / unrecognised → "en".
    """
    if not token:
        return "en"
    normalised = token.strip().lower()
    raw = token.strip()
    if normalised in TC_LANG_ALIASES or raw in TC_LANG_ALIASES:
        return "tc"
    if normalised in MIX_LANG_ALIASES or raw in MIX_LANG_ALIASES:
        return "mix"
    return "en"


def is_language_token(token: "str | None") -> bool:
    """Return whether *token* explicitly selects a supported report language."""
    if not token:
        return False
    raw = token.strip()
    normalised = raw.lower()
    aliases = TC_LANG_ALIASES | EN_LANG_ALIASES | MIX_LANG_ALIASES
    return normalised in aliases or raw in aliases


async def translate_report(md: str, lang: str) -> str:
    """
    Render *md* in the requested language.

    ``tc`` returns a Traditional-Chinese translation. ``mix`` first requests that
    same translation, validates it, then interleaves it with the untouched English
    source in code. ``en`` (and unsupported values) returns *md* unchanged.

    Routes through engine.ask_role(role_key="translator") so the model is config-driven.
    On empty / failed LLM result → returns the original English md (never breaks the report).
    """
    if lang not in {"tc", "mix"}:
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

    # Guarantee Traditional (Taiwan) output even if the model emitted Simplified.
    translated = _to_traditional(result)
    if not _contains_han(translated):
        log.warning("translate_report: translation contains no Chinese; returning original md")
        return md
    if lang == "tc":
        return translated

    mixed = _interleave_translation(md, translated)
    if mixed is None:
        log.warning(
            "translate_report: translation structure does not match English; "
            "returning original md"
        )
        return md
    return mixed
