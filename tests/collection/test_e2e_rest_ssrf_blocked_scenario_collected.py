"""Regression: T-UC-004-webhook-ssrf-blocked must be collected on e2e_rest too (#1802).

``tests/bdd/conftest.py``'s ``_NO_E2E_REST_TAGS`` silently drops
``Transport.E2E_REST`` from this scenario's parametrize list inside
``pytest_generate_tests`` — no ``pytest.mark.xfail``, so neither detector in
``tests/unit/test_architecture_e2e_rest_escape_hatches.py`` can see the route
(detector 1 only walks ``pytest_collection_modifyitems`` xfail conditions;
detector 2 only walks ``tests/harness/`` ``E2EUnsupportedSetup`` sites). The
scenario is exercised on a2a/mcp/rest but never even attempted on e2e_rest —
a silent, untracked exemption class the PR's own stated posture (exemptions
fail loudly, never age into prose) exists to prevent.

Reproduces the bug behaviorally: real pytest collection, asserting the
e2e_rest-parametrized test id exists. An AST scan of ``_NO_E2E_REST_TAGS``
would only prove the set is non-empty, not that a scenario is actually missing
from collection — this test proves the actual collected-item behavior the bug
describes.

The collection it reads is the REAL RUN's, published by
``tests/_collection_manifest.py``, not a nested ``--collect-only`` subprocess of
its own (#2168). Same evidence, and stronger: the suite that ran is the one
graded, rather than a second collection that could drift from it.
"""

from __future__ import annotations

from tests._collection_manifest import BDD_TREE, load, manifest_dir

_UC004_MODULE = "tests/bdd/test_uc004_deliver_media_buy_metrics.py"
_SCENARIO_TEST_NAME = "test_blocked_outbound_webhook_url_skips_delivery_without_post"


def test_ssrf_blocked_scenario_has_an_e2e_rest_variant():
    """The scenario must be collected on all four transports, e2e_rest included.

    With BDD_E2E_ENABLED=true, every other UC-004 webhook scenario gets an
    ``[e2e_rest]`` id (even ones later xfailed via the LOCKED
    ``_UC004_E2E_WEBHOOK_INTERNAL_TAGS`` route). This scenario alone gets
    none — it isn't even attempted, so it can't be xfailed, ledgered, or
    declared E2EUnsupportedSetup. That invisibility is the bug.
    """
    # e2e_enabled=True is demanded, not inferred: the ids this test looks for
    # only EXIST in a collection with the e2e_rest transport on, so a record
    # without it must fail loudly rather than report the scenario missing.
    rows = load(manifest_dir(), target=BDD_TREE, e2e_enabled=True)
    node_ids = [row["nodeid"] for row in rows if row["nodeid"].startswith(f"{_UC004_MODULE}::")]
    scenario_ids = [n for n in node_ids if _SCENARIO_TEST_NAME in n]
    assert scenario_ids, f"scenario {_SCENARIO_TEST_NAME!r} not collected at all:\n{node_ids}"

    variants = {n.rsplit("[", 1)[-1].rstrip("]") for n in scenario_ids if "[" in n}
    assert "e2e_rest" in variants, (
        f"{_SCENARIO_TEST_NAME!r} has no e2e_rest-parametrized test id — "
        f"_NO_E2E_REST_TAGS in tests/bdd/conftest.py silently drops it from "
        f"pytest_generate_tests before collection, invisible to both escape-hatch "
        f"detectors in test_architecture_e2e_rest_escape_hatches.py. "
        f"Collected variants: {sorted(variants)}"
    )
