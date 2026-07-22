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
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# e2e_rest before rest so extract_transport does not truncate the id.
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


def outcomes_by_transport_for_base(
    base: str,
    nodeid_outcomes: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Build ``{transport: outcome}`` for one scenario base.

    When the same transport appears more than once, the last outcome wins
    (deterministic for a single bdd.json entry per transport).
    """
    out: dict[str, str] = {}
    for nodeid, outcome in nodeid_outcomes:
        if extract_scenario_base(nodeid) != base:
            continue
        transport = extract_transport(nodeid)
        if transport:
            out[transport] = outcome
    return out
