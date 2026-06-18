"""runloop.py — typed processor/hook layer (HarnessX composition, item 1).

Why this exists: V1/V2 used to fire only when the *model elected to call the tool*
(a SYSTEM_PROMPT instruction the model could forget — exactly the MCHP failure the
gate was built to stop). HarnessX's composition layer moves enforcement to a
*processor bound to a lifecycle hook*: the run loop fires it unconditionally on
every turn, so the model can no longer skip it.

This module is the deterministic, never-raises substrate (mirrors the TIRF house
style — every field a safe default, a throwing processor never breaks the turn).
The concrete after_model processors live in processors.py; the live wiring point
is cio/agent.py:ask(), where the run loop fires on the assistant's response right
next to the existing Haiku verifier (_run_verifier), which already follows the
"return a note to append" contract this layer reuses.

Hooks mirror the paper's lifecycle (Table 1). Only AFTER_MODEL is wired today;
the others are defined so a future processor (e.g. a before_tool guard) drops in
without a type change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Protocol, runtime_checkable

from .models import Finding, Severity


class Hook(IntEnum):
    """Lifecycle points a processor can attach to (subset of HarnessX Table 1)."""
    BEFORE_MODEL = 1
    AFTER_MODEL = 2
    BEFORE_TOOL = 3
    AFTER_TOOL = 4


@dataclass
class Event:
    """One unit the run loop hands to each processor for a hook.

    All fields optional/defaulted so a bare Event still runs every processor's
    skip-path instead of crashing. ``resolver`` is the injected URL resolver for
    the citation processor (the live bot passes the real http_resolver; tests pass
    a dict-backed fake). ``extra`` carries hook-specific flags (e.g. ``material``).
    """
    hook: Hook = Hook.AFTER_MODEL
    text: str = ""                                  # assistant response (after_model)
    scope: str = "global"
    profile: str = "committee"
    sources: list[dict] = field(default_factory=list)   # structured cited sources
    resolver: Callable[[str], "int | None"] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    """What one processor returns. ``note`` is a user-facing ⚠️ string to append
    (or "" for nothing); ``intercept`` asks the loop to replace the response with
    ``replacement`` (no default processor uses it yet — annotate-only is the safe
    live behavior, matching _run_verifier)."""
    name: str = ""
    findings: list[Finding] = field(default_factory=list)
    note: str = ""
    intercept: bool = False
    replacement: str = ""

    @property
    def blocked(self) -> bool:
        return any(f.severity >= Severity.BLOCK for f in self.findings)

    def codes(self) -> list[str]:
        return [f.code for f in self.findings]


@runtime_checkable
class Processor(Protocol):
    """A processor consumes an Event for its hook and returns a ProcessorResult.
    Stateless by convention; the run loop validates nothing beyond the type."""
    name: str
    hook: Hook

    def process(self, event: Event) -> ProcessorResult: ...


@dataclass
class RunResult:
    """Aggregate of every processor that fired for a hook."""
    results: list[ProcessorResult] = field(default_factory=list)
    note: str = ""
    intercept: bool = False
    replacement: str = ""

    @property
    def blocked(self) -> bool:
        return any(r.blocked for r in self.results)

    def codes(self) -> list[str]:
        return [c for r in self.results for c in r.codes()]

    def to_row(self) -> dict[str, Any]:
        return {
            "note": self.note,
            "blocked": self.blocked,
            "intercept": self.intercept,
            "codes": self.codes(),
            "results": [
                {"name": r.name, "note": r.note, "blocked": r.blocked,
                 "codes": r.codes()}
                for r in self.results
            ],
        }


class Runloop:
    """Holds processors keyed by hook and fires them in insertion order.

    ``fire`` NEVER raises: a processor that throws is recorded as an empty result
    and the turn proceeds (a broken guard must not break the bot). Notes are
    concatenated; the first intercept wins.
    """

    def __init__(self, processors: dict[Hook, list[Processor]] | None = None):
        self._procs: dict[Hook, list[Processor]] = processors or {}

    def add(self, proc: Processor) -> "Runloop":
        self._procs.setdefault(proc.hook, []).append(proc)
        return self

    def processors(self, hook: Hook) -> list[Processor]:
        return list(self._procs.get(hook, []))

    def fire(self, event: Event) -> RunResult:
        out = RunResult()
        notes: list[str] = []
        for proc in self._procs.get(event.hook, []):
            try:
                r = proc.process(event)
            except Exception:               # a throwing processor never breaks the turn
                r = ProcessorResult(name=getattr(proc, "name", "?"))
            if r is None:
                continue
            out.results.append(r)
            if r.note:
                notes.append(r.note)
            if r.intercept and not out.intercept:
                out.intercept = True
                out.replacement = r.replacement
        out.note = "\n".join(notes)
        return out
