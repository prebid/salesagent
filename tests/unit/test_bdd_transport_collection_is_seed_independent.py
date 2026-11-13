"""Regression: which transports grade a BDD scenario must not depend on the run.

salesagent-1iidr. Measured across two full runs of the SAME tree
(test-results/innet_080926_0943 and innet_080926_1145, bdd_inprocess.json,
568 UC-010 nodeids each): a2a was collected 200 times in both, but mcp/rest
split 182/175 and then 179/178, and six scenarios changed which pair they ran
on -- ``test_brandblock__brand_protocol_capabilities_experimental_in_v31``
went [a2a, mcp] -> [a2a, rest], ``test_capabilities_response_includes_all_``
``targeting_dimensions`` went the other way. Reproduced here at seed level:
seeds 1/2/3 over the UC-010 module gave mcp/rest 183/166, 171/178, 174/175.

The cause was ``pytest_collection_modifyitems``'s strict-xfail representative
keeping the FIRST mcp/rest sibling it walked, over an ``items`` order that
pytest-randomly reshuffles every run (the ``bdd-inprocess-s*`` tox envs do not
pass ``-p no:randomly``). The transport it skipped was ungraded -- and
invisibly so: it presents as ~19 removed / ~19 added nodeids, exactly the
benign transport-parameter noise ``scripts/audit/compare_runs.py`` documents,
so every nodeid-set comparison reported CLEAN.

Why this test collects for real instead of asserting on the constant: the
counts are not the contract, stability is. Any future collection-time
condition that reads run-varying state -- a clock, a set iteration order, a
worker id -- reintroduces the same defect without touching the code this
one lived in, and this test still fails.
"""

from __future__ import annotations

import re
from collections import defaultdict

from tests.unit._architecture_helpers import collect_bdd_node_ids_with_e2e_enabled

#: The reported module. UC-010 is the largest transport-parametrized BDD module
#: (599 items) and is where the flap was measured, so it is the cheapest place
#: to observe a per-run difference -- but nothing here is UC-010-specific.
_UC010_MODULE = "tests/bdd/test_uc010_discover_seller_capabilities.py"

#: ``test_thing[mcp-example row]`` -> ("test_thing[", "mcp", "-example row]").
_TRANSPORT_PARAM = re.compile(r"^(?P<head>.*?\[)(?P<transport>a2a|mcp|rest|e2e_rest)(?P<tail>[-\]].*)$")


def _transport_sets(node_ids: list[str]) -> dict[str, set[str]]:
    """``{scenario with its transport token removed: {transports it collected}}``."""
    sets: dict[str, set[str]] = defaultdict(set)
    for node_id in node_ids:
        match = _TRANSPORT_PARAM.match(node_id)
        if match:
            sets[f"{match.group('head')}{match.group('tail')}"].add(match.group("transport"))
    return dict(sets)


def test_the_transports_a_scenario_collects_do_not_change_with_the_seed():
    """Two collections, two pytest-randomly seeds, identical per-scenario sets."""
    first = _transport_sets(collect_bdd_node_ids_with_e2e_enabled(_UC010_MODULE, randomly_seed=1))
    second = _transport_sets(collect_bdd_node_ids_with_e2e_enabled(_UC010_MODULE, randomly_seed=2))

    assert first, f"no transport-parametrized items collected from {_UC010_MODULE}"

    flapped = {
        scenario: (sorted(first[scenario]), sorted(second.get(scenario, set())))
        for scenario in first
        if first[scenario] != second.get(scenario, set())
    }
    missing = sorted(set(second) - set(first))

    detail = "\n  ".join(f"{scenario}: {before} -> {after}" for scenario, (before, after) in sorted(flapped.items()))
    assert not flapped and not missing, (
        f"{len(flapped)} scenario(s) collected a different transport set under a different "
        f"pytest-randomly seed, and {len(missing)} appeared only in the second collection. "
        f"The transport a scenario skips is then chosen per run, so a defect there survives "
        f"indefinitely while every nodeid-set diff reads as benign transport-parameter noise:"
        f"\n  {detail}\n  only in second: {missing}"
    )
