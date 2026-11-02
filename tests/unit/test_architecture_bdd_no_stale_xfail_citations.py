"""Guard: BDD ``.feature`` scenarios must not carry a stale "#1592" citation.

GH #1592 is the umbrella epic for the capabilities/accounts spec-production
gaps. Scenarios that cited it as their xfail reason, and have since
GRADUATED (their tag was removed from every xfail-registration structure in
``tests/bdd/conftest.py``), keep the "#1592" text in their Gherkin comment
because nobody updated the comment after the scenario turned green — the
comment is prose, not the xfail marker itself, so nothing forces it to stay
in sync.

This is a non-behavioral, structural-disease pattern (see
``salesagent-z2cw``'s Core Invariant: "an xfail's citation must point to the
specific GH issue that will make the scenario true, and must be re-cited the
moment the reason it was written stops being accurate — a stale or umbrella
citation is itself a defect in the ledger, same tier as a stale test.").

Scanning approach: for every ``Scenario:`` / ``Scenario Outline:`` block in
``tests/bdd/features/*.feature``, resolve its owning ``@T-UC-...`` tag(s) and
check membership against the set of tags CURRENTLY registered in
``tests/bdd/conftest.py``'s three xfail-registration structures
(``_XFAIL_TAGS`` keys, ``_SELECTIVE_XFAIL`` tags, ``_MCP_SELECTIVE_XFAIL``
tags). A scenario whose body mentions "1592" but whose tag(s) are absent from
ALL three structures has graduated — its "#1592" citation is stale.

UC-010 has a FOURTH gate, the wired-tag allowlist returned by
``tests.bdd.conftest._uc010_wired_tags()``: a ``T-UC-010-*`` tag absent from
it never reaches real harness wiring at all — it is routed to its own
dormancy row (``_uc010_dormancy_rows()`` over ``_UC010_DORMANT_TRACKING``,
registered in ``ENV_ROUTES``) and xfails fast on every run, regardless of
``_XFAIL_TAGS``/``_SELECTIVE_XFAIL`` membership. Such a tag is DORMANT (never
actually graded), not graduated, so its citation cannot be stale-by-graduation
— it has never been tested against production. This guard therefore treats a
dormant UC-010 tag as "active" too; see :class:`_ActiveTagSet`.

Where the obligation lives NOW
------------------------------
The property is unchanged; only the LOCATOR moved. This guard used to read all
four structures out of ``conftest.py``'s SOURCE with ``ast``, requiring
``_XFAIL_TAGS`` to be a module-level dict literal, ``_SELECTIVE_XFAIL`` /
``_MCP_SELECTIVE_XFAIL`` to be lists of tuple literals, and
``_UC010_WIRED_TAGS`` to be a bare set literal. #1858 replaced ``_harness_env``'s
``elif`` chain with the declarative ``EnvRoute``/``ENV_ROUTES`` registry and moved
the wired set inside ``_uc010_wired_tags()``, where it is built as
``frozenset({...})`` — an ``ast.Call``, not an ``ast.Set``. Every check here
died on that assert.

The fix is the one ``test_architecture_uc010_dormancy_citations.py`` took: stop
reading source text and read the LIVE values out of the imported conftest
module, which is the same object pytest's hooks consult at
``conftest.py:1218``/``:1609``/``:1651``. A guard that reaches production by
string keeps passing after a rename — it finds nothing, which is
indistinguishable from finding nothing wrong — so
:func:`test_guard_subjects_still_resolve` and
:func:`test_the_guard_has_subjects_to_grade` fail loudly instead.

Scope: ``.feature`` files only. Step-definition ``.py`` docstrings/comments
(``uc010_capabilities.py``, ``uc011_accounts.py``) use free-form prose with
no clean tag-to-comment structural mapping and are fixed by hand separately
(see salesagent-z2cw disposition table row 10).

GH: #1592 (the umbrella epic this guard cleans citations of)
"""

from __future__ import annotations

import glob
import importlib
from pathlib import Path
from typing import Any

import pytest

from tests.unit._architecture_helpers import (
    assert_guard_subject_resolves,
    assert_scanned_paths_exist,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFTEST_PATH = _REPO_ROOT / "tests" / "bdd" / "conftest.py"
_FEATURES_DIR = _REPO_ROOT / "tests" / "bdd" / "features"

_CONFTEST_MODULE = "tests.bdd.conftest"
_XFAIL_TAGS_NAME = "_XFAIL_TAGS"
_SELECTIVE_XFAIL_NAME = "_SELECTIVE_XFAIL"
_MCP_SELECTIVE_XFAIL_NAME = "_MCP_SELECTIVE_XFAIL"
_WIRED_ACCESSOR = "_uc010_wired_tags"

_CITATION_SUBSTRING = "1592"
_SCENARIO_PREFIXES = ("Scenario:", "Scenario Outline:")

# ── ALLOWLIST (ratchet: shrinks only, never grows) ─────────────────────────
# Sourced from salesagent-z2cw's "Codebase Disease Scan" disposition table
# (`bd show salesagent-z2cw`), rows 8/9/10/12:
#   - Row 8/9 (conftest.py bare-#1592 xfail entries + the matching
#     BR-UC-010 Gherkin comments for the SAME ~17 still-genuinely-open tags,
#     e.g. T-UC-010-main, -audience-caps, -features, -targeting, the
#     account-*/degradation-* per-row _SELECTIVE_XFAIL family, ...): every one
#     of those tags IS present in _XFAIL_TAGS / _SELECTIVE_XFAIL today, so
#     this guard already finds them ACTIVE and never flags their feature-file
#     comments. No allowlist entry is needed or added for them.
#   - Row 10 (uc010_capabilities.py / uc011_accounts.py step-definition
#     docstrings): out of scope for this guard entirely (feature files only —
#     see module docstring "Scope").
#   - Row 12 (BR-UC-017-account-financials-usage.feature:149-150,868):
#     needs an explicit entry. get_account_financials / report_usage have no
#     production surface and UC-017 has no binding/step module at all, so it
#     has ZERO wiring anywhere in conftest.py (verified: `grep -c T-UC-017
#     tests/bdd/conftest.py` -> 0) — the tag can never appear "ACTIVE" under
#     this guard's tag-membership check no matter how open the gap is. The
#     "#1592" text there is historical-provenance prose ("Re-homed from
#     #1592 to #1722 by ... decision"), not a live xfail citation — exactly
#     the disposition table's row-12 verdict.
_ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        # Shifted down by one on 2026-09-09: the DO-NOT-EDIT header line was removed from
        # all 28 feature files (the features are no longer regenerated), so every line below
        # it moved. The citations themselves are unchanged -- 149/150/868 became 148/149/867.
        # This allowlist is keyed by (file, LINE NUMBER), so any edit above a cited line
        # invalidates it silently; the guard's own staleness check is what caught it.
        ("BR-UC-017-account-financials-usage.feature", 148),
        ("BR-UC-017-account-financials-usage.feature", 149),
        ("BR-UC-017-account-financials-usage.feature", 867),
    }
)


# ── conftest.py: active xfail-tag extraction (LIVE values, never source text) ──


def _bdd_conftest() -> Any:
    """The BDD conftest as a module — the same dotted name pytest imports it under.

    Read the live objects, not the source: the hooks at ``conftest.py:1218``,
    ``:1609`` and ``:1651`` iterate exactly these values, so this guard grades
    what actually routes an xfail. Source-shape scanning graded the literal
    spelling instead, and died the moment #1858 moved one of them.
    """
    return importlib.import_module(_CONFTEST_MODULE)


def _uc010_wired_tags() -> frozenset[str]:
    """The UC-010 tags whose harness wiring has landed, per production itself.

    Any ``T-UC-010-*`` tag absent from this set is routed to a dormancy row and
    fast-xfails independently of ``_XFAIL_TAGS``/``_SELECTIVE_XFAIL`` — so it can
    never be "stale by graduation" and must count as active here.
    """
    return frozenset(getattr(_bdd_conftest(), _WIRED_ACCESSOR)())


def _registered_xfail_tags() -> set[str]:
    """Union of ``_XFAIL_TAGS`` keys and the tag element of every ``*_SELECTIVE_XFAIL`` row."""
    conftest = _bdd_conftest()
    tags = set(getattr(conftest, _XFAIL_TAGS_NAME))
    for name in (_SELECTIVE_XFAIL_NAME, _MCP_SELECTIVE_XFAIL_NAME):
        tags |= {entry[0] for entry in getattr(conftest, name)}
    return tags


class _ActiveTagSet:
    """`tag in this` is True for explicitly-registered tags AND dormant UC-010 tags."""

    def __init__(self, registered: set[str], uc010_wired: frozenset[str]) -> None:
        self._registered = registered
        self._uc010_wired = uc010_wired

    def __contains__(self, tag: object) -> bool:
        if tag in self._registered:
            return True
        return isinstance(tag, str) and tag.startswith("T-UC-010-") and tag not in self._uc010_wired


def _active_xfail_tags() -> _ActiveTagSet:
    """Every tag treated as "active" (not stale-by-graduation) for this guard.

    Membership is True for:
    - every string key in ``_XFAIL_TAGS``
    - the tag element of every ``_SELECTIVE_XFAIL`` / ``_MCP_SELECTIVE_XFAIL`` row
    - every ``T-UC-010-*`` tag NOT wired into ``_uc010_wired_tags()`` (dormant)
    """
    return _ActiveTagSet(_registered_xfail_tags(), _uc010_wired_tags())


# ── feature files: scenario + tag + body parsing ───────────────────────────


def _parse_scenarios(lines: list[str]) -> list[tuple[list[str], int, int]]:
    """Split a feature file's lines into (tags, body_start_idx, body_end_idx) blocks.

    ``body_start_idx`` is the 0-based index of the ``Scenario:``/``Scenario
    Outline:`` line itself; ``body_end_idx`` is the exclusive end (the index
    of the next tag line or Scenario line, or EOF). Tags are collected from
    the run of ``@``-prefixed lines immediately (contiguously) preceding the
    Scenario line — any intervening non-tag line (Feature-level tags,
    Background:, blank lines, prose) resets the pending set, so Feature-level
    tag lines (e.g. ``@schema-v3.1`` before ``Feature:``) never leak into the
    first scenario's tag set.
    """
    scenarios: list[tuple[list[str], int, int]] = []
    pending_tags: list[str] = []
    current: tuple[list[str], int] | None = None  # (tags, start_idx)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("@"):
            pending_tags.extend(stripped.split())
            continue
        if stripped.startswith(_SCENARIO_PREFIXES):
            if current is not None:
                tags, start = current
                scenarios.append((tags, start, idx))
            current = (pending_tags, idx)
            pending_tags = []
            continue
        pending_tags = []

    if current is not None:
        tags, start = current
        scenarios.append((tags, start, len(lines)))

    return scenarios


def _uc_tags(tags: list[str]) -> list[str]:
    """``@T-UC-...`` tags with the leading ``@`` stripped."""
    return [tag.lstrip("@") for tag in tags if tag.startswith("@T-UC-")]


def _citation_sites() -> list[tuple[str, int, tuple[str, ...]]]:
    """Every ``(feature_file_name, lineno, owning_tags)`` whose scenario body cites #1592.

    Activity-blind on purpose: both the violation check and the allowlist
    staleness check need the same scan, and running it once here keeps the two
    from drifting apart.
    """
    sites: list[tuple[str, int, tuple[str, ...]]] = []
    for path_str in sorted(glob.glob(str(_FEATURES_DIR / "*.feature"))):
        path = Path(path_str)
        lines = path.read_text(encoding="utf-8").splitlines()
        for tags, start, end in _parse_scenarios(lines):
            uc_tags = tuple(_uc_tags(tags))
            for offset, text in enumerate(lines[start:end]):
                if _CITATION_SUBSTRING in text:
                    sites.append((path.name, start + offset + 1, uc_tags))
    return sites


def _unregistered_citation_sites() -> list[tuple[str, int, tuple[str, ...]]]:
    """Citation sites whose owning scenario is no longer registered under ANY xfail structure.

    A scenario is "graduated" (stale citation) when NONE of its ``@T-UC-...``
    tags are present in ``_active_xfail_tags()`` — i.e. the scenario is no
    longer registered under any xfail-registration structure, so whatever
    "#1592" text remains in its Gherkin comments describes a gap that (per
    the mechanical tag-membership rule) no longer blocks it.
    """
    active = _active_xfail_tags()
    return [site for site in _citation_sites() if not any(tag in active for tag in site[2])]


def _find_stale_citations() -> list[tuple[str, int, tuple[str, ...]]]:
    """Graduated-scenario citation sites, minus the allowlisted ones."""
    return [site for site in _unregistered_citation_sites() if (site[0], site[1]) not in _ALLOWLIST]


# ── Tests ───────────────────────────────────────────────────────────────


class TestNoStaleXfailCitations:
    """Every '#1592' citation in a BDD .feature file must belong to a still-active xfail tag."""

    @pytest.mark.arch_guard
    def test_no_stale_1592_citations_in_feature_files(self) -> None:
        """A scenario that graduated (tag absent from every xfail structure) must not
        still carry a "#1592" citation in its Gherkin comments."""
        violations = _find_stale_citations()

        if violations:
            lines = [
                "Stale '#1592' citation(s) found: the scenario's @T-UC-... tag(s) are "
                "absent from _XFAIL_TAGS / _SELECTIVE_XFAIL / _MCP_SELECTIVE_XFAIL in "
                "tests/bdd/conftest.py (the scenario graduated), but its Gherkin comment "
                "still cites #1592.",
                "",
            ]
            for rel_name, lineno, tags in violations:
                tag_str = ", ".join(tags) if tags else "(no @T-UC-... tag found)"
                lines.append(f"  tests/bdd/features/{rel_name}:{lineno}: tag(s) = {tag_str}")
            lines.extend(
                [
                    "",
                    "Fix: replace the stale '#1592 ...' comment with the correct disposition "
                    "(a 'Graduated (...)' note, or a re-cite to the narrower GH issue that now "
                    "covers the gap). See salesagent-z2cw for the per-instance disposition.",
                    "This allowlist is DEBT, not permission — see _ALLOWLIST docstring above.",
                ]
            )
            raise AssertionError("\n".join(lines))

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self) -> None:
        """Every allowlisted (file, line) must still be a genuine, still-unregistered citation.

        Catches stale allowlist entries: if a UC-017-style tag ever gets wired
        into conftest.py's xfail structures, or the comment is fixed by hand,
        the allowlist entry becomes dead weight and must be removed.
        """
        live_hits = {(rel_name, lineno) for rel_name, lineno, _tags in _unregistered_citation_sites()}
        stale_allowlist_entries = _ALLOWLIST - live_hits
        assert not stale_allowlist_entries, (
            "Stale _ALLOWLIST entries (no longer a real citation, or the tag is now "
            f"registered): {sorted(stale_allowlist_entries)}. Remove them from _ALLOWLIST."
        )


class TestGuardIsNotVacuous:
    """A guard that finds nothing to scan passes silently, and silence reads as health.

    Every check in this file is a "no violations" assertion over a scanned set;
    each one of them goes green the instant its subject stops resolving. These
    tests pin the subjects themselves, so a rename or a deletion fails loudly
    here instead of draining the guard.
    """

    @pytest.mark.arch_guard
    def test_guard_subjects_still_resolve(self) -> None:
        """Every production name this guard reaches for must still exist."""
        assert_guard_subject_resolves(
            _CONFTEST_MODULE,
            _XFAIL_TAGS_NAME,
            _SELECTIVE_XFAIL_NAME,
            _MCP_SELECTIVE_XFAIL_NAME,
            _WIRED_ACCESSOR,
            why="Stale '#1592' citations would go unguarded: with no registered-tag set to "
            "compare against, every scenario reads as still-active and nothing is ever flagged.",
        )
        assert_scanned_paths_exist(
            [str(_CONFTEST_PATH), str(_FEATURES_DIR)],
            why="This guard reads both by path; a rename would drain it silently.",
        )

    @pytest.mark.arch_guard
    def test_the_registered_tag_structures_are_not_empty(self) -> None:
        """An empty registry makes EVERY cited scenario read as graduated (or, for UC-010,
        as dormant) — the comparison would be against nothing."""
        conftest = _bdd_conftest()
        assert _registered_xfail_tags(), (
            f"{_XFAIL_TAGS_NAME} / {_SELECTIVE_XFAIL_NAME} / {_MCP_SELECTIVE_XFAIL_NAME} are all "
            "empty in tests/bdd/conftest.py. If every BDD scenario really has graduated, retire "
            "this guard deliberately rather than leaving it green over an empty set."
        )
        # Per-structure, because a union hides one branch going empty: a broken read of
        # _SELECTIVE_XFAIL would still leave the union non-empty via _XFAIL_TAGS, and
        # every selectively-xfailed scenario would silently read as graduated.
        # _MCP_SELECTIVE_XFAIL is deliberately excluded: conftest.py:1031 records that
        # its entries were folded into _UC005_PARTIAL_TAGS/_XFAIL_TAGS, so it is
        # legitimately empty today and must not be forced non-empty.
        for name in (_XFAIL_TAGS_NAME, _SELECTIVE_XFAIL_NAME):
            assert getattr(conftest, name), f"{name} is empty — its scenarios would all read as graduated."
        assert _uc010_wired_tags(), (
            f"{_WIRED_ACCESSOR}() is empty — every T-UC-010-* tag would read as dormant, so no "
            "UC-010 citation could ever be flagged stale."
        )

    @pytest.mark.arch_guard
    def test_the_guard_has_subjects_to_grade(self) -> None:
        """At least one live '#1592' citation must exist for the scan to be about anything."""
        sites = _citation_sites()
        assert sites, (
            "No '#1592' citation remains in any tests/bdd/features/*.feature scenario body. "
            "Every check in this file now grades an empty set. If the umbrella-epic citations "
            "are genuinely all cleaned up, delete this guard and its _ALLOWLIST together."
        )


# ── Meta-tests (the guard catches the disease, and tolerates the corrected form) ──


class TestGuardMechanics:
    """Verify the tag-resolution and scenario-parsing logic in isolation."""

    def test_every_registered_tag_is_active(self) -> None:
        """Derived, not pinned: every tag in any live xfail structure reads as active.

        This replaced three assertions that each named one real tag
        (``T-UC-010-v31-webhook-signing-bounds``, ``-v31-creative-approval-mode``,
        ``-ext-a``). Those were themselves stale locators in waiting — the moment
        such a tag legitimately graduates, the meta-test reddens for a reason that
        has nothing to do with the property. Quantifying over the live structures
        is strictly stronger and cannot go stale.
        """
        registered = _registered_xfail_tags()
        active = _active_xfail_tags()
        assert registered, "no registered xfail tags — see TestGuardIsNotVacuous"
        assert not [tag for tag in sorted(registered) if tag not in active]

    def test_a_wired_uc010_tag_with_no_registration_is_not_active(self) -> None:
        """The graduated case, derived: wired (so really graded) + registered nowhere.

        ``T-UC-010-ext-a`` is one member of this set — it was removed from
        ``_XFAIL_TAGS`` on graduation (salesagent-rldj) and must not resurface.
        """
        graduated = sorted(_uc010_wired_tags() - _registered_xfail_tags())
        assert graduated, (
            "No UC-010 tag is both wired and unregistered, so the graduated branch of "
            "_ActiveTagSet is graded by nothing here."
        )
        active = _active_xfail_tags()
        assert not [tag for tag in graduated if tag in active]

    def test_active_tag_membership_rule_covers_all_four_quadrants(self) -> None:
        """The membership rule graded on synthetic input, so every branch is exercised.

        The dormant-UC-010 branch in particular has no live counter-example to point
        at: production is free to have zero dormant tags on any given day, and a
        rule that only ever sees one quadrant is a rule nothing checks.
        """
        active = _ActiveTagSet(
            registered={"T-UC-010-registered", "T-UC-099-registered"},
            uc010_wired=frozenset({"T-UC-010-registered", "T-UC-010-graduated"}),
        )
        assert "T-UC-010-registered" in active, "a registered tag is active"
        assert "T-UC-099-registered" in active, "registration is not UC-010-specific"
        assert "T-UC-010-dormant" in active, "an unwired UC-010 tag is dormant, never graduated"
        assert "T-UC-010-graduated" not in active, "wired + unregistered = graduated"
        assert "T-UC-099-graduated" not in active, "the dormancy exemption is UC-010 only"

    def test_parse_scenarios_associates_tag_line_immediately_above(self) -> None:
        lines = [
            "Feature: fake",
            "",
            "  @T-UC-999-fake @boundary",
            "  Scenario: fake scenario",
            "    Given a thing",
            "    # cites #1592 here",
            "",
            "  @T-UC-998-other",
            "  Scenario: another",
            "    Given another thing",
        ]
        scenarios = _parse_scenarios(lines)
        assert len(scenarios) == 2
        first_tags, first_start, first_end = scenarios[0]
        assert _uc_tags(first_tags) == ["T-UC-999-fake"]
        assert lines[first_start].strip() == "Scenario: fake scenario"
        assert any("1592" in lines[i] for i in range(first_start, first_end))

    def test_parse_scenarios_does_not_leak_feature_level_tags(self) -> None:
        """A @tag line before 'Feature:' must not attach to the first scenario."""
        lines = [
            "@schema-v3.1",
            "Feature: fake",
            "",
            "  @T-UC-999-fake",
            "  Scenario: fake scenario",
            "    Given a thing",
        ]
        scenarios = _parse_scenarios(lines)
        assert len(scenarios) == 1
        tags, _start, _end = scenarios[0]
        assert "@schema-v3.1" not in tags
        assert _uc_tags(tags) == ["T-UC-999-fake"]

    def test_find_stale_citations_flags_graduated_scenario_with_1592_comment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive: a scenario whose tag is absent from every xfail structure but
        whose body still cites #1592 is flagged.

        Uses a synthetic feature file in a temp dir (not live repo content) — the
        live disease instances this guard originally caught (e.g. T-UC-010-ext-a)
        get fixed as part of the same change that adds this guard, so pinning the
        positive case to a specific live line would make this test flip to failing
        red the moment the real staleness it exists to catch is remediated.
        """
        feature = tmp_path / "BR-UC-FAKE.feature"
        feature.write_text(
            "Feature: fake\n"
            "\n"
            "  @T-UC-999-graduated-but-uncommented\n"
            "  Scenario: fake graduated scenario\n"
            "    Given a thing\n"
            "    # XFAIL-EXPECTED: production gap — #1592 (stale, tag was removed on graduation)\n"
        )
        monkeypatch.setattr("tests.unit.test_architecture_bdd_no_stale_xfail_citations._FEATURES_DIR", tmp_path)
        violations = _find_stale_citations()
        flagged_tags = {tag for _rel, _lineno, tags in violations for tag in tags}
        assert "T-UC-999-graduated-but-uncommented" in flagged_tags, (
            "A scenario whose tag is absent from every xfail structure but whose body still "
            "cites #1592 must be flagged as stale."
        )

    def test_find_stale_citations_does_not_flag_active_tag(self) -> None:
        """Negative: a scenario whose tag IS still registered is not flagged, even
        though its body cites #1592 (the citation is accurate, not stale)."""
        violations = _find_stale_citations()
        flagged_tags = {tag for _rel, _lineno, tags in violations for tag in tags}
        assert "T-UC-010-v31-webhook-signing-bounds" not in flagged_tags, (
            "T-UC-010-v31-webhook-signing-bounds is still a live _XFAIL_TAGS entry citing "
            "#1592 for a genuinely open gap — must not be flagged."
        )
