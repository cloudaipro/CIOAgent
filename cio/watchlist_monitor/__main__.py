"""
python -m cio.watchlist_monitor [SYMBOL ...] [zh]

Dev/smoke tool: runs the WMA over the given symbols (or the active watchlist when
none are given), renders the morning briefing, and writes a PDF to data/reports/.
Add ``zh`` for a Traditional-Chinese briefing. Prints the output path. Falls back
to .md if PDF rendering fails.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path


def main():
    from . import monitor_watchlist, build_briefing, briefing_summary, as_of_now
    from ..committee.translate import normalize_lang, translate_report

    # Split a trailing/any language token (zh) out of the symbol list.
    lang = "en"
    symbols: list[str] = []
    for arg in sys.argv[1:]:
        if normalize_lang(arg) == "tc":
            lang = "tc"
        else:
            symbols.append(arg.upper())
    lang_suffix = "_zh" if lang == "tc" else ""

    assessments = asyncio.run(monitor_watchlist(symbols or None))
    briefing = build_briefing(assessments, as_of=as_of_now())
    briefing = asyncio.run(translate_report(briefing, lang))
    print(briefing_summary(assessments))
    print()

    out_dir = Path(__file__).resolve().parent.parent.parent / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = date.today().isoformat()
    pdf_path = out_dir / f"watchlist_briefing_{date_str}{lang_suffix}.pdf"
    try:
        from ..committee.render_pdf import markdown_to_pdf
        markdown_to_pdf(briefing, pdf_path, title=f"Watchlist Briefing {date_str}")
        print(pdf_path)
    except Exception as exc:
        md_path = out_dir / f"watchlist_briefing_{date_str}{lang_suffix}.md"
        md_path.write_text(briefing, encoding="utf-8")
        print(f"[PDF render failed: {exc}] Wrote .md: {md_path}")


if __name__ == "__main__":
    main()
