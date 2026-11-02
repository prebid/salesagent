"""Guard: UC-010 xfail reason TEXT must describe the actual measured failure, and the
mi0x v3.1.1 scenario cluster must not claim "graded" while dormant.

``tests/unit/test_architecture_bdd_no_stale_xfail_citations.py`` only checks TAG
MEMBERSHIP (is a scenario's tag still registered in one of the xfail structures) — it
does not validate that a *registered* tag's reason STRING actually names the failure
production currently produces, nor that a UC-010 tag claiming a graded "#1592
production gap" in its Gherkin comment has ever actually run against production
(``_uc010_wired_tags()``/``_UC010_WIRED_TAGS`` gate). This guard closes that gap:

    An xfail reason must describe the ACTUAL first failing assert measured on that
    transport, cite the specific GH issue that will make the scenario true (never a
    stale/umbrella one), and must be re-derived the moment the reason it was written
    stops being accurate. A scenario that never runs must not claim a graded result.

1. ``T-UC-010-main``'s ``_XFAIL_TAGS`` reason must not repeat the stale
   "supported_pricing_models, reporting_delivery_methods not emitted" claim: both
   derive correctly now, and the Then order in
   ``BR-UC-010-discover-seller-capabilities.feature`` fails on ``account.sandbox``
   before ever reaching those fields.

2. ``T-UC-010-targeting``'s reason must not claim "geo_postal_areas uses the deprecated
   boolean-alias shape" — ``_build_geo_postal_areas`` builds the native country-keyed
   map correctly.

3. The 4 mi0x-cluster v3.1.1 scenarios (``creative_multiplicity``,
   ``creative_agentic_flags``, ``governance_aware``, ``vendor_metric_optimization``) have
   no Given/When/Then step definitions yet, so they are correctly absent from
   ``_UC010_WIRED_TAGS`` and fast-xfail via the generic dormant fallback. Their Gherkin
   comments must say so honestly (DORMANT), not claim a graded "#1592 production gap"
   they never actually run against.

Where the obligation lives NOW
------------------------------
The property is unchanged; only the LOCATORS moved, and both moved the same way —
from source shape to live value:

* ``_xfail_tags_reasons()`` required ``_XFAIL_TAGS`` to be a module-level
  ``ast.Dict`` of constant-to-constant pairs. It now reads the dict object
  ``conftest.py:1651`` actually iterates, so a reason built any other way is graded
  rather than skipped.
* The wired-tag gate was scanned as a bare ``_UC010_WIRED_TAGS`` set literal. #1858
  moved it inside ``_uc010_wired_tags()`` as ``frozenset({...})`` — an ``ast.Call``
  — and every check here died on that assert. It is now obtained by CALLING the
  accessor.
* The mi0x Gherkin check sliced 2000 characters after ``text.index(f"@{tag}")``.
  A window is not a scenario: too short and it misses a comment at the foot of the
  block, too long and it reports the NEXT scenario's comment against this tag. It
  now uses the same Gherkin block parser the citation guard uses.

Absence is the failure mode all three shared: a drained locator reports "no
violations", which is what compliance looks like. :class:`TestGuardIsNotVacuous`
pins the subjects so the next such move fails loudly here instead.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from tests.unit._architecture_helpers import (
    assert_guard_subject_resolves,
    assert_scanned_paths_exist,
)
from tests.unit.test_architecture_bdd_no_stale_xfail_citations import _parse_scenarios, _uc_tags

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFTEST_PATH = _REPO_ROOT / "tests" / "bdd" / "conftest.py"
_FEATURE_PATH = _REPO_ROOT / "tests" / "bdd" / "features" / "BR-UC-010-discover-seller-capabilities.feature"

_CONFTEST_MODULE = "tests.bdd.conftest"
_XFAIL_TAGS_NAME = "_XFAIL_TAGS"
_WIRED_ACCESSOR = "_uc010_wired_tags"

_MI0X_TAGS: tuple[str, ...] = (
    "T-UC-010-v31-creative-multiplicity",
    "T-UC-010-v31-creative-agentic-flags",
    "T-UC-010-v31-governance-aware",
    "T-UC-010-v31-vendor-metric-optimization",
)


def _bdd_conftest() -> Any:
    """The BDD conftest as a module — the same dotted name pytest imports it under."""
    return importlib.import_module(_CONFTEST_MODULE)


def _xfail_tags_reasons() -> dict[str, str]:
    """Every ``{tag: reason}`` pair conftest's xfail hook actually iterates.

    Read live, not out of the source: ``conftest.py:1651`` walks this very dict,
    so this is the reason a scenario really xfails with. The previous locator
    required ``_XFAIL_TAGS`` to be a module-level ``ast.Dict`` literal and would
    have silently under-reported any entry built some other way.
    """
    return dict(getattr(_bdd_conftest(), _XFAIL_TAGS_NAME))


def _uc010_wired_tags() -> frozenset[str]:
    """The wired-tag allowlist, asked of production rather than pattern-matched.

    #1858 moved this set inside ``_uc010_wired_tags()``, where it is built as
    ``frozenset({...})``. Every guard that scanned conftest's source for a bare
    ``_UC010_WIRED_TAGS`` set literal died on that move; calling the accessor
    answers for whatever shape the set takes next.
    """
    return frozenset(getattr(_bdd_conftest(), _WIRED_ACCESSOR)())


def _scenario_block_for_tag(tag: str) -> list[str]:
    """The lines of the scenario carrying ``@tag``, parsed — never a fixed-size window.

    This used to slice 2000 characters after ``text.index(f"@{tag}")``. Two ways
    that lies: a window shorter than the scenario misses a comment at its foot,
    and a window longer than it bleeds into the NEXT scenario and reports that
    one's comment against this tag. Both are silent.
    """
    lines = _FEATURE_PATH.read_text(encoding="utf-8").splitlines()
    blocks = [lines[start:end] for tags, start, end in _parse_scenarios(lines) if tag in _uc_tags(tags)]
    assert blocks, f"@{tag} tags no scenario in {_FEATURE_PATH.name} — this guard needs updating."
    return [line for block in blocks for line in block]


class TestGuardIsNotVacuous:
    """Every check below is "this string is absent" or "this tag is absent".

    Absence is what a drained guard reports too: if the reasons map came back
    empty, or the feature file moved, every one of them would pass while grading
    nothing. That is exactly how the previous locator failed — it asserted a
    source SHAPE, and the shape moved. These tests pin the subjects.
    """

    @pytest.mark.arch_guard
    def test_guard_subjects_still_resolve(self) -> None:
        assert_guard_subject_resolves(
            _CONFTEST_MODULE,
            _XFAIL_TAGS_NAME,
            _WIRED_ACCESSOR,
            why="Nothing else checks that a REGISTERED xfail's reason text still describes the "
            "failure production currently produces, nor that a tag claiming a graded gap is wired.",
        )
        assert_scanned_paths_exist(
            [str(_CONFTEST_PATH), str(_FEATURE_PATH)],
            why="This guard reads both by path; a rename would drain it silently.",
        )

    @pytest.mark.arch_guard
    def test_the_reasons_map_has_entries_to_grade(self) -> None:
        """An empty map makes every reason-accuracy check below vacuous."""
        assert _xfail_tags_reasons(), (
            f"{_XFAIL_TAGS_NAME} is empty. If no scenario is xfail-routed any more, retire this "
            "guard deliberately rather than leaving it green over an empty map."
        )

    @pytest.mark.arch_guard
    def test_the_wired_set_has_entries_to_grade(self) -> None:
        """An empty wired set would make the mi0x dormancy checks pass by construction."""
        assert _uc010_wired_tags(), (
            f"{_WIRED_ACCESSOR}() is empty — 'tag not in wired' would hold for every tag, "
            "including ones that ARE wired."
        )

    @pytest.mark.parametrize("tag", _MI0X_TAGS)
    @pytest.mark.arch_guard
    def test_each_mi0x_tag_still_names_a_scenario(self, tag: str) -> None:
        """A tag no scenario carries makes its dormancy checks grade nothing."""
        assert _scenario_block_for_tag(tag)


class TestUC010MainReasonAccuracy:
    """The reporting-delivery xfail reason must name the CURRENTLY measured failing assert.

    #1721: T-UC-010-main was SPLIT. Its one undeliverable assert moved to
    @T-UC-010-main-reporting-delivery, which is what carries the xfail now; the rest
    of T-UC-010-main executes un-xfailed. This class follows the assert to its new
    tag -- a reason-accuracy meta-test that kept grading the old tag would grade an
    entry that no longer exists, and would pass vacuously if the dict lookup were
    ever made forgiving.
    """

    def test_main_itself_is_no_longer_xfailed(self) -> None:
        """The split's whole point: T-UC-010-main's other asserts must EXECUTE.

        Two separate causes kept this scenario from running, and both are fixed:
        its one spec-blocked assert moved to @T-UC-010-main-reporting-delivery
        (#1291), and its primary_channels failure turned out to be a malformed
        Given -- the feature quoted the channel list as ONE string, so the
        harness received a single bogus channel name and production fell back to
        [display]. Re-routing this tag to xfail would mask the account.*,
        supported_pricing_models, features, geo and portfolio asserts all over
        again, which is the condition #1721 existed to end.
        """
        assert "T-UC-010-main" not in _xfail_tags_reasons(), (
            "T-UC-010-main is xfail-routed again — its account.*, supported_pricing_models, "
            "features, geo and portfolio asserts are masked once more."
        )

    def test_reason_names_reporting_delivery_methods_as_the_live_gap(self) -> None:
        reason = _xfail_tags_reasons()["T-UC-010-main-reporting-delivery"]
        assert "reporting_delivery_methods" in reason, (
            "The split scenario's reason must name media_buy.reporting_delivery_methods "
            f"as the live gap. Got: {reason!r}"
        )

    def test_reason_names_the_rule_and_no_resolved_claim(self) -> None:
        """The gap is the pinned cross-field rule and the state this scenario's Given can reach.

        Two claims the reason must NOT make, both resolved. ``account.sandbox`` is long
        since emitted. And RFC 9421 webhook signing is IMPLEMENTED -- ``webhook_signing``
        is derived from real key material (``src.core.signing.posture.webhook_signing_posture``)
        and the emitting case is graded by ``@T-UC-010-v31-webhook-signing-required-when``
        -- so a reason citing #1291 as an unimplemented dependency describes a tree that
        no longer exists, and pins a scenario as unreachable for a reason that has gone.
        """
        reason = _xfail_tags_reasons()["T-UC-010-main-reporting-delivery"]
        assert "must_equal_when" in reason, (
            f"The reason must name the pinned cross-field rule that forbids the emission. Got: {reason!r}"
        )
        assert "#1291" not in reason, (
            "The reason cites #1291 as the blocker, but webhook_signing is derived from key material "
            f"and graded by @T-UC-010-v31-webhook-signing-required-when. Got: {reason!r}"
        )
        assert "account.sandbox" not in reason, (
            f"The reason still claims the resolved account.sandbox gap. Got: {reason!r}"
        )


class TestUC010TargetingReasonAccuracy:
    """T-UC-010-targeting's reason must not repeat a falsified boolean-alias claim."""

    def test_reason_does_not_claim_boolean_alias_shape(self) -> None:
        reason = _xfail_tags_reasons()["T-UC-010-targeting"]
        assert "boolean-alias" not in reason, (
            "T-UC-010-targeting's reason still claims the deprecated boolean-alias "
            "shape; _build_geo_postal_areas builds the native country-keyed map. "
            f"Got: {reason!r}"
        )


class TestMi0xScenariosHonestlyDormant:
    """The 4 v3.1.1 mi0x-cluster scenarios (creative_multiplicity,
    creative_agentic_flags, governance_aware, vendor_metric_optimization) have no
    Given/When/Then step definitions yet. Wiring them into _UC010_WIRED_TAGS without
    those steps would make them fast-xfail with a FALSE reason (claiming a graded
    production gap on a scenario that never runs) -- the same defect class this guard
    exists to catch. Until the step definitions are written, the correct state is:
    absent from _UC010_WIRED_TAGS (so they run through the honest dormant-fallback
    reason), and their Gherkin comment must say DORMANT, not XFAIL-EXPECTED."""

    @pytest.mark.parametrize("tag", _MI0X_TAGS)
    def test_tag_is_not_wired_without_step_definitions(self, tag: str) -> None:
        wired = _uc010_wired_tags()
        assert tag not in wired, (
            f"{tag} is in _UC010_WIRED_TAGS but has no Given/When/Then step "
            "definitions -- wiring without steps produces a false xfail reason."
        )

    @pytest.mark.parametrize("tag", _MI0X_TAGS)
    def test_feature_file_does_not_claim_a_graded_gap_for_the_mi0x_cluster(self, tag: str) -> None:
        block = _scenario_block_for_tag(tag)
        offenders = [line.strip() for line in block if "XFAIL-EXPECTED" in line]
        assert not offenders, (
            f"{tag}'s scenario claims XFAIL-EXPECTED (graded) but has no step "
            f"definitions and never runs -- comment must say DORMANT instead: {offenders}"
        )
