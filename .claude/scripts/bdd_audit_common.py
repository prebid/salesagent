"""Shared helpers for BDD audit meta-tooling scripts.

Used by ``bdd_full_audit.py``, ``audit_xfails.py``, and
``cross_reference_audit.py`` so transport / scenario / UC parsing and
graduation bucketing cannot drift apart.

Transport vocabulary mirrors ``tests/bdd/conftest.py`` parametrize ids after
#1417: ``a2a`` / ``mcp`` / ``rest``, plus ``e2e_rest`` when in-network is
enabled. Legacy ``impl`` remains recognized for historical bdd.json files but
is never required for graduation.

Report-bucket vocabulary (intentional split, same underlying coverage grade):
``bdd_full_audit`` labels partial xpass as ``PARTIAL_XPASS`` / full as
``GRADUATE``; ``audit_xfails`` keeps ``PARTIAL_PASS`` / ``STALE``. Do not
unify the report tokens without a deliberate cross-script rename.

``parse_conftest_xfail_tags`` stays per-script on purpose: ``audit_xfails``
returns ``tag → (reason, mechanism)`` for classification, while
``bdd_full_audit`` returns the simpler ``tag → reason`` map for report
titles. Those contracts diverge; sharing a single parser would force one
consumer onto the other's shape.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# `[` anchor + `(?:-|])` tail delimiter recognizes full ids (including
# `e2e_rest`); alternation order is not load-bearing for that match.
_TRANSPORT_RE = re.compile(r"\[(e2e_rest|impl|a2a|mcp|rest)(?:-|])")

# Outcomes that count as "this transport passed for the scenario base".
_PASSING_OUTCOMES = frozenset({"passed", "xpassed"})
# Outcomes that block graduation for a present transport.
_FAILING_OUTCOMES = frozenset({"failed", "xfailed"})


def extract_transport(nodeid: str) -> str | None:
    """Extract transport id from a parametrized pytest nodeid.

    Recognizes ``a2a``, ``mcp``, ``rest``, ``e2e_rest``, and legacy ``impl``.
    Returns ``None`` when the nodeid has no known transport param (e.g. admin
    scenarios without wire-transport parametrization).
    """
    m = _TRANSPORT_RE.search(nodeid)
    return m.group(1) if m else None


def extract_scenario_base(nodeid: str) -> str:
    """Strip the trailing parametrize bracket suffix to get the scenario base."""
    return re.sub(r"\[.*\]$", "", nodeid)


def extract_uc(text: str) -> str:
    """Extract use case id from a nodeid or path (e.g. ``UC-004``).

    Matches ``test_ucNNN`` / ``ucNNN`` (case-insensitive). Returns ``GENERIC``
    when no use-case token is present.
    """
    m = re.search(r"(?:test_)?uc(\d+)", text, re.IGNORECASE)
    return f"UC-{m.group(1)}" if m else "GENERIC"


def extract_longrepr_e_line(longrepr: str) -> str:
    """Return the first pytest ``E `` error line from a longrepr, or ``\"\"``."""
    for line in longrepr.split("\n"):
        stripped = line.strip()
        if stripped.startswith("E "):
            return stripped[2:].strip()
    return ""


def transport_coverage(
    outcomes_by_transport: Mapping[str, str],
) -> tuple[bool, set[str], set[str]]:
    """Grade a scenario base from per-transport outcomes.

    Graduation is relative to transports *present for that base* in the result
    set — not a hardcoded four-transport universe. A UC that only parametrizes
    ``a2a``+``mcp`` graduates when both pass; ``rest`` is not "missing".

    Returns ``(graduates, passing, missing)`` where ``missing`` is the set of
    present transports that did not pass (failed/xfailed or unknown outcome).
    """
    present = set(outcomes_by_transport)
    if not present:
        return False, set(), set()

    passing = {t for t, outcome in outcomes_by_transport.items() if outcome in _PASSING_OUTCOMES}
    failing = {t for t, outcome in outcomes_by_transport.items() if outcome in _FAILING_OUTCOMES}
    missing = present - passing
    # Graduate only when every present transport passed and none failed/xfailed.
    # (failing ⊆ missing for standard pytest outcomes; keep both checks so an
    # unexpected outcome cannot silently graduate.)
    graduates = bool(present) and present <= passing and not failing
    return graduates, passing, missing


def _worst_transport_outcome(outcomes: list[str]) -> str:
    """Aggregate example-row outcomes for one transport.

    A transport passes only if every example passed/xpassed and none
    failed/xfailed. Any failing example dominates; otherwise the first
    non-passing outcome blocks graduation; else prefer ``passed`` over
    ``xpassed``.
    """
    if any(o in _FAILING_OUTCOMES for o in outcomes):
        return "failed" if "failed" in outcomes else "xfailed"
    non_passing = [o for o in outcomes if o not in _PASSING_OUTCOMES]
    if non_passing:
        return non_passing[0]
    if "passed" in outcomes:
        return "passed"
    return "xpassed"


def outcomes_by_transport_for_base(
    base: str,
    nodeid_outcomes: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Build ``{transport: aggregated_outcome}`` for one scenario base.

    Scenario outlines emit one bdd.json entry per example row per transport.
    Aggregate a worst-outcome per transport: the transport passes only if
    every example passed/xpassed and none failed/xfailed.
    """
    collected: dict[str, list[str]] = {}
    for nodeid, outcome in nodeid_outcomes:
        if extract_scenario_base(nodeid) != base:
            continue
        transport = extract_transport(nodeid)
        if transport:
            collected.setdefault(transport, []).append(outcome)
    return {t: _worst_transport_outcome(outs) for t, outs in collected.items()}
