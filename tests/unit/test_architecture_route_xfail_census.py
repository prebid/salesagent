"""Ratchet: the population of BDD scenarios xfailed at FIXTURE SETUP may only shrink.

``tests/bdd/conftest.py::_run_env_route`` begins

    if route.xfail_reason is not None:
        pytest.xfail(route.xfail_reason)

so an ``EnvRoute`` row carrying a reason xfails the scenario before its env is built and
before one step runs. The scenario is still bound, still collected, and still counted by
every guard that counts scenarios -- and it grades nothing.

That is a fifth routing mechanism, and the one with no baseline. The other four are
auditable by reading a file: a conftest xfail tag set, ``dormant_scenarios.txt``,
``e2e_rest_known_failures.txt``, a ``pytest.mark``. This one is a ``when=`` predicate on a
registry row, and the three largest rows are ``lambda m: True`` catch-alls -- they claim
every scenario of their UC that no earlier row took, so a scenario can join the population
by being WRITTEN, with no edit to any list. Measured when this guard landed: 819 of 2879
routed combinations, 28%.

WHY A RATCHET AND NOT A REFUSAL, which is the opposite of the sibling decision.
``test_architecture_bdd_unrouted_is_refused`` refuses its mechanism outright because its
population is ZERO, and a zero-entry baseline is only something to grow. Here the
population is 819 and every row names a real unfinished harness, so refusing would delete
work rather than measure it. A shrink-only count is what fits a non-empty population that
should drain.

WHAT THIS GUARD DOES NOT CLAIM. It does not say these scenarios SHOULD xfail, and it does
not grade the reason strings -- ``test_architecture_uc010_dormancy_citations`` already
grades that each UC-010 dormant tag's reason cites its own issue, and
``test_architecture_bdd_ext_fallback_xfail`` already grades that each ext-split UC HAS a
not-wired row. Those two protect the rows' CONTENT and EXISTENCE. Nothing protected their
SIZE, which is all this adds.

TRANSPORT-INDEPENDENT BY CONSTRUCTION. The item count depends on ``BDD_E2E_ENABLED``: the
same scenarios collect on three in-process transports or on four
(salesagent-8qela.151), so 819 combinations are 2457 items or 3276 depending on a flag no
guard controls. This counts the combination -- the nodeid with its transport
parametrization removed -- which is identical either way.

DERIVED, NEVER DECLARED, AND FROM ONE COLLECTION. The rows come from
``collect_bdd_items_with_markers`` -- the SAME nested collection
``test_architecture_env_route_agreement`` grades on, which already carries its own
truncation floor and emits each item's marker set through the pinned accessor. The verdict
per row comes from the PRODUCTION resolver, ``storyboard_spec.resolve_env_route`` over the
live ``ENV_ROUTES``. A hand-written list of UCs, or a second collector, would be a second
derivation and would drift from the routing it claims to describe.

WHY THIS IS NOT A ``RatchetCounter``. ``test_architecture_ratchet_enforcement`` requires
every registered ratchet to name an enforcement that actually runs, because a ratchet filed
only under ``stages: [pre-push]`` fires nowhere in this workflow. Its own message names the
two acceptable homes: "'guard' (a test in the always-run unit suite)" or "'gate' (the
quality tox env)". This is the first -- it runs on every unit-suite invocation, in ~13s --
so it needs no ``.pre-commit-coverage-map.yml`` entry and no committed baseline file. The
counters registered there are line-counting hooks over source text; this one derives its
population from a collection, which is a different shape with the same discipline.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    collect_bdd_items_with_markers,
)

#: Per-route ceiling on routed (function, non-transport-parametrization) combinations.
#:
#: SHRINK-ONLY. Wiring a harness LOWERS an entry in the same change, as a reviewed edit;
#: a row whose count rises is a scenario that joined a catch-all, which is the regression
#: this file exists to catch. Raising an entry is never the fix.
#:
#: A row ABSENT here is a row that must not exist: a new ``xfail_reason`` route fails
#: :func:`test_every_setup_xfail_route_is_declared` until it is added deliberately, which
#: is the door the catch-alls left open.
#:
#: Measured at integrate/sdk9-lanes b6e2554c5 by this file's own derivation.
_CEILINGS: dict[str, int] = {
    # The catch-all: `when=_uc("UC-010", lambda m: True)`. It claims every scenario of its
    # UC that no earlier row took, so it is the entry that can grow without anyone editing
    # a list. The UC-002 and UC-003 catch-alls (385 and 381) drained to zero: their rows
    # lost their xfail_reason, and what fails is named per scenario in the BDD conftest's
    # _UC002_UC003_LEDGER (measure_innet_291026_1202).
    # 21 at integrate/wave1 a3687d971; 18 after wave 6 deleted the
    # T-UC-010-degradation-creative outline and wired T-UC-010-local-inline-creative-management.
    "uc010-not-wired": 18,
    # Per-tag UC-010 dormancy rows, generated from `_UC010_PARKED_TAGS`. Their reasons
    # cite issues and `test_architecture_uc010_dormancy_citations` grades the citation;
    # only the size was uncounted.
    "uc010-dormant-T-UC-010-v31-identity-key-origins": 4,
    "uc010-dormant-T-UC-010-v31-governance-aware": 2,
    "uc010-dormant-T-UC-010-v31-vendor-metric-optimization": 1,
    "uc010-dormant-T-UC-010-v31-creative-specs": 1,
    "uc010-dormant-T-UC-010-v31-identity-compromise-notification": 1,
    "uc010-dormant-T-UC-010-v31-content-standards-block": 1,
    "uc010-dormant-T-UC-010-v31-identity-brand-json-url": 1,
}

#: The population floor. A derivation that matches nothing is silently green, and that is
#: this family's failure mode -- so a census that finds almost nothing fails rather than
#: reporting a clean pass. This catches a BROKEN DERIVATION, it does not pin the count,
#: which ``_CEILINGS`` does per row. 400 -> 15 when the UC-002 and UC-003 catch-alls drained:
#: what remains is the UC-010 catch-all and its dormancy rows (ceilings sum to 42; a static
#: resolution of the bound features over ENV_ROUTES gives 34), so the floor sits well below
#: that and above zero, which is the case it exists to refuse.
_MIN_ROUTED_COMBINATIONS = 15

#: The explicit lists that replaced the UC-002 and UC-003 catch-alls in the BDD conftest,
#: each as (entries, nodeids matched). The catch-alls' 385 and 381 above were the only
#: counter on this population; these take their place.
#:
#: SHRINK-ONLY, for the same reason as ``_CEILINGS``. A new entry, or a ``rows=`` widened to
#: claim more Examples rows, raises a count and fails; graduating a row lowers it, and the
#: ceiling is lowered in that same change.
#:
#: The nodeid count is over the four transports every UC-002 / UC-003 scenario runs on,
#: rebuilt from each collected combination, so it is the same with ``BDD_E2E_ENABLED`` on
#: or off (the nested collection drops it). Measured by resolving both lists over the bound
#: features at the commit that named each park's rows (measure_innet_291026_1202).
_LEDGER_CEILINGS: dict[str, tuple[int, int]] = {
    # Wave 5 graduated 65 ledger and 52 park entries (integrate/wave1 a3687d971), measured
    # by this file's derivation over the merged tree. 42/192 -> 41/188 when the
    # T-UC-002-partition-pricing-option-xor cpa_model row graduated (lane w7-cpa-stub).
    "_UC002_UC003_LEDGER": (41, 188),
    "_UC002_UC003_VACUOUS_PARKS": (3, 16),
    # The two UC-003 missing-identifier rows on rest and e2e_rest: 2 rows x 2 transports.
    "_TRANSPORT_INEXPRESSIBLE_ROWS": (2, 4),
}

#: The one list whose entries run (strict xfail). Every other list is xfail(run=False), so an
#: entry there that names no rows would silently claim an Examples row added later.
_RUNNING_LIST = "_UC002_UC003_LEDGER"
#: The list whose entries a transport cannot express: each must leave a transport that grades.
_INEXPRESSIBLE_LIST = "_TRANSPORT_INEXPRESSIBLE_ROWS"

_LEDGER_TRANSPORTS = ("a2a", "mcp", "rest", "e2e_rest")

_TRANSPORTS = ("mcp", "a2a", "rest", "e2e_rest", "e2e_mcp", "e2e_a2a", "e2e_admin")

_BRACKET = re.compile(r"\[(?P<params>.*)\]$")


def _combination(nodeid: str) -> str:
    """The nodeid with its transport parametrization removed.

    pytest-bdd parametrizes the transport FIRST inside the bracket, so dropping a leading
    ``<transport>-`` leaves the scenario's own parametrization intact. Only a known
    transport is stripped: a blind ``partition("-")`` would eat the first token of any
    scenario whose own parameter happens to lead, and silently merge distinct rows.
    """
    match = _BRACKET.search(nodeid)
    if match is None:
        return nodeid
    head, params = nodeid[: match.start()], match.group("params")
    for transport in _TRANSPORTS:
        if params == transport:
            return head
        if params.startswith(f"{transport}-"):
            return f"{head}[{params[len(transport) + 1 :]}]"
    return nodeid


class _Census:
    """The partition of the collected suite by routing verdict."""

    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        from scripts.audit import storyboard_spec
        from tests.bdd.conftest import ENV_ROUTES

        self.collected = len(rows)
        self.wired = 0
        self.unrouted = 0
        self.combinations: dict[str, set[str]] = {}
        self.items: Counter[str] = Counter()
        for row in rows:
            names = frozenset(row["derived"] or row["markers"])
            route = storyboard_spec.resolve_env_route(names, ENV_ROUTES)
            if route is None:
                self.unrouted += 1
            elif route.xfail_reason is None:
                self.wired += 1
            else:
                self.combinations.setdefault(route.tag, set()).add(_combination(row["nodeid"]))
                self.items[route.tag] += 1

    @property
    def routed_combinations(self) -> int:
        return sum(len(combos) for combos in self.combinations.values())


@pytest.fixture(scope="module")
def census() -> _Census:
    return _Census(collect_bdd_items_with_markers())


@pytest.mark.arch_guard
def test_the_census_accounts_for_every_collected_item(census: _Census) -> None:
    """The partition is exhaustive, and it found a real population.

    Both halves matter. Without the floor, a derivation matching nothing satisfies every
    ceiling below. Without the partition, an item the resolver answered for in some third
    way would leave the arithmetic unnoticed.
    """
    accounted = census.wired + census.unrouted + sum(census.items.values())
    assert accounted == census.collected, (
        f"the partition accounts for {accounted} of {census.collected} collected items: "
        "wired + unrouted + setup-xfailed must be the whole collection"
    )
    assert census.routed_combinations >= _MIN_ROUTED_COMBINATIONS, (
        f"only {census.routed_combinations} routed combinations were found (expected at "
        f"least {_MIN_ROUTED_COMBINATIONS}); the resolver, the marker accessor or "
        "ENV_ROUTES changed shape and this census is grading nothing"
    )


@pytest.mark.arch_guard
def test_no_setup_xfail_route_grows(census: _Census) -> None:
    """Every route's routed-combination count is at or below its ceiling."""
    grown = {
        tag: (len(combos), _CEILINGS[tag])
        for tag, combos in sorted(census.combinations.items())
        if tag in _CEILINGS and len(combos) > _CEILINGS[tag]
    }
    assert not grown, (
        "setup-phase xfail routes grew. Those scenarios are bound, collected and counted, "
        "and they grade nothing -- wire the harness or move the scenario; do not raise the "
        f"ceiling: {grown}"
    )


@pytest.mark.arch_guard
def test_the_declared_routes_are_exactly_the_routed_ones(census: _Census) -> None:
    """``_CEILINGS``' key set must EQUAL the set of routes that xfail at setup.

    Two obligations in one equality, which is why this is the shared primitive's shape
    rather than two hand-rolled set differences:

    * a route present and UNDECLARED is a new placeholder row carrying any number of
      scenarios while the ceilings say nothing about it -- the catch-all door;
    * a route declared and ABSENT is a stale entry. Draining to zero is the GOAL, so a
      route that drains loses its entry in the same change, or this file accumulates
      ceilings nobody can grow and the next reader cannot tell which are live.

    ``assert_violations_match_allowlist`` reports those two separately, as "new violations"
    and "stale entries" -- exactly the distinction, reported the way every other allowlist
    in the suite reports it.
    """
    assert_violations_match_allowlist(
        {(tag,) for tag in census.combinations},
        {(tag,) for tag in _CEILINGS},
        fix_hint=(
            "A newly routed row needs a ceiling entry with its measured count, or should "
            "not exist. A stale entry means the harness was wired -- delete it, the work is "
            "done. The per-route COUNT is graded separately by "
            "test_no_setup_xfail_route_grows: a shrink-only numeric ceiling, which is not a "
            "set comparison and so is not expressible here."
        ),
    )


@pytest.mark.arch_guard
def test_the_combination_key_strips_the_transport_and_nothing_else() -> None:
    """``_combination`` is what makes the ceilings independent of ``BDD_E2E_ENABLED``.

    Both directions, because a key stripping too much merges distinct scenarios and
    under-counts, and one stripping too little counts a scenario once per transport and
    makes every ceiling depend on a flag no guard controls (salesagent-8qela.151).
    """
    node = "tests/bdd/test_uc003_update_media_buy.py::test_x"
    assert {
        _combination(f"{node}[{transport}-case_a-expected]") for transport in ("mcp", "a2a", "rest", "e2e_rest")
    } == {f"{node}[case_a-expected]"}
    assert _combination(f"{node}[mcp]") == node
    assert _combination(f"{node}[mcp-case_a]") != _combination(f"{node}[mcp-case_b]")
    assert _combination(node) == node
    # A parameter that merely LOOKS like a lead token is left alone: only a known
    # transport is stripped, so this scenario keeps both of its own parameters.
    assert _combination(f"{node}[restaurant-case_a]") == f"{node}[restaurant-case_a]"


@pytest.mark.arch_guard
def test_the_census_reads_the_production_routing_call() -> None:
    """The names this guard derives from are the ones the BDD conftest routes on.

    If ``_harness_env`` stopped calling ``resolve_env_route``, or stopped keying it on
    ``derive_marker_names``, or stopped xfailing on a reason, this census would still
    answer -- about a function nothing uses. A guard measuring a path production no longer
    takes is the defect salesagent-8qela.14 was filed about one level down, so the three
    lines it depends on are asserted rather than assumed.
    """
    source = (Path(__file__).resolve().parents[1] / "bdd" / "conftest.py").read_text(encoding="utf-8")
    for line in (
        "marker_names = derive_marker_names(request.node)",
        "storyboard_spec.resolve_env_route(marker_names, ENV_ROUTES)",
        "pytest.xfail(route.xfail_reason)",
    ):
        assert line in source, f"the production routing call changed shape: {line!r} is gone"


def _with_transport(combination: str, transport: str) -> str:
    """The inverse of :func:`_combination`: put *transport* back as the leading parameter."""
    match = _BRACKET.search(combination)
    if match is None:
        return f"{combination}[{transport}]"
    return f"{combination[: match.start()]}[{transport}-{match.group('params')}]"


def _ledger_violations(
    rows: tuple[dict[str, Any], ...],
    lists: dict[str, tuple[Any, ...]],
    ceilings: dict[str, tuple[int, int]],
) -> list[str]:
    """Every way the explicit row lists (the ledger, the parks, the inexpressible rows) break
    their rules, as messages.

    Four rules, resolved with the entries' own ``matches`` over the collected marker names
    (the names ``pytest_collection_modifyitems`` matches on):

    * each list stays at or under its (entries, nodeids) ceiling, and matches something;
    * no nodeid is matched by two lists -- only the first would apply, so the other is a
      dormant claim that swallows the row the day the first entry is graduated;
    * a run=False entry (every list but the ledger) that names no rows on an outline would
      claim a row added to it later, so it must name its rows;
    * an inexpressible entry names the transports it is inexpressible on and leaves at least
      one transport that grades the row; on every transport it would be a skip.
    """
    names_by_combination: dict[str, set[str]] = {}
    for row in rows:
        names_by_combination.setdefault(_combination(row["nodeid"]), set(row["markers"]))
    nodeids = [
        (_with_transport(combination, transport), names)
        for combination, names in names_by_combination.items()
        for transport in _LEDGER_TRANSPORTS
    ]
    from tests.bdd.conftest import _example_row

    violations: list[str] = []
    matched: dict[str, set[str]] = {}
    for name, entries in lists.items():
        matched[name] = set()
        for entry in entries:
            hits = {nodeid for nodeid, names in nodeids if entry.matches(names, nodeid)}
            matched[name] |= hits
            if name != _RUNNING_LIST and not entry.rows and any(_example_row(nodeid) for nodeid in hits):
                violations.append(f"{name}: {entry.tag} parks a Scenario Outline without naming its rows")
            if name == _INEXPRESSIBLE_LIST and set(entry.transports or _LEDGER_TRANSPORTS) >= set(_LEDGER_TRANSPORTS):
                violations.append(f"{name}: {entry.tag} leaves no transport that grades the row")
        max_entries, max_nodeids = ceilings[name]
        if len(entries) > max_entries:
            violations.append(f"{name} grew to {len(entries)} entries (ceiling {max_entries})")
        if len(matched[name]) > max_nodeids:
            violations.append(f"{name} matches {len(matched[name])} nodeids (ceiling {max_nodeids})")
        if not matched[name]:
            violations.append(f"{name} matches no collected nodeid: the derivation is grading nothing")
    for first, second in combinations(matched, 2):
        for nodeid in sorted(matched[first] & matched[second]):
            violations.append(f"{nodeid} is matched by both {first} and {second}")
    return violations


@pytest.mark.arch_guard
def test_the_uc002_uc003_ledger_only_shrinks() -> None:
    """The explicit ledger, its parks and the inexpressible rows are held to their measured size and shape."""
    from tests.bdd.conftest import _TRANSPORT_INEXPRESSIBLE_ROWS, _UC002_UC003_LEDGER, _UC002_UC003_VACUOUS_PARKS

    lists = {
        "_UC002_UC003_LEDGER": _UC002_UC003_LEDGER,
        "_UC002_UC003_VACUOUS_PARKS": _UC002_UC003_VACUOUS_PARKS,
        "_TRANSPORT_INEXPRESSIBLE_ROWS": _TRANSPORT_INEXPRESSIBLE_ROWS,
    }
    violations = _ledger_violations(collect_bdd_items_with_markers(), lists, _LEDGER_CEILINGS)
    assert not violations, (
        "the explicit row lists broke their rules. A failing scenario is fixed or measured, "
        "not added; a park names the rows it parks; never raise a ceiling:\n  " + "\n  ".join(violations)
    )


@pytest.mark.arch_guard
def test_the_ledger_rules_refuse_growth_overlap_and_a_rowless_outline_park() -> None:
    """The rules above can return the other answer: each breach, made on purpose, is reported."""
    from tests.bdd.conftest import _CatchallXfail

    rows = (
        {"nodeid": "t.py::test_outline[mcp-row_a-x]", "markers": ["T-A"]},
        {"nodeid": "t.py::test_outline[mcp-row_b-y]", "markers": ["T-A"]},
        {"nodeid": "t.py::test_outline[mcp-row_c-z]", "markers": ["T-A"]},
        {"nodeid": "t.py::test_plain[mcp]", "markers": ["T-B"]},
    )
    ledger = (_CatchallXfail("T-A", "fails", rows=("row_a",)),)
    parks = (_CatchallXfail("T-A", "vacuous", rows=("row_b",)), _CatchallXfail("T-B", "vacuous"))
    inexpressible = (_CatchallXfail("T-A", "no request", rows=("row_c",), transports=("rest", "e2e_rest")),)
    ceilings = {
        "_UC002_UC003_LEDGER": (1, 4),
        "_UC002_UC003_VACUOUS_PARKS": (2, 8),
        "_TRANSPORT_INEXPRESSIBLE_ROWS": (1, 2),
    }

    def check(led: tuple[Any, ...], prk: tuple[Any, ...], inx: tuple[Any, ...] = inexpressible) -> list[str]:
        lists = {"_UC002_UC003_LEDGER": led, "_UC002_UC003_VACUOUS_PARKS": prk, "_TRANSPORT_INEXPRESSIBLE_ROWS": inx}
        return _ledger_violations(rows, lists, ceilings)

    assert check(ledger, parks) == []
    extra = _CatchallXfail("T-B", "fails")
    assert any("grew to 2 entries" in v for v in check(ledger + (extra,), parks))
    widened = (_CatchallXfail("T-A", "fails", rows=("row_a", "row_b")),)
    assert any("matches 8 nodeids (ceiling 4)" in v for v in check(widened, parks))
    assert any("matched by both" in v for v in check(widened, parks))
    rowless = (_CatchallXfail("T-A", "vacuous"), parks[1])
    assert any("without naming its rows" in v for v in check(ledger, rowless))
    # The inexpressible rows are held to the same rules, and must leave a transport that grades.
    rowless_inexpressible = (_CatchallXfail("T-A", "no request", transports=("rest",)),)
    assert any("without naming its rows" in v for v in check(ledger, parks, rowless_inexpressible))
    everywhere = (_CatchallXfail("T-A", "no request", rows=("row_c",)),)
    assert any("leaves no transport that grades the row" in v for v in check(ledger, parks, everywhere))
    overlapping = (_CatchallXfail("T-A", "no request", rows=("row_a",), transports=("rest",)),)
    assert any("matched by both" in v for v in check(ledger, parks, overlapping))
    # A row name is matched whole: row_a does not also claim row_ab.
    assert not _CatchallXfail("T-A", "x", rows=("row_a",)).matches({"T-A"}, "t.py::f[mcp-row_ab-x]")
