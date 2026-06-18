"""admin.py — owner CLI for the self-authoring skill gate.

This is the human-in-the-loop surface. The agent can PROPOSE skills; only the
owner can move them through verification, approval, and activation here. Run:

    python -m cio.harness.admin list
    python -m cio.harness.admin show <id>
    python -m cio.harness.admin verify <id>            # runs committed cases (candidates.py)
    python -m cio.harness.admin verify <id> --manual --note "ran pytest, green"
    python -m cio.harness.admin approve <id> --by alex  # REFUSED unless VERIFIED
    python -m cio.harness.admin activate <id>           # REFUSED unless APPROVED
    python -m cio.harness.admin reject <id>
    python -m cio.harness.admin retire <id>

Gate ordering is enforced in store.transition(): approve requires VERIFIED,
activate requires APPROVED. The owner cannot skip verification.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import store


def _verify(sid: str, manual: bool, note: str, path, candidates_map=None) -> dict:
    """Verify a proposal. Preferred path: run the owner-committed (check, cases)
    from candidates.py. Fallback: an explicit --manual owner attestation."""
    rec = store.get(sid, path=path)
    if rec is None:
        return {"ok": False, "reason": f"unknown skill: {sid}"}

    if candidates_map is None:
        try:
            from . import candidates
            candidates_map = candidates.CANDIDATES
        except Exception:
            candidates_map = {}

    cand = candidates_map.get(rec["name"])
    if cand is not None:
        check, cases = cand
        if not cases:
            return {"ok": False, "reason": "candidate has no cases"}
        passed, results = 0, []
        for c in cases:
            try:
                ok = c.passes(check(c.input))
            except Exception as e:
                ok = False
                results.append({"case": c.name, "ok": False, "error": type(e).__name__})
                continue
            passed += int(ok)
            results.append({"case": c.name, "ok": ok})
        rate = passed / len(cases)
        if rate >= 1.0:
            return store.transition(sid, "verify", "ci",
                                    {"rate": rate, "results": results}, path=path)
        store.transition(sid, "reject", "ci", {"rate": rate, "results": results}, path=path)
        return {"ok": False, "reason": f"verification failed: {passed}/{len(cases)} cases",
                "results": results}

    if manual:
        return store.transition(sid, "verify", "owner",
                                {"manual": True, "note": note}, path=path)
    return {"ok": False, "reason": (
        f"no committed implementation for '{rec['name']}' in candidates.py. "
        "Implement (check, cases) there and re-run, or pass --manual once tests are green.")}


def run(argv: list[str], path=store.DEFAULT_STORE) -> int:
    """Argv-driven entry (testable). Returns a process exit code."""
    ap = argparse.ArgumentParser(prog="cio.harness.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_show = sub.add_parser("show"); p_show.add_argument("id")
    p_ver = sub.add_parser("verify"); p_ver.add_argument("id")
    p_ver.add_argument("--manual", action="store_true")
    p_ver.add_argument("--note", default="")
    p_app = sub.add_parser("approve"); p_app.add_argument("id"); p_app.add_argument("--by", required=True)
    p_act = sub.add_parser("activate"); p_act.add_argument("id")
    p_rej = sub.add_parser("reject"); p_rej.add_argument("id")
    p_ret = sub.add_parser("retire"); p_ret.add_argument("id")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        rows = store.all_records(path=path)
        if not rows:
            print("(no skills proposed yet)")
        for r in rows:
            print(f"{r['id']}  {r['status_label']:<9}  {r['kind']:<9}  {r['name']}")
        return 0

    if args.cmd == "show":
        rec = store.get(args.id, path=path)
        if rec is None:
            print(f"unknown skill: {args.id}"); return 1
        print(json.dumps(rec, indent=2))
        return 0

    if args.cmd == "verify":
        res = _verify(args.id, args.manual, args.note, path)
    elif args.cmd == "approve":
        res = store.transition(args.id, "approve", args.by, path=path)
    elif args.cmd == "activate":
        res = store.transition(args.id, "activate", "owner", path=path)
    elif args.cmd == "reject":
        res = store.transition(args.id, "reject", "owner", path=path)
    elif args.cmd == "retire":
        res = store.transition(args.id, "retire", "owner", path=path)
    else:
        res = {"ok": False, "reason": "unknown command"}

    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


def main() -> None:  # pragma: no cover
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    main()
