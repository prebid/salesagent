"""Guard: every BR-UC-*.feature must be named by some BDD test module.

pytest-bdd collects a feature file ONLY through an explicit
``scenarios("features/BR-UC-....feature")`` call. A feature file that no module
names is not collected at all — so its scenarios are not run, not xfailed, not
skipped, and not in any ledger. It is the most invisible way to be ungraded:
the file reads as reviewed work, cites ``@source`` spec pointers, and grades
nothing.

That is not hypothetical. When this guard was written, 20 of 38 feature files
were unbound, hiding 1604 scenarios and 148 error codes absent from CODE_TABLE
— codes no raise site can emit, so those scenarios would FAIL if they ran.
They did not fail, because nothing ran them (#1947).

This guard only stops the population from GROWING. It deliberately does not
assert that a bound file's scenarios pass — a bound file with a large xfail
ledger is the honest state, and an honest ledger beats an invisible one.

The allowlist may only SHRINK. Binding a file means adding the ``scenarios()``
call to a module (new or existing) and removing its entry here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

BDD_DIR = Path(__file__).resolve().parent.parent / "bdd"
FEATURES_DIR = BDD_DIR / "features"

# FIXME(#1947): unbound feature files ("census unbound BDD feature files -- scenarios()
# binding gap"), also tracked as salesagent-yz8mo. Each entry is
# scenarios that never execute. This set may only shrink.
#
# READ THIS BEFORE REMOVING AN ENTRY: a ``scenarios()`` call ALONE buys nothing here,
# and leaves this guard green while the scenarios still grade nothing.
#
# Binding has a SECOND precondition this guard cannot see: an ``ENV_ROUTES`` row.
# ``tests/bdd/conftest.py``'s ``_harness_env`` resolves one per scenario and, when
# there is none, calls ``pytest.xfail(f"No harness wired for {uc} ...")`` at FIXTURE
# SETUP -- before a single step runs. That xfail has no baseline and no ledger: unlike
# the missing-step dormancy converter in the same file, which refuses a scenario absent
# from ``tests/bdd/dormant_scenarios.txt``, nothing caps or records it. So removing an
# entry below without adding the route converts "unbound, and counted HERE" into
# "bound, xfailed at setup, counted NOWHERE" -- the same invisibility with this guard
# satisfied, which is strictly worse than the entry.
#
# MEASURED (``storyboard_spec.resolve_env_route`` against the live ``ENV_ROUTES``):
# all 18 features below resolve to NO route, covering 1473 scenarios. The module
# docstring's "20 of 38 ... hiding 1604 scenarios" is this population before two of
# the twenty were bound; the file count is still 38.
# ``registry_wired`` in ``scripts/audit``'s liveness join reports this, and
# ``test_architecture_storyboard_measured_status.py`` explains why that False is a
# measurement rather than an absence -- but it is a REPORT, not a gate.
#
# The entries are also not one population. Graded against the pinned spec's
# ``docs/protocol/required-tasks.mdx``, which fixes each task to an agent ROLE,
# and against the 14 names in ``src.core.tools.registry.TOOLS``:
#
#   This seller's own served surface -- the only bind-worthy entries:
#     BR-UC-001  get_products (Required for a sales agent)          121 scenarios
#     BR-UC-027  list_tasks, complete_task, get_task_status          93
#
#   Tasks this seller does not serve (no row in ``TOOLS``), Optional or Conditional
#   for a sales agent where the spec names them at all:
#     BR-UC-015 sync_event_sources, log_event · BR-UC-016 sync_audiences
#     BR-UC-023 sync_catalogs · BR-UC-017 report_usage, get_account_financials
#     BR-UC-032 comply_test_controller -- named in no role table, and its pinned
#       request schema reads "Sandbox only -- sellers MUST NOT expose this tool in
#       production", so anything bound here must be a sandbox-only surface
#
#   ANOTHER AGENT ROLE'S contract. Not a sales-agent obligation in any edition of
#   the spec, so no binding of these can grade this seller:
#     BR-UC-020 build_creative, BR-UC-021 preview_creative, BR-UC-022
#       get_creative_delivery/get_creative_features -- Creative Protocol, and
#       OPTIONAL even for a creative agent. This seller is the CLIENT of a creative
#       agent (``src/core/creative_agent_registry.py``) and declares only
#       ``media_buy`` in ``DEFAULT_SUPPORTED_PROTOCOLS``, so it does not claim the
#       role either.
#     BR-UC-008 signal agent · BR-UC-012, BR-UC-024 content-standards agent
#     BR-UC-013, BR-UC-025 property-governance agent · BR-UC-028 collection
#     governance · BR-UC-030 campaign governance · BR-UC-014 brand agent (SI)
UNBOUND_ALLOWLIST: set[str] = {
    "BR-UC-001-discover-available-inventory.feature",
    "BR-UC-008-manage-audience-signals.feature",
    "BR-UC-012-manage-content-standards.feature",
    "BR-UC-013-manage-property-lists.feature",
    "BR-UC-014-sponsored-intelligence-session.feature",
    "BR-UC-015-track-conversions.feature",
    "BR-UC-016-sync-audiences.feature",
    "BR-UC-017-account-financials-usage.feature",
    "BR-UC-020-build-creative.feature",
    "BR-UC-021-preview-creative.feature",
    "BR-UC-022-creative-delivery-features.feature",
    "BR-UC-023-sync-product-catalogs.feature",
    "BR-UC-024-content-compliance.feature",
    "BR-UC-025-property-features-validation.feature",
    "BR-UC-027-manage-async-tasks.feature",
    "BR-UC-028-manage-collection-lists.feature",
    "BR-UC-030-manage-governance-binding.feature",
    "BR-UC-032-compliance-test-controller.feature",
}

_FEATURE_REF = re.compile(r"BR-UC-[0-9A-Za-z.\-]+\.feature")


def _bound_feature_names() -> set[str]:
    """Every feature filename referenced by any BDD test module."""
    names: set[str] = set()
    for module in BDD_DIR.glob("test_*.py"):
        names.update(_FEATURE_REF.findall(module.read_text(encoding="utf-8")))
    return names


class TestEveryFeatureFileIsBound:
    @pytest.mark.arch_guard
    def test_unbound_feature_files_match_allowlist(self) -> None:
        """No NEW unbound feature file, and no stale entry once one is bound.

        Uses the shared helper so both directions are one assertion: a new
        unbound file and a now-bound allowlist entry are distinct error modes
        with distinct fixes, and hand-rolling the set-diff is itself a guarded
        anti-pattern here (test_architecture_no_handrolled_allowlist_diff).
        """
        all_features = {p.name for p in FEATURES_DIR.glob("BR-UC-*.feature")}
        unbound = {(name,) for name in all_features - _bound_feature_names()}
        assert_violations_match_allowlist(
            unbound,
            {(name,) for name in UNBOUND_ALLOWLIST},
            fix_hint=(
                'Bind a feature file by adding scenarios("features/<name>") to a BDD test '
                "module. Do NOT add a new entry to UNBOUND_ALLOWLIST -- that set may only "
                "shrink. An unbound file's scenarios never execute: not run, not xfailed, "
                "not skipped, not in any ledger."
            ),
        )
