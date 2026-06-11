"""
test_pdf_report.py — offline tests for PDF rendering and TC translation.

All tests are fully offline:
  - PDF rendering uses real WeasyPrint (local, no network).
  - Translation tests monkeypatch engine.ask_role — no LLM call.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A. render_pdf.markdown_to_pdf
# ---------------------------------------------------------------------------

class TestMarkdownToPdf:
    def test_english_md_produces_pdf_magic_bytes(self, tmp_path):
        """An English-only md renders to a file starting with %PDF."""
        from cio.committee.render_pdf import markdown_to_pdf

        md = (
            "# Investment Committee Report: AAPL\n\n"
            "## Executive Summary\n\n"
            "**Recommendation:** Buy\n"
            "**Confidence:** 78\n\n"
            "| Specialist | Vote | Confidence | Reason |\n"
            "|---|---|---|---|\n"
            "| Equity | BUY | 80 | Strong earnings growth |\n"
        )
        out = tmp_path / "aapl_report.pdf"
        result_path = markdown_to_pdf(md, out, title="AAPL Report")

        assert result_path == str(out)
        assert out.exists()
        assert out.stat().st_size > 0
        assert out.read_bytes()[:4] == b"%PDF", "File does not start with %PDF magic bytes"

    def test_cjk_md_produces_pdf_magic_bytes(self, tmp_path):
        """A md containing 繁體中文 renders to a file starting with %PDF."""
        from cio.committee.render_pdf import markdown_to_pdf

        md = (
            "# 投資委員會報告：AAPL\n\n"
            "## 執行摘要\n\n"
            "**建議：** 買入\n"
            "**信心：** 78\n\n"
            "| 專家 | 投票 | 信心 | 原因 |\n"
            "|---|---|---|---|\n"
            "| 股票分析師 | 買入 | 80 | 盈利增長強勁 |\n\n"
            "市值：繁體中文測試文字。P/E 比率良好，ROE 表現優秀。\n"
        )
        out = tmp_path / "aapl_zh_report.pdf"
        result_path = markdown_to_pdf(md, out, title="AAPL 投資報告")

        assert result_path == str(out)
        assert out.exists()
        assert out.stat().st_size > 0
        assert out.read_bytes()[:4] == b"%PDF", "CJK PDF does not start with %PDF magic bytes"

    def test_output_dir_created_if_missing(self, tmp_path):
        """markdown_to_pdf creates parent directories when they don't exist."""
        from cio.committee.render_pdf import markdown_to_pdf

        out = tmp_path / "nested" / "subdir" / "report.pdf"
        assert not out.parent.exists()
        markdown_to_pdf("# Hello\n\nSimple report.", out)
        assert out.exists()
        assert out.read_bytes()[:4] == b"%PDF"

    def test_default_title_used_when_not_provided(self, tmp_path):
        """markdown_to_pdf works without an explicit title (uses default)."""
        from cio.committee.render_pdf import markdown_to_pdf

        out = tmp_path / "default_title.pdf"
        markdown_to_pdf("# Test\n\nContent.", out)
        assert out.read_bytes()[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# B. translate.normalize_lang
# ---------------------------------------------------------------------------

class TestNormalizeLang:
    def test_tc_aliases_return_tc(self):
        from cio.committee.translate import normalize_lang

        for token in ("zh", "tc", "zh-tw", "zh_tw", "中文", "繁中", "繁體", "繁體中文"):
            assert normalize_lang(token) == "tc", f"Expected 'tc' for token {token!r}"

    def test_non_tc_tokens_return_en(self):
        from cio.committee.translate import normalize_lang

        for token in ("en", "EN", "foo", "ja", "ko", "", "english"):
            assert normalize_lang(token) == "en", f"Expected 'en' for token {token!r}"

    def test_none_returns_en(self):
        from cio.committee.translate import normalize_lang

        assert normalize_lang(None) == "en"

    def test_case_insensitive_ascii(self):
        from cio.committee.translate import normalize_lang

        # "TC" uppercase → "tc" after .lower()
        assert normalize_lang("TC") == "tc"
        assert normalize_lang("ZH") == "tc"
        assert normalize_lang("ZH-TW") == "tc"


# ---------------------------------------------------------------------------
# C. translate.translate_report
# ---------------------------------------------------------------------------

class TestTranslateReport:
    def test_lang_en_returns_unchanged_no_ask_role_call(self, monkeypatch):
        """lang='en' must return md unchanged and NOT call ask_role."""
        import cio.committee.engine as engine_mod

        ask_role_calls: list = []

        async def _fake_ask_role(*args, **kwargs):
            ask_role_calls.append((args, kwargs))
            return "should not be called"

        monkeypatch.setattr(engine_mod, "ask_role", _fake_ask_role)

        from cio.committee.translate import translate_report

        md = "# Report\n\nContent here."
        result = _run(translate_report(md, "en"))

        assert result == md
        assert len(ask_role_calls) == 0, "ask_role must not be called for lang='en'"

    def test_lang_tc_calls_ask_role_and_returns_translation(self, monkeypatch):
        """lang='tc' calls ask_role and returns the translated result."""
        import cio.committee.engine as engine_mod

        canned_tc = "# 投資報告\n\n內容在此。"

        async def _fake_ask_role(system_prompt, user_prompt, role_key=None, **kwargs):
            assert role_key == "translator"
            return canned_tc

        monkeypatch.setattr(engine_mod, "ask_role", _fake_ask_role)

        from cio.committee.translate import translate_report

        md = "# Report\n\nContent here."
        result = _run(translate_report(md, "tc"))

        assert result == canned_tc

    def test_simplified_model_output_is_forced_to_traditional(self, monkeypatch):
        """A model that emits Simplified Chinese is OpenCC-converted to Traditional."""
        import cio.committee.engine as engine_mod

        simplified = "# 投资报告\n\n苹果公司的软件和网络服务收入增长。"

        async def _fake_simplified(system_prompt, user_prompt, role_key=None, **kwargs):
            return simplified

        monkeypatch.setattr(engine_mod, "ask_role", _fake_simplified)

        from cio.committee.translate import translate_report

        result = _run(translate_report("# Report", "tc"))
        # Traditional forms must appear; Simplified forms must be gone.
        assert "投資報告" in result and "蘋果" in result
        assert "投资" not in result and "苹果" not in result and "软件" not in result

    def test_ask_role_returns_empty_falls_back_to_original(self, monkeypatch):
        """ask_role returning '' → translate_report falls back to the original md."""
        import cio.committee.engine as engine_mod

        async def _fake_empty(*args, **kwargs):
            return ""

        monkeypatch.setattr(engine_mod, "ask_role", _fake_empty)

        from cio.committee.translate import translate_report

        md = "# Original Report\n\nFallback content."
        result = _run(translate_report(md, "tc"))

        assert result == md

    def test_ask_role_raises_falls_back_to_original(self, monkeypatch):
        """ask_role raising an exception → translate_report falls back to the original md."""
        import cio.committee.engine as engine_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("NIM exploded")

        monkeypatch.setattr(engine_mod, "ask_role", _boom)

        from cio.committee.translate import translate_report

        md = "# Original Report\n\nFallback content."
        result = _run(translate_report(md, "tc"))

        assert result == md


# ---------------------------------------------------------------------------
# D. models.resolve("translator")
# ---------------------------------------------------------------------------

class TestResolveTranslator:
    def test_resolve_translator_returns_claude(self):
        """resolve('translator') must return Claude — reliable on long markdown and
        strong Traditional Chinese (output is also OpenCC-forced to Traditional)."""
        from cio.committee.models import load_config, resolve

        # Hermetic: load_config is lru_cached and other tests point it at temp
        # configs (monkeypatch restores the env var but not the cache), so under
        # parallel/non-alphabetical ordering a stale doc could leak in here.
        load_config.cache_clear()
        try:
            service, model = resolve("translator")
            assert service == "claude"
            assert model == "claude-sonnet-4-6"
        finally:
            load_config.cache_clear()
