"""
python -m cio.committee.tirf <cmd>

Dev/audit CLI for the Transparent Investment Research Framework (PRD §14, the
proposal §16 endpoints re-homed to a CLI — no HTTP server per locked surface).

  generate SYMBOL [zh]   run the committee, persist the TIRF report, print the dossier
  show REPORT_ID         print a stored report's dossier (reconstructed from DB)
  list [N]               list the most recent N research reports
"""
from __future__ import annotations

import asyncio
import sys

from . import store


def _list(n: int = 20) -> None:
    rows = store.list_reports(limit=n)
    if not rows:
        print("(no research reports yet)")
        return
    print(f"{'report_id':<14}{'ticker':<8}{'ver':<5}{'tirf':<6}{'rec':<14}{'created'}")
    for r in rows:
        print(f"{r['report_id']:<14}{(r['ticker'] or ''):<8}{r['version']:<5}"
              f"{str(r.get('tirf_score') or '-'):<6}"
              f"{(r.get('final_recommendation') or '-')[:13]:<14}{r.get('created_at','')}")


def _show(report_id: str) -> None:
    row = store.get_report(report_id)
    if not row:
        print(f"No report {report_id}")
        sys.exit(1)
    ev = store.get_evidence(report_id)
    asm = store.get_assumptions(report_id)
    src = store.get_sources(report_id)
    ctr = store.get_counterarguments(report_id)
    print(f"# Research Report {report_id} — {row['ticker']} v{row['version']}")
    print(f"Recommendation: {row.get('final_recommendation')}  (confidence {row.get('confidence')})")
    print(f"TIRF score: {row.get('tirf_score')}  | evidence_quality: {row.get('evidence_quality')}")
    print(f"Pins: data_hash={row.get('data_hash','')[:12]}… prompt={row.get('prompt_version')} "
          f"agent={row.get('agent_version')}")
    print(f"\nEvidence ({len(ev)}):")
    for e in ev:
        print(f"  [{e['item_score']:>3}] {e['source_tier']:<16} {e['date'] or '—':<12} {e['finding']}")
    print(f"\nAssumptions ({len(asm)}):")
    for a in asm:
        print(f"  - {a['role_key']}: {a['name']} = {a['value']}")
    print(f"\nCounterarguments ({len(ctr)}):")
    for c in ctr:
        print(f"  - ({c['role_key']}) {c['argument']}")
    print(f"\nSources ({len(src)}):")
    for s in src:
        print(f"  [{s['reliability_score']:>3}] {s['reference']}")


def _generate(symbol: str, lang: str | None) -> None:
    from . import build_research_report, persist, render_dossier
    from ..engine import run_committee, set_run_source
    from .. import build_report

    set_run_source("cli")
    result = asyncio.run(run_committee(symbol))
    if result.error:
        print(f"No data for {symbol}: {result.error}")
        sys.exit(1)

    report = build_research_report(
        ticker=result.resolved or symbol,
        bundle=result.bundle,
        opinions=result.opinions,
        cio=result.cio,
        debate_result=result.debate,
        source="cli",
        run_id=None,
    )
    rid = persist(report)
    print(f"[persisted report_id={rid} v{report.version} tirf={report.metrics.get('tirf_score')}]\n")
    print(render_dossier(report))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        _list(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("Usage: python -m cio.committee.tirf show REPORT_ID")
            sys.exit(1)
        _show(sys.argv[2])
    elif cmd == "generate":
        if len(sys.argv) < 3:
            print("Usage: python -m cio.committee.tirf generate SYMBOL [zh]")
            sys.exit(1)
        _generate(sys.argv[2].upper(), sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
