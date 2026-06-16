"""
test_richfmt.py — Telegram Rich Markdown format guard.

Root cause (prod records 270-273): a GFM table whose header row directly
follows a paragraph (e.g. a ``**bold label**`) with no blank line does not
parse — the header is a lazy continuation of the paragraph, so the whole table
collapses to one flattened line of literal pipes. These tests prove
``richfmt.normalize`` inserts the missing blank lines (and ``validate`` reports
them) without disturbing anything else.
"""
from __future__ import annotations

from cio import richfmt


TABLE = "| 評等 | 家數 | % |\n|---|---|---|\n| Strong Buy | 9 | 27% |"


def test_blank_inserted_before_table_after_paragraph():
    md = "**分析師共識** — 33 家覆蓋\n" + TABLE
    out = richfmt.normalize(md)
    lines = out.split("\n")
    hdr = lines.index("| 評等 | 家數 | % |")
    assert lines[hdr - 1] == ""                      # blank now precedes header
    assert lines[hdr - 2] == "**分析師共識** — 33 家覆蓋"  # label preserved


def test_blank_inserted_after_table_before_paragraph():
    md = "intro\n\n" + TABLE + "\n結論：buy"
    out = richfmt.normalize(md)
    lines = out.split("\n")
    last_row = lines.index("| Strong Buy | 9 | 27% |")
    assert lines[last_row + 1] == ""                 # blank after table
    assert lines[last_row + 2] == "結論：buy"


def test_already_correct_table_unchanged():
    md = "intro\n\n" + TABLE + "\n\nafter"
    assert richfmt.normalize(md) == md               # no spurious edits


def test_idempotent():
    md = "**label**\n" + TABLE + "\ntail"
    once = richfmt.normalize(md)
    assert richfmt.normalize(once) == once


def test_no_table_content_untouched():
    md = "# Heading\n\n- a\n- b\n\nplain paragraph with a | pipe in prose"
    assert richfmt.normalize(md) == md


def test_thematic_break_not_treated_as_table():
    # A lone '---' is a horizontal rule, not a table separator.
    md = "para one\n\n---\n\npara two"
    assert richfmt.normalize(md) == md
    assert richfmt.validate(md) == []


def test_prose_pipe_line_not_a_table():
    md = "a | b is not a table\njust prose"
    assert richfmt.normalize(md) == md


def test_validate_flags_missing_blank_and_ragged_columns():
    md = "label\n" + TABLE                            # missing blank before
    warns = richfmt.validate(md)
    assert any("blank line before" in w for w in warns)

    ragged = "intro\n\n| a | b | c |\n|---|---|\n| 1 | 2 | 3 |"  # 3 hdr vs 2 sep
    warns2 = richfmt.validate(ragged)
    assert any("columns" in w for w in warns2)


def test_validate_clean_table_no_warnings():
    md = "intro\n\n" + TABLE + "\n\nafter"
    assert richfmt.validate(md) == []


def test_heading_then_table_gets_blank_harmlessly():
    md = "### 基本面\n| 項目 | 值 |\n|---|---|\n| 市值 | 54B |"
    out = richfmt.normalize(md)
    lines = out.split("\n")
    hdr = lines.index("| 項目 | 值 |")
    assert lines[hdr - 1] == ""                       # blank between heading+table
    assert "### 基本面" in lines
