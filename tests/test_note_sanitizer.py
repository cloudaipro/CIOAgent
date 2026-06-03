"""note_sanitizer: LLM figures-salvage layer with the regex firewall as contract.

All tests use a fake async `asker` (no network). They verify every outcome path:
salvage, fast-path skip, all-figure reject, retry-then-accept, retry-then-reject,
and fail-safe deferral when the model is unavailable.
"""
import asyncio
import json

from cio.committee import note_sanitizer as ns


def _run(coro):
    return asyncio.run(coro)


def _asker_returning(*responses):
    """Build a fake asker that yields the given responses in order (last repeats)."""
    calls = {"n": 0}

    async def asker(system_prompt, user_prompt, role_key=None, service=None, model=None):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    asker.calls = calls
    return asker


def _json(clean, removed=None):
    return json.dumps({"clean": clean, "removed": removed or []})


def test_fast_path_skips_llm_when_already_clean():
    """A figure-free note returns unchanged WITHOUT calling the model."""
    asker = _asker_returning("SHOULD NOT BE USED")
    out = _run(ns.sanitize("AAPL has a durable, widening moat", "AAPL", asker))
    assert out == "AAPL has a durable, widening moat"
    assert asker.calls["n"] == 0  # LLM never invoked


def test_salvages_insight_and_strips_figure():
    """A figure-laden note is rewritten to a clean, qualitative one."""
    clean = "AAPL's exceptional profitability proves a durable moat"
    asker = _asker_returning(_json(clean, ["141% ROE", "27% margins"]))
    out = _run(ns.sanitize("AAPL's 141% ROE and 27% margins prove a durable moat", "AAPL", asker))
    assert out == clean
    assert not ns.memory._looks_like_figure(out)
    assert asker.calls["n"] == 1


def test_all_figure_note_is_rejected():
    """If nothing qualitative survives, sanitize returns None (drop)."""
    asker = _asker_returning(_json("", ["141% ROE"]))
    out = _run(ns.sanitize("ROE is 141%", "AAPL", asker))
    assert out is None


def test_retry_then_accept():
    """First rewrite still has a figure; retry feeds the leak back and succeeds."""
    dirty = _json("margins around 27% stay strong")     # still a figure
    clean = _json("margins stay structurally strong")    # clean
    asker = _asker_returning(dirty, clean)
    out = _run(ns.sanitize("27% margins are strong", "AAPL", asker))
    assert out == "margins stay structurally strong"
    assert asker.calls["n"] == 2  # one retry used


def test_retry_exhausted_then_reject():
    """If every rewrite still carries a figure, reject rather than store a leak."""
    dirty = _json("still 27% margins")
    asker = _asker_returning(dirty)  # always dirty
    out = _run(ns.sanitize("27% margins", "AAPL", asker, max_retries=1))
    assert out is None
    assert asker.calls["n"] == 2  # initial + one retry, both dirty


def test_unavailable_model_defers_to_firewall():
    """Empty model response → return ORIGINAL text so the regex firewall decides."""
    asker = _asker_returning("")  # offline / no key / budget exhausted
    original = "AAPL's 141% ROE proves the moat"
    out = _run(ns.sanitize(original, "AAPL", asker))
    assert out == original  # unchanged; save_note's regex firewall will reject it


def test_asker_exception_defers_to_firewall():
    """Asker raising must not break the run — defer to the regex firewall."""
    async def boom(*a, **k):
        raise RuntimeError("api down")
    original = "AAPL's 141% ROE proves the moat"
    out = _run(ns.sanitize(original, "AAPL", boom))
    assert out == original


def test_unparseable_response_retries_then_rejects():
    """Non-JSON garbage yields empty clean → treated as nothing salvageable."""
    asker = _asker_returning("not json at all")
    out = _run(ns.sanitize("27% margins strong", "AAPL", asker))
    assert out is None


# ---- audit callback -------------------------------------------------------

def _audit_sink():
    events = []
    return events, (lambda action, original, cleaned, removed:
                    events.append((action, original, cleaned, tuple(removed))))


def test_audit_fired_on_clean():
    events, sink = _audit_sink()
    clean = "AAPL's exceptional profitability proves a durable moat"
    asker = _asker_returning(_json(clean, ["141% ROE", "27% margins"]))
    _run(ns.sanitize("AAPL's 141% ROE and 27% margins prove a moat", "AAPL", asker, audit=sink))
    assert len(events) == 1
    assert events[0][0] == "cleaned"
    assert events[0][2] == clean
    assert events[0][3] == ("141% ROE", "27% margins")


def test_audit_fired_on_reject():
    events, sink = _audit_sink()
    asker = _asker_returning(_json("", ["141% ROE"]))
    _run(ns.sanitize("ROE is 141%", "AAPL", asker, audit=sink))
    assert [e[0] for e in events] == ["rejected"]


def test_audit_silent_on_fast_path_and_unavailable():
    """No figure action taken → audit must NOT fire (fast-path clean, model down)."""
    events, sink = _audit_sink()
    _run(ns.sanitize("durable moat widening", "AAPL", _asker_returning("X"), audit=sink))
    _run(ns.sanitize("AAPL's 141% ROE proves moat", "AAPL", _asker_returning(""), audit=sink))
    assert events == []
