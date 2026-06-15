"""Shared committee-report delivery pipeline.

Run the committee for one symbol, translate, render to PDF (markdown fallback on
render failure), and build a short summary. Used by BOTH the Telegram
`/committee` command and the conversational agent's `run_committee` tool so the
two stay in lockstep — change the pipeline once, both callers follow.

Imports of run_committee / build_report / translate_report / markdown_to_pdf are
done lazily inside `produce_report` so test seams that monkeypatch
``cio.committee.<name>`` are honoured (same pattern the bot used inline).
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cio.committee.delivery")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9.\-^=]")


def _safe_name(text: str, fallback: str = "report") -> str:
    s = _SAFE_NAME.sub("", str(text)).lstrip(".")[:24]
    return s or fallback


@dataclass
class CommitteeArtifact:
    """A finished committee run ready to hand to a transport (Telegram, agent).

    On success ``doc_path`` (a .pdf, or .md fallback) and ``summary`` are set and
    ``error`` is None. On failure ``error`` holds a user-facing message and
    ``doc_path``/``summary`` are None.
    """
    symbol: str = ""
    lang_label: str = ""
    doc_path: Path | None = None
    summary: str | None = None
    error: str | None = None


async def produce_report(symbol: str, lang: "str | None", reports_dir: Path,
                         source: str = "command") -> CommitteeArtifact:
    """Run the full committee pipeline for one symbol and return an artifact.

    ``source`` tags what triggered the run ("command" for /committee, "chat" for
    the conversational agent's tool) so the dev dashboard can tell them apart.

    Never raises: any failure inside run_committee is captured into
    ``CommitteeArtifact.error``.
    """
    from .translate import normalize_lang, translate_report
    from .engine import set_run_source

    sym = str(symbol).upper()
    lang = normalize_lang(lang)
    lang_label = " (繁體中文)" if lang == "tc" else ""

    set_run_source(source)
    from . import run_committee, build_report  # honour monkeypatch seams
    try:
        result = await run_committee(sym)
    except Exception as e:
        log.exception("run_committee error for %s", sym)
        return CommitteeArtifact(symbol=sym, lang_label=lang_label,
                                 error=f"⚠️ Committee error: {e}")

    if result.error:
        return CommitteeArtifact(
            symbol=sym, lang_label=lang_label,
            error=f"No data for {sym}. Check the symbol (TW codes need .TW/.TWO).")

    md = build_report(sym, result)
    date_str = datetime.date.today().isoformat()
    lang_suffix = "_zh" if lang == "tc" else ""
    md = await translate_report(md, lang)

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_title = f"Investment Committee Report: {sym}{lang_label}"
    pdf_path = reports_dir / f"{_safe_name(sym)}_committee_{date_str}{lang_suffix}.pdf"
    # Best-effort indicator-visualization chart for the dossier appendix.
    appendix_images = []
    try:
        from .. import stock
        chart_path = stock.render_indicators(sym, "committee")
        appendix_images.append((f"{sym} · RSI / MACD / KDJ · divergence", chart_path))
    except Exception:
        log.debug("indicator chart skipped for %s", sym, exc_info=True)

    try:
        from .render_pdf import markdown_to_pdf
        markdown_to_pdf(md, pdf_path, title=report_title,
                        appendix_images=appendix_images)
        doc_path = pdf_path
        # Also persist the markdown source next to the PDF.
        pdf_path.with_suffix(".md").write_text(md, encoding="utf-8")
    except Exception:
        log.exception("PDF render failed for %s; falling back to .md", sym)
        md_path = reports_dir / f"{_safe_name(sym)}_committee_{date_str}{lang_suffix}.md"
        md_path.write_text(md, encoding="utf-8")
        doc_path = md_path

    return CommitteeArtifact(symbol=sym, lang_label=lang_label, doc_path=doc_path,
                             summary=_summary(sym, lang_label, result))


def _summary(sym: str, lang_label: str, result) -> str:
    """One short Markdown summary message (mirrors the old inline bot version)."""
    from .report import confidence_band

    cio = result.cio or {}
    tally = result.vote_tally or {}
    consensus = result.consensus or {}

    final_rec = cio.get("final_recommendation") or "N/A"
    conf_score = cio.get("confidence_score")
    band = confidence_band(conf_score) if conf_score is not None else "N/A"
    conf_str = f"{conf_score}" if conf_score is not None else "N/A"
    buy_c = tally.get("buy_count", 0)
    hold_c = tally.get("hold_count", 0)
    sell_c = tally.get("sell_count", 0)
    agree = consensus.get("agreement_score") or "N/A"

    return (
        f"📋 *{sym} Committee Summary{lang_label}*\n\n"
        f"*Recommendation:* {final_rec}\n"
        f"*Confidence:* {conf_str} — {band}\n"
        f"*Vote Tally:* BUY {buy_c} | HOLD {hold_c} | SELL {sell_c}\n"
        f"*Agreement Score:* {agree}\n\n"
        f"_Full report attached above._"
    )
