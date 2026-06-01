"""
python -m cio.committee SYMBOL

Dev/smoke tool: runs the full committee pipeline and writes a markdown report
to docs/reports/{SYMBOL}_{date}.md. Prints the output path when done.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m cio.committee SYMBOL")
        sys.exit(1)

    symbol = sys.argv[1].upper()

    from . import run_committee, build_report

    result = asyncio.run(run_committee(symbol))
    report_md = build_report(symbol, result)

    out_dir = Path(__file__).resolve().parent.parent.parent / "docs" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{result.resolved or symbol}_{date.today().isoformat()}.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
