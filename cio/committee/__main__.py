"""
python -m cio.committee SYMBOL [zh]

Dev/smoke tool: runs the full committee pipeline, optionally translates to
Traditional Chinese, and writes a PDF report to data/reports/. Prints the
output path when done. Falls back to .md if PDF rendering fails.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m cio.committee SYMBOL [zh]")
        sys.exit(1)

    symbol = sys.argv[1].upper()

    from .translate import normalize_lang, translate_report
    lang = normalize_lang(sys.argv[2] if len(sys.argv) > 2 else None)
    lang_suffix = "_zh" if lang == "tc" else ""

    from . import run_committee, build_report

    result = asyncio.run(run_committee(symbol))
    report_md = build_report(symbol, result)
    report_md = asyncio.run(translate_report(report_md, lang))

    out_dir = Path(__file__).resolve().parent.parent.parent / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_sym = result.resolved or symbol
    date_str = date.today().isoformat()
    pdf_path = out_dir / f"{safe_sym}_{date_str}{lang_suffix}.pdf"
    try:
        from .render_pdf import markdown_to_pdf
        markdown_to_pdf(report_md, pdf_path, title=f"Investment Committee Report: {symbol}")
        print(pdf_path)
    except Exception as exc:
        md_path = out_dir / f"{safe_sym}_{date_str}{lang_suffix}.md"
        md_path.write_text(report_md, encoding="utf-8")
        print(f"[PDF render failed: {exc}] Wrote .md: {md_path}")


if __name__ == "__main__":
    main()
