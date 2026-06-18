"""candidates.py — owner-committed implementations for proposed skills.

The self-authoring loop is deliberately asymmetric:
  * the AGENT may only PROPOSE a skill (a name, a trigger, a human-readable
    rule_spec) — it writes a PROPOSED record to store.py and nothing else.
  * a HUMAN turns an approved proposal into a real check by committing the
    implementation HERE, as a (check_fn, cases) pair keyed by the skill name.

`python -m cio.harness.admin verify <id>` looks the skill up by name in
CANDIDATES and runs the cases. No entry here ⇒ no automated verification ⇒ the
skill cannot pass the gate (except via an explicit owner --manual override).
This is what keeps model-authored text out of the execution path: only code a
human wrote and committed ever runs.

To wire a proposed skill:
  1. read the proposal:  python -m cio.harness.admin show <id>
  2. implement the check + held-out cases below, keyed by the proposal's NAME
  3. verify:             python -m cio.harness.admin verify <id>
  4. approve & activate: python -m cio.harness.admin approve <id> --by <you>
                         python -m cio.harness.admin activate <id>
"""
from __future__ import annotations

from typing import Any, Callable

from .registry import VerifyCase

# name -> (check_fn, [VerifyCase, ...])
CANDIDATES: dict[str, tuple[Callable[[Any], Any], list[VerifyCase]]] = {}


def register(name: str, check: Callable[[Any], Any], cases: list[VerifyCase]) -> None:
    """Register an owner-committed implementation for a proposed skill name."""
    CANDIDATES[name] = (check, cases)


def get(name: str):
    return CANDIDATES.get(name)
