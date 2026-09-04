"""Grader: the two env-route resolvers must agree, per COLLECTED scenario.

The structural half of this lane — one owner, one routing call, the four routing
helpers absorbed — lives in ``test_architecture_liveness_contract_owner.py``.
This module grades the BEHAVIOR the lane exists to fix: the verdict
``tests/bdd/conftest.py::_harness_env`` dispatches on and the verdict
``scripts/audit/scenario_liveness_join`` publishes must be the SAME verdict for
the same scenario.

## Why this is parametrized over collected scenarios, not over ``ENV_ROUTES``

The registry has 7 rows and the two lookups already AGREE on every one of them,
so a table-parametrized test is green before AND after and could never detect
the disagreement. The disagreement lives entirely in what is NOT a row: six
marker-set-keyed prose branches in ``_harness_env``, invisible to
``scenario_liveness_join.registry_wired`` — and a marker SET is not expressible
as a "tag combination in the table" at all. So the input is the real collected
``tests/bdd`` suite: every scenario's tag + MARKER set, taken from a real
``pytest --collect-only`` run (the same shape as
``tests/integration/test_bdd_scenario_liveness_real_run.py``'s narrow slices),
grouped per feature module so a failure names the module it is in.

## The negative obligation (F1c.1)

Covering the positive case alone is what let the degenerate reading — "a UC
``_detect_uc`` recognizes but ``ENV_ROUTES`` has no row for is routed-by-branch,
therefore wired" — pass an earlier draft of this grader. Each of those six UCs
also carries an ``else: pytest.xfail("... not yet wired")`` catch-all, so the
degenerate reading reports those scenarios wired TOO, converting today's
false-DORMANT into a false-LIVE. ``conftest.py:3472-3474`` documents the
magnitude in the other direction: "Dropping this line is what flipped ~800
dormant scenarios from xfail to fail."

So one CATCH-ALL scenario per branch UC is pinned below, by exact tag, and must
resolve NOT-wired on both sides.

UC-004 is the documented exception, stated rather than dropped: its catch-all
(``conftest.py:3715``) is UNREACHABLE today — ``_detect_delivery_harness``
is total over the four handled harness types, returning "poll" as its fallback,
so no collected UC-004 scenario can reach it. The obligation is therefore
authored in its only true form for that branch (every collected UC-004 scenario
resolves WIRED on both sides), which fails just as loudly if the move loses a
scenario. See ``test_uc004_catch_all_is_unreachable_so_all_of_it_resolves_wired``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from tests._collection_manifest import BDD_TREE, load, manifest_dir
from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root
from tests.unit._liveness_contract_pins import (
    DERIVATION_ACCESSOR,
    FIX_HINT,
    OWNER,
    RECORD_MARKER_FIELD,
    ROUTE_RESOLVER,
    accessor_dotted_module,
)

pytestmark = pytest.mark.arch_guard

_BDD_DIR = Path("tests/bdd")
_OWNER_MODULE = ".".join(OWNER.with_suffix("").parts)

# One catch-all scenario per branch UC, by EXACT tag, each verified in-tree to
# fall through to its branch's `else: pytest.xfail(...)` today. UC-004 is
# handled separately — see the module docstring.
_CATCH_ALL_SCENARIOS = (
    ("UC-002", "T-UC-002-alt-asap", "conftest.py:3475"),
    ("UC-003", "T-UC-003-alt-budget", "conftest.py:3586"),
    ("UC-006", "T-UC-006-boundary-approval", "conftest.py:3617"),
    ("UC-011", "T-UC-011-v31-error-conflict-version", "conftest.py:3665"),
    ("UC-018", "T-UC-018-boundary-default-query", "conftest.py:3644-3647"),
)

_UC004_TAG_PREFIX = "T-UC-004-"

# The real BDD collection yields ~7.2k in-process items today (9945 rows
# collected with e2e_rest on, 7223 in-process at authoring time).
_MINIMUM_COLLECTED_ITEMS = 6000

# BDD modules whose scenarios carry NO ``T-`` identity tag. They are outside the
# join by construction — ``scenario_liveness._scenario_identifier`` returns None,
# so no artifact record is ever written for them and no verdict is ever
# published. Pinned as an exact set rather than skipped silently: a module
# sliding into this bucket is a scenario losing its identity, which is a
# regression, not a shrug.
_MODULES_WITHOUT_SCENARIO_IDENTITY = frozenset(
    {
        "tests/bdd/test_brand_shorthand_bdd.py",
        "tests/bdd/test_get_products_inventory_profile.py",
    }
)


@lru_cache(maxsize=1)
def _collected_items() -> tuple[dict[str, Any], ...]:
    """Every collected ``tests/bdd`` item with its marker set, from a real run.

    Reads the full-tree record the real BDD session published rather than
    spawning a nested ``pytest --collect-only``. The marker sets are the ones
    ``iter_markers()`` reports AFTER every ``pytest_collection_modifyitems``
    hook — the auto-applied entity markers from tests/conftest.py and the
    transport xfail markers from tests/bdd/conftest.py included — because the
    record is serialized at ``pytest_collection_finish``. That is the superset
    ``DERIVATION_ACCESSOR`` is pinned on, and grading against anything narrower
    would compare two equally-truncated sets and pass while proving nothing.

    e2e_rest rows are dropped: this grader reasons about the in-process marker
    sets, and the record may come from a session that had BDD_E2E_ENABLED on
    (docker-compose sets it in-network). Dropping them here is what the old
    nested collection achieved by popping the variable from its subprocess env.
    """
    rows = [row for row in load(manifest_dir(), target=BDD_TREE) if "[e2e_rest" not in row["nodeid"]]

    # The suite collects ~7.2k items today. A floor well under that still catches
    # a truncated collection, which would make every verdict below vacuous. It
    # binds because `target` scoped the read to the tests/bdd tree — rows from
    # tests/unit cannot pad it.
    assert len(rows) > _MINIMUM_COLLECTED_ITEMS, (
        f"the collection record produced only {len(rows)} in-process items "
        f"(expected > {_MINIMUM_COLLECTED_ITEMS}) — the grader would be reasoning "
        "about a truncated suite."
    )
    return tuple(rows)


def _scenario_id(markers: list[str]) -> str | None:
    """The scenario's ``T-*`` identity tag — the key both sides join on.

    Matches ``scenario_liveness._scenario_identifier``: a scenario carrying no
    ``T-`` tag has no identity in the artifact and cannot be joined at all, so
    it is out of scope here rather than silently counted as agreeing.
    """
    for tag in markers:
        if tag.startswith("T-"):
            return tag
    return None


@lru_cache(maxsize=1)
def _scenarios_by_module() -> dict[str, dict[str, frozenset[frozenset[str]]]]:
    """``module -> scenario id -> the distinct marker sets its items carry``."""
    grouped: dict[str, dict[str, set[frozenset[str]]]] = defaultdict(lambda: defaultdict(set))
    for row in _collected_items():
        scenario_id = _scenario_id(row["markers"])
        if scenario_id is None:
            continue
        grouped[row["nodeid"].split("::")[0]][scenario_id].add(frozenset(row["markers"]))
    return {module: {sid: frozenset(sets) for sid, sets in scenarios.items()} for module, scenarios in grouped.items()}


def _require(module: str, name: str) -> Any:
    """The pinned contract symbol, or a failure that names what is missing."""
    import importlib

    target = getattr(importlib.import_module(module), name, None)
    assert target is not None, f"{module}.{name} does not exist yet.\n{FIX_HINT}"
    return target


def _env_routes() -> dict[str, Any]:
    from tests.bdd.conftest import ENV_ROUTES

    return ENV_ROUTES


def _harness_side_wired(marker_names: frozenset[str]) -> bool:
    """The verdict ``_harness_env`` dispatches on: the ONE routing call.

    A row with ``xfail_reason`` set is a registered placeholder, not a real
    wire — the registry's own published semantic (EnvRoute's docstring,
    scenario_liveness_join.registry_wired:116).
    """
    resolve = _require(_OWNER_MODULE, ROUTE_RESOLVER)
    route = resolve(marker_names, _env_routes())
    return route is not None and route.xfail_reason is None


def _join_side_wired(artifact_path: Path, scenarios: dict[str, frozenset[str]]) -> dict[str, bool]:
    """The verdict the audit join publishes, driven through its real entry point.

    The marker set reaches the join the only way it can — on the artifact
    RECORD (4a): the join's inputs are the claiming-scenario id set and the
    artifact, so there is no other marker source.
    """
    from scripts.audit.scenario_liveness_join import build_index

    artifact_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "scenario_id": scenario_id,
                        "steps_bound": True,
                        "ledgered": False,
                        RECORD_MARKER_FIELD: sorted(markers),
                    }
                    for scenario_id, markers in sorted(scenarios.items())
                ]
            }
        ),
        encoding="utf-8",
    )
    index = build_index(set(scenarios), artifact_path=artifact_path, env_routes=_env_routes())
    return {scenario_id: index[scenario_id].registry_wired for scenario_id in scenarios}


def _one_marker_set(scenario_id: str, marker_sets: frozenset[frozenset[str]]) -> frozenset[str]:
    """The scenario's routing input, asserted to be routing-stable across its items.

    A scenario's items differ only in transport-parametrization bookkeeping
    (e.g. an added ``xfail`` marker); the route must not turn on that.
    """
    verdicts = {_harness_side_wired(markers) for markers in marker_sets}
    assert len(verdicts) == 1, (
        f"{scenario_id} routes differently across its own collected items: {verdicts}. "
        "Routing must not depend on transport/xfail bookkeeping markers."
    )
    return sorted(marker_sets, key=sorted)[0]


@pytest.mark.parametrize(
    "module",
    [str(path) for path in sorted(Path(repo_root() / _BDD_DIR).glob("test_*.py"))],
    ids=lambda path: Path(path).name,
)
def test_both_resolvers_agree_for_every_collected_scenario(module: str, tmp_path: Path) -> None:
    """The direct fix for the measured disagreement, scenario by scenario."""
    relative = str(Path(module).relative_to(repo_root()))
    scenarios = _scenarios_by_module().get(relative, {})
    if relative in _MODULES_WITHOUT_SCENARIO_IDENTITY:
        assert scenarios == {}, (
            f"{relative} is pinned as carrying no T- identity tag, but now contributes "
            f"{sorted(scenarios)} — remove it from _MODULES_WITHOUT_SCENARIO_IDENTITY"
        )
        return
    assert scenarios, (
        f"{relative} contributed no identified scenario to the collected tests/bdd suite — either its "
        "scenarios lost their T- identity tag, or it belongs in _MODULES_WITHOUT_SCENARIO_IDENTITY"
    )

    inputs = {sid: _one_marker_set(sid, sets) for sid, sets in scenarios.items()}
    join_verdicts = _join_side_wired(tmp_path / "liveness.json", inputs)
    disagreements = {
        (sid, _harness_side_wired(markers), join_verdicts[sid])
        for sid, markers in inputs.items()
        if _harness_side_wired(markers) != join_verdicts[sid]
    }
    assert_violations_match_allowlist(
        disagreements,
        set(),
        fix_hint=(
            "(scenario, _harness_env verdict, join verdict). Both sides must reach the same "
            f"{ROUTE_RESOLVER}() with the same marker set.\n{FIX_HINT}"
        ),
    )


@pytest.mark.parametrize(
    ("use_case", "scenario_id", "site"),
    _CATCH_ALL_SCENARIOS,
    ids=[f"{uc}-{site}" for uc, _, site in _CATCH_ALL_SCENARIOS],
)
def test_catch_all_scenario_resolves_not_wired(use_case: str, scenario_id: str, site: str, tmp_path: Path) -> None:
    """A scenario that falls through its UC's branch to ``pytest.xfail`` is NOT wired.

    Without this, "recognized UC with no registry row => routed-by-branch =>
    wired" passes every other obligation while turning ~800 dormant scenarios
    into false-LIVE claims.
    """
    marker_sets = {
        markers
        for scenarios in _scenarios_by_module().values()
        for sid, sets in scenarios.items()
        if sid == scenario_id
        for markers in sets
    }
    assert marker_sets, (
        f"{scenario_id} ({use_case} catch-all at {site}) is not in the collected suite — the pin is stale"
    )

    markers = _one_marker_set(scenario_id, frozenset(marker_sets))
    assert _harness_side_wired(markers) is False, (
        f"{scenario_id} falls through to the {use_case} catch-all at {site} — it must resolve NOT-wired"
    )
    assert _join_side_wired(tmp_path / "liveness.json", {scenario_id: markers})[scenario_id] is False, (
        f"the join reports {scenario_id} as registry-wired although {use_case}'s catch-all xfails it at {site}"
    )


def test_uc004_catch_all_is_unreachable_so_all_of_it_resolves_wired(tmp_path: Path) -> None:
    """UC-004's catch-all (conftest.py:3715) is dead code and must stay dead.

    ``_detect_delivery_harness`` is total — "create", "circuit-breaker" and the
    "poll" fallback cover every marker set — so no collected UC-004 scenario can
    reach ``pytest.xfail(f"UC-004 harness not yet wired for type: ...")``. The
    negative obligation is therefore authored as its only true form for this
    branch: every UC-004 scenario resolves WIRED, on both sides. It fails just
    as loudly if the move drops one.
    """
    inputs = {
        sid: _one_marker_set(sid, sets)
        for scenarios in _scenarios_by_module().values()
        for sid, sets in scenarios.items()
        if sid.startswith(_UC004_TAG_PREFIX)
    }
    assert len(inputs) > 100, f"expected the full UC-004 scenario set, collected {len(inputs)}"

    join_verdicts = _join_side_wired(tmp_path / "liveness.json", inputs)
    not_wired = {
        (sid, _harness_side_wired(markers), join_verdicts[sid])
        for sid, markers in inputs.items()
        if not (_harness_side_wired(markers) and join_verdicts[sid])
    }
    assert_violations_match_allowlist(
        not_wired,
        set(),
        fix_hint=f"(scenario, _harness_env verdict, join verdict) — every UC-004 scenario is wired today.\n{FIX_HINT}",
    )


def test_scenario_never_measured_this_run_resolves_not_wired(tmp_path: Path) -> None:
    """4c: a marker-keyed resolver has no input for an unmeasured scenario.

    ``build_index`` calls the resolver for ids whose record is ``None``. Unstated,
    the implementer keeps the tag-only lookup as a fallback and resurrects the
    divergent path as a branch. Record-is-None resolves NOT-WIRED — consistent,
    because ``graded_by_live_scenario`` already ANDs ``measured_this_run``.
    """
    from scripts.audit.scenario_liveness_join import build_index

    empty = tmp_path / "liveness.json"
    empty.write_text(json.dumps({"scenarios": []}), encoding="utf-8")
    index = build_index({"T-UC-005-boundary-agent-asset"}, artifact_path=empty, env_routes=_env_routes())
    record = index["T-UC-005-boundary-agent-asset"]

    assert record.measured_this_run is False
    assert record.registry_wired is False, (
        "an unmeasured scenario has no marker set to route on, so it cannot be reported registry-wired — "
        f"a tag-only fallback here is the divergent path returning as a branch.\n{FIX_HINT}"
    )


def test_pinned_derivation_accessor_reproduces_iter_markers_for_every_collected_item() -> None:
    """4b: ONE canonical derivation, and it is the superset both sides can produce.

    ``conftest`` derives ``{m.name for m in request.node.iter_markers()}`` (entity
    markers included); the plugin derives ``scenario.tags | feature.tags``, a
    STRICT SUBSET. One shared resolver fed by two different derivations
    reproduces this lane's disease one layer out, where the agreement test above
    cannot see it — same function, same argument supplied by the test.
    """
    rows = _collected_items()
    assert accessor_dotted_module() is not None, (
        f"{DERIVATION_ACCESSOR}()'s home is not pinned yet (binding input G2: the implement atom records the "
        f"chosen module in the task and pins it in tests/unit/_liveness_contract_pins.py).\n{FIX_HINT}"
    )
    mismatches = {(row["nodeid"], tuple(row["markers"])) for row in rows if row["derived"] != row["markers"]}
    assert_violations_match_allowlist(
        mismatches,
        set(),
        fix_hint=(
            f"{DERIVATION_ACCESSOR}(node) must reproduce the iter_markers derivation exactly, for every "
            f"collected item — it is the single definition site both call sites use.\n{FIX_HINT}"
        ),
    )
