"""Telegram Rich Markdown format guard.

Telegram's Rich Markdown (Bot API 10.1) parses GFM-style pipe tables, but —
like CommonMark/GFM — a table only parses when a **blank line** separates its
header row from the preceding paragraph. When the agent writes a label right
above the table with no blank line, e.g.::

    **分析師共識 (2026-06-01)** — 33 家覆蓋
    | 評等 | 家數 | % |
    |---|---|---|
    | Strong Buy | 9 | 27% |

the header row is absorbed as a *lazy continuation* of the ``**…**`` paragraph,
the separator and every row fold in too, and the whole block degrades to one
paragraph. Telegram then collapses the single newlines to spaces and renders a
flattened soup of literal ``|`` — the bug seen in production records 270–273.

:func:`normalize` deterministically inserts the missing blank lines around
every table (no LLM, idempotent), so the choke point in :mod:`cio.richmsg`
can repair any message before it is sent. :func:`validate` reports the
problems it would fix, for logging / telemetry.

Reference (Rich Markdown style, table syntax):
https://core.telegram.org/bots/api#rich-message-formatting-options
"""
from __future__ import annotations

import re

# A table separator cell is optional colons around one-or-more dashes: ---, :--,
# --:, :-:. A separator LINE must contain a pipe (so a lone ``---`` thematic
# break is never mistaken for one) and every cell must match.
_SEP_CELL = re.compile(r"^:?-+:?$")


def _cells(line: str) -> list[str]:
    """Split a pipe row into trimmed cell texts, dropping the empty edges that
    a leading / trailing ``|`` produces."""
    parts = [c.strip() for c in line.strip().split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _looks_like_row(line: str) -> bool:
    """A Markdown table row as the agent emits them: starts with ``|`` and has
    at least two pipes (so ``a | b`` prose without a leading pipe is ignored)."""
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator(line: str) -> bool:
    """True if *line* is a GFM table separator row (``|---|---|`` etc.)."""
    if "|" not in line:
        return False
    cells = _cells(line)
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def _table_header_at(lines: list[str], i: int) -> bool:
    """True if line *i* is a table header (its next line is a separator)."""
    return (i + 1 < len(lines)
            and _looks_like_row(lines[i])
            and _is_separator(lines[i + 1]))


def normalize(md: str) -> str:
    """Return *md* with a blank line guaranteed before and after every table.

    Idempotent and content-preserving: only blank lines are inserted, never
    removed, and no non-table text is touched. Safe to run on every message.
    """
    if not md or "|" not in md:
        return md

    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if _table_header_at(lines, i):
            # Blank line BEFORE the header (the actual fix for the prod bug).
            if out and out[-1].strip() != "":
                out.append("")
            out.append(lines[i])        # header
            out.append(lines[i + 1])    # separator
            j = i + 2
            while j < n and _looks_like_row(lines[j]):
                out.append(lines[j])    # body rows
                j += 1
            # Blank line AFTER the table, before any following paragraph.
            if j < n and lines[j].strip() != "":
                out.append("")
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def validate(md: str) -> list[str]:
    """List the Rich-Markdown problems in *md* that :func:`normalize` repairs or
    that a human should know about. Empty list = clean. Never raises."""
    if not md or "|" not in md:
        return []
    warnings: list[str] = []
    lines = md.split("\n")
    for i in range(len(lines)):
        if not _table_header_at(lines, i):
            continue
        if i > 0 and lines[i - 1].strip() != "":
            warnings.append(
                f"table at line {i + 1}: no blank line before header "
                f"(would not render — header lazily continues the previous "
                f"paragraph)")
        n_hdr = len(_cells(lines[i]))
        n_sep = len(_cells(lines[i + 1]))
        if n_hdr != n_sep:
            warnings.append(
                f"table at line {i + 1}: header has {n_hdr} columns but "
                f"separator has {n_sep}")
    return warnings
