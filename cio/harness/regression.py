"""regression.py — golden seesaw suite for the processor/profile layer (item 3).

The operational-mirror's catastrophic-forgetting defense, in code. HarnessX's
seesaw constraint says: an edit that improves one case must not silently regress
another (the τ³-Bench Telecom −14% failure — five same-type edits accumulated
sub-threshold coupling until a tip). We can't run that over a market verifier, but
we CAN pin the processor/profile layer (items 1 + 4) with a fixed set of golden
(event → expectation) pairs that must hold after ANY processor edit or profile
change.

Run it in CI (tests/test_harness_x.py) and, optionally, before admitting a config
edit: if a change flips a golden, it is a regression — refuse it. The goldens
deliberately include a variant-isolation case (the same plan that BLOCKS under
``committee`` must NOT block under ``monitor``, which carries no consistency
processor), so a careless edit that widens a guard into the wrong situation is
caught here.

Deterministic: the citation goldens inject a dict-backed fake resolver; no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from .runloop import Hook, Event
from . import processors as _processors


def _plan_block(d: dict) -> str:
    return "```plan\n" + json.dumps(d) + "\n```"


def _ev(text: str, profile: str = "committee", sources=None, material: bool = False,
        resolver: Callable | None = None) -> Event:
    return Event(hook=Hook.AFTER_MODEL, text=text, profile=profile,
                 sources=sources or [], resolver=resolver,
                 extra={"material": material})


# A fake resolver shared by the citation goldens (dead = absent → None).
_FAKE_RESOLVER = (lambda table: (lambda u: table.get(u)))(
    {"https://live.sec.gov/x": 200})


@dataclass
class Golden:
    name: str
    event: Event
    expect_blocked: bool = False
    expect_codes: list[str] = field(default_factory=list)
    expect_note: bool = False


_MCHP_PLAN = _plan_block({"symbol": "MCHP", "entry_kind": "limit",
                          "entry_price": 97.5, "current_price": 99.0,
                          "market_bias": "up"})
_CLEAN_PLAN = _plan_block({"symbol": "X", "entry_kind": "breakout",
                           "entry_price": 100, "current_price": 99,
                           "stop_price": 95, "target_price": 110,
                           "market_bias": "up"})

GOLDENS: list[Golden] = [
    # V1 still catches the motivating defect under the deep-decision variant.
    Golden("mchp_committee_blocks",
           _ev(f"Here is the setup.\n{_MCHP_PLAN}", profile="committee"),
           expect_blocked=True, expect_codes=["R1_REL_WEAKNESS"], expect_note=True),
    # A coherent plan with relative strength passes clean — no over-blocking.
    Golden("clean_committee_ok",
           _ev(f"Entry idea.\n{_CLEAN_PLAN}", profile="committee"),
           expect_blocked=False, expect_codes=[], expect_note=False),
    # VARIANT ISOLATION: the SAME blocking plan under `monitor` (no v1 processor)
    # must NOT block — a daily watchlist pass emits no plan, so v1 is absent and
    # cannot fire. Proves a guard stays in its situation.
    Golden("monitor_skips_consistency",
           _ev(f"Watchlist note.\n{_MCHP_PLAN}", profile="monitor"),
           expect_blocked=False, expect_codes=[], expect_note=False),
    # V2 fails closed on a dead cited URL backing a material claim.
    Golden("dead_url_committee_blocks",
           _ev("Intel won the Apple deal [1].", profile="committee", material=True,
               sources=[{"url": "https://dead.example/intel"}],
               resolver=_FAKE_RESOLVER),
           expect_blocked=True, expect_codes=["C_DEAD_URL"], expect_note=True),
    # A plain answer with no plan and no sources is a clean no-op (no false note).
    Golden("benign_no_note",
           _ev("The portfolio is up 1.2% today.", profile="committee"),
           expect_blocked=False, expect_codes=[], expect_note=False),
]


def check_regression(build: Callable[[str], object] = _processors.build_runloop
                     ) -> list[dict]:
    """Fire every golden through ``build(profile)`` and return a list of failures
    (empty ⇒ all goldens hold). ``build`` is injectable so a test can prove the
    detector catches a deliberately broken configuration."""
    failures: list[dict] = []
    for g in GOLDENS:
        rl = build(g.event.profile)
        res = rl.fire(g.event)
        codes = res.codes()
        problems = []
        if res.blocked is not g.expect_blocked:
            problems.append(f"blocked={res.blocked} expected {g.expect_blocked}")
        for c in g.expect_codes:
            if c not in codes:
                problems.append(f"missing code {c}")
        if bool(res.note) is not g.expect_note:
            problems.append(f"note={bool(res.note)} expected {g.expect_note}")
        if problems:
            failures.append({"golden": g.name, "problems": problems,
                             "codes": codes})
    return failures
