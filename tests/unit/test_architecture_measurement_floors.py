"""Floors under the measurement counters that can silently measure nothing.

Plan Decision 5 (#1858): "Each measurement stage degrades to 'measured less'
without failing. Every counter gets a minimum that fails the run when
measurement disappears."

Two floors live here. Both guard a counter whose failure mode is SILENCE — the
thing being counted disappears and the count keeps reporting success.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit._storyboard_guard_env import ADCP_HOME, requires_pinned_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


# ── Floor 1: the BDD env-routing registry ───────────────────────────────────
#
# Deleting one row turns its scenarios dormant and every structural guard stays
# green, because the guards check the registry's SHAPE, not its MEMBERSHIP.
#
# Pinned as a SET, not a count: a count is satisfied by add-plus-drop, which is
# the same weakness this epic's suite comparisons avoid by comparing xpassed
# node ids by identity rather than by size.
#
# The set spans BOTH sources, deliberately. The literal `ENV_ROUTES` block holds
# 14 wired rows; `ENV_ROUTES +=` appends 5 more from `_UC_BUCKET_ROUTES` at
# import time. "How many wired rows are there" therefore has two answers (14 and
# 19), and a floor that does not say which it means is itself an ambiguous
# counter. This pins the runtime set — what actually routes scenarios.
#
# Discipline, matching EXPECTED_LEDGER in test_storyboard_ledger_state.py:
# graduating a placeholder to wired ADDS a tag here in the same change, as a
# reviewed edit. A tag disappearing without one is the regression this catches.
EXPECTED_WIRED_ROUTES: frozenset[str] = frozenset(
    {
        # bucket rows, appended from _UC_BUCKET_ROUTES
        "ADMIN",
        "COMPAT",
        "UC-005",
        "UC-010",
        "UC-019",
        "UC-030",
        "UC-GET-PRODUCTS",
        # literal ENV_ROUTES block
        "uc002-account",
        "uc002-ext",
        "uc002-idempotency",
        "uc002-manual-approval",
        "uc003-ext",
        "uc003-manual-approval",
        "uc003-storyboard-generic-client",
        "uc004-circuit-breaker",
        "uc004-create",
        "uc004-poll",
        "uc006-creative-sync",
        "uc019-post-create-poll",
        "uc011-list",
        "uc011-sync",
        "uc018-list",
    }
)


def test_every_wired_env_route_is_still_registered() -> None:
    """A wired route disappearing must redden, not silently dormant its scenarios."""
    from tests.bdd import conftest

    wired = {route.tag for route in conftest.ENV_ROUTES if route.xfail_reason is None}
    missing = sorted(EXPECTED_WIRED_ROUTES - wired)
    added = sorted(wired - EXPECTED_WIRED_ROUTES)

    assert not missing, (
        f"{len(missing)} wired env-route(s) vanished from ENV_ROUTES: {missing}. "
        "Their scenarios are now dormant and no other guard notices. Restore the row, or — if "
        "the removal is deliberate — drop the tag from EXPECTED_WIRED_ROUTES in the same change."
    )
    assert not added, (
        f"{len(added)} wired env-route(s) are registered but not pinned: {added}. "
        "Add them to EXPECTED_WIRED_ROUTES so a later deletion is caught (graduating a "
        "placeholder to wired belongs in the same change as its pin)."
    )


# ── Floor 2: the published check-index cannot shrink unnoticed ─────────────
#
# The gate-schema oracle declares this blind spot about itself: both of its
# assertions read the PUBLISHED records, and only ON-PATH and GATED storyboards
# are published (`INDEXED_STATUSES`). A DECLARING storyboard misrouted to
# OFF-PATH therefore leaves the index entirely, and a comparison over what is
# present cannot see an absence. Measured blast radius: misrouting the 21 gated
# storyboards that carry no ledger entry drops 516 of 544 GATED records — 38% of
# the 1351-record index — and the whole 110-module architecture suite stays
# green.
#
# Pinned as a SET, not a count, for the same reason the route floor above is:
# a count is satisfied by add-plus-drop. The set is what makes a misroute
# visible, because a misrouted storyboard leaves it.
EXPECTED_GATED_STORYBOARDS: frozenset[str] = frozenset(
    {
        "protocols/media-buy/scenarios/audience_buy_flow.yaml",
        "protocols/media-buy/scenarios/available_actions.yaml",
        "protocols/media-buy/scenarios/clicks_buy_flow.yaml",
        "protocols/media-buy/scenarios/completed_views_buy_flow.yaml",
        "protocols/media-buy/scenarios/dependency_impairment.yaml",
        "protocols/media-buy/scenarios/dependency_impairment_cardinality.yaml",
        "protocols/media-buy/scenarios/event_dedup_flow.yaml",
        "protocols/media-buy/scenarios/frequency_cap_enforcement.yaml",
        "protocols/media-buy/scenarios/inline_creatives_without_sync.yaml",
        "protocols/media-buy/scenarios/pending_creatives_to_start.yaml",
        "protocols/media-buy/scenarios/per_creative_conversion_attribution.yaml",
        "protocols/media-buy/scenarios/performance_buy_flow.yaml",
        "protocols/media-buy/scenarios/performance_buy_flow_roas.yaml",
        "protocols/media-buy/scenarios/product_signal_targeting.yaml",
        "protocols/media-buy/scenarios/reach_buy_flow.yaml",
        "protocols/media-buy/scenarios/refine_finalize_exclusivity.yaml",
        "protocols/media-buy/scenarios/vendor_metric_catalog_precondition.yaml",
        "protocols/media-buy/scenarios/vendor_metric_optimization_flow.yaml",
        "protocols/media-buy/state-machine.yaml",
        "universal/get-products-pagination-integrity.yaml",
        "universal/wholesale-feed-bulk-webhooks.yaml",
        "universal/wholesale-feed-product-webhooks.yaml",
        "universal/wholesale-feed-products.yaml",
        "universal/wholesale-feed-signal-webhooks.yaml",
        "universal/wholesale-feed-signals.yaml",
    }
)

#: Floor under the record count itself. A count, not a set — the records are the
#: thing being counted, and 1351 individual ids would be a second index rather
#: than a floor. It catches wholesale collapse; the SET above catches a misroute.
MINIMUM_INDEX_CHECKS = 1351


@requires_pinned_bundle
def test_the_published_check_index_does_not_silently_shrink() -> None:
    """A declaring storyboard leaving the index must redden, not go quiet."""
    from scripts.audit import storyboard_check_index

    index = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    gated = {r["storyboard"] for r in index["records"] if r["gate"] == "GATED"}

    missing = sorted(EXPECTED_GATED_STORYBOARDS - gated)
    added = sorted(gated - EXPECTED_GATED_STORYBOARDS)

    assert not missing, (
        f"{len(missing)} GATED storyboard(s) left the published index: {missing}. "
        "Either the gate classifier misrouted them to OFF-PATH — in which case their checks "
        "vanished from the index and the gate-schema oracle cannot see the absence — or the "
        "reclassification is deliberate and belongs in EXPECTED_GATED_STORYBOARDS in the same change."
    )
    assert not added, (
        f"{len(added)} GATED storyboard(s) are published but not pinned: {added}. "
        "Add them so a later disappearance is caught."
    )
    assert index["totals"]["checks"] >= MINIMUM_INDEX_CHECKS, (
        f"the check index published {index['totals']['checks']} records, below the "
        f"{MINIMUM_INDEX_CHECKS} floor. The index is the denominator every conformance "
        "number here is quoted against; it shrinking silently makes every one of them flattering."
    )


# ── Floor 2: the liveness artifact's collect-only protection ────────────────
#
# `scenario_liveness.pytest_sessionfinish` returns early on a collect-only
# session. Without it, `make quality` destroys the artifact on every run: it
# shells out to `pytest tests/bdd --collect-only`, whose empty `_RECORDS` would
# be written as `{"scenarios": []}`, and the join fails CLOSED on an empty file
# — so every check silently reports liveness 0.
#
# The step body records that two independent mutations removed this branch and
# every guard stayed green. This is the test that was missing.


def test_collect_only_session_does_not_write_the_liveness_artifact(tmp_path, monkeypatch) -> None:
    """A session that observed nothing must not overwrite a real artifact."""
    from tests.bdd import scenario_liveness

    artifact = tmp_path / "liveness.json"
    artifact.write_text('{"scenarios": [{"scenario_id": "real"}]}', encoding="utf-8")
    monkeypatch.setenv("BDD_LIVENESS_ARTIFACT", str(artifact))

    # NO `workeroutput` attribute: `_is_xdist_worker` is `hasattr(config,
    # "workeroutput")`, so supplying one sends this down the WORKER branch,
    # which returns before the collect-only check is ever reached. A first
    # version of this test did exactly that — it passed with the collect-only
    # branch deleted, i.e. it asserted nothing. The mutation caught it.
    session = SimpleNamespace(config=SimpleNamespace(option=SimpleNamespace(collectonly=True)))
    assert not scenario_liveness._is_xdist_worker(session.config), (
        "this session must take the CONTROLLER path or the test is vacuous"
    )
    scenario_liveness.pytest_sessionfinish(session)

    assert artifact.read_text(encoding="utf-8") == '{"scenarios": [{"scenario_id": "real"}]}', (
        "a collect-only session overwrote the liveness artifact — the branch in "
        "scenario_liveness.pytest_sessionfinish that returns early on collectonly is gone or "
        "no longer reached, and `make quality` will now destroy the artifact on every run"
    )


# ── Floor 3: a narrowed bdd run must not pass as a measurement ──────────────
#
# The collect-only return closes the ZERO case. NARROWING was the remaining
# hole: seeding the artifact with 900 records and running
# `pytest tests/bdd -k "storyboard and recancel"` rewrote it to ONE record, and
# the other 899 read as dormant — a claim about production, from a file that
# only recorded a narrower question having been asked.


def test_a_narrowed_run_is_rejected_rather_than_read_as_dormancy(tmp_path) -> None:
    """The artifact's own scope block must make a partial run visible."""
    from scripts.audit import scenario_liveness_join, storyboard_spec

    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps(
            {
                "run": {"collected": 1, "selection": "storyboard and recancel", "markers": ""},
                "scenarios": [{"scenario_id": "T-UC-003-x", "steps_bound": True, "ledgered": False}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(storyboard_spec.StoryboardAuditError) as excinfo:
        scenario_liveness_join.load_artifact(artifact)
    assert "storyboard and recancel" in str(excinfo.value)


def test_a_full_run_is_still_read_normally(tmp_path) -> None:
    """The floor must not reject the artifact it exists to protect."""
    from scripts.audit import scenario_liveness_join

    artifact = tmp_path / "liveness.json"
    artifact.write_text(
        json.dumps(
            {
                "run": {"collected": 900, "selection": "", "markers": ""},
                "scenarios": [{"scenario_id": "T-UC-003-x", "steps_bound": True, "ledgered": False}],
            }
        ),
        encoding="utf-8",
    )

    assert set(scenario_liveness_join.load_artifact(artifact)) == {"T-UC-003-x"}
