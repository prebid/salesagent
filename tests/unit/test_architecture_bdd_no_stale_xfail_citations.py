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

UC-010 has a FOURTH gate, ``_UC010_WIRED_TAGS`` (the per-tag harness-wiring
allowlist inside the ``uc == "UC-010"`` branch of the DB-scope fixture): a
``T-UC-010-*`` tag absent from ``_UC010_WIRED_TAGS`` never reaches real
harness wiring at all — it fast-xfails on every run via the generic
``pytest.xfail("UC-010 wiring batch 2/3 pending (#1592, salesagent-4sn7)")``
fallback, regardless of ``_XFAIL_TAGS``/``_SELECTIVE_XFAIL`` membership. Such
a tag is DORMANT (never actually graded), not graduated — verified live via
``saci test bdd test_uc010_discover_seller_capabilities.py -k "multiplicity or
agentic or governance or vendor_metric" -rxX``, which shows exactly this
fallback reason for all four v3.1.1 rollup scenarios. A UC-010 tag outside
``_UC010_WIRED_TAGS`` is therefore treated as "active" too (its citation
cannot be stale-by-graduation because it has never been tested against
production) — see ``uc010_wired_tags()``.

Scope: ``.feature`` files only. Step-definition ``.py`` docstrings/comments
(``uc010_capabilities.py``, ``uc011_accounts.py``) use free-form prose with
no clean tag-to-comment structural mapping and are fixed by hand separately
(see salesagent-z2cw disposition table row 10).

GH: #1592 (the umbrella epic this guard cleans citations of)
"""

from __future__ import annotations

import ast
import glob
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import BDD_CONFTEST_PATH, string_constant, uc010_wired_tags

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FEATURES_DIR = _REPO_ROOT / "tests" / "bdd" / "features"

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
        ("BR-UC-017-account-financials-usage.feature", 149),
        ("BR-UC-017-account-financials-usage.feature", 150),
        ("BR-UC-017-account-financials-usage.feature", 868),
    }
)


# ── conftest.py: active xfail-tag extraction ───────────────────────────────


def _module_level_assign_value(tree: ast.Module, name: str) -> ast.expr:
    """Return the RHS expression of a module-level ``name = ...`` / ``name: T = ...``."""
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    assert node.value is not None, f"{name} has no value"
                    return node.value
    raise AssertionError(f"{name} not found at module level in {BDD_CONFTEST_PATH}")


def _dict_key_tags(tree: ast.Module, name: str) -> set[str]:
    """Every string key of a module-level ``name: dict[str, str] = {...}``."""
    value = _module_level_assign_value(tree, name)
    assert isinstance(value, ast.Dict), f"{name} is not a dict literal"
    tags: set[str] = set()
    for key in value.keys:
        if key is None:  # dict unpacking (**other) — not used here, but don't crash
            continue
        tag = string_constant(key)
        if tag is not None:
            tags.add(tag)
    return tags


def _tuple_list_first_element_tags(tree: ast.Module, name: str) -> set[str]:
    """First element of every tuple in a module-level ``name: list[tuple[...]] = [...]``."""
    value = _module_level_assign_value(tree, name)
    assert isinstance(value, ast.List), f"{name} is not a list literal"
    tags: set[str] = set()
    for element in value.elts:
        assert isinstance(element, ast.Tuple), f"{name} entry is not a tuple: {ast.dump(element)}"
        tag = string_constant(element.elts[0])
        if tag is not None:
            tags.add(tag)
    return tags


def _active_xfail_tags_raw() -> set[str]:
    """Union of ``_XFAIL_TAGS`` keys, ``_SELECTIVE_XFAIL`` tags, ``_MCP_SELECTIVE_XFAIL`` tags."""
    tree = ast.parse(BDD_CONFTEST_PATH.read_text(encoding="utf-8"))
    tags = _dict_key_tags(tree, "_XFAIL_TAGS")
    tags |= _tuple_list_first_element_tags(tree, "_SELECTIVE_XFAIL")
    tags |= _tuple_list_first_element_tags(tree, "_MCP_SELECTIVE_XFAIL")
    return tags


def _active_xfail_tags() -> set[str]:
    """Every tag treated as "active" (not stale-by-graduation) for this guard.

    Union of:
    - every string key in ``_XFAIL_TAGS``
    - the first tuple element of every entry in ``_SELECTIVE_XFAIL``
    - the first tuple element of every entry in ``_MCP_SELECTIVE_XFAIL`` (if any)
    - every ``T-UC-010-*`` tag NOT wired into ``_UC010_WIRED_TAGS`` (dormant —
      see ``uc010_wired_tags``)
    """
    tags = _active_xfail_tags_raw()
    wired = uc010_wired_tags()

    def is_active(tag: str) -> bool:
        if tag in tags:
            return True
        if tag.startswith("T-UC-010-") and tag not in wired:
            return True  # dormant fallback — never stale-by-graduation
        return False

    # Materialize as a set-like membership check via a small wrapper class so
    # callers keep using `tag in active` without change.
    return _ActiveTagSet(tags, wired)


class _ActiveTagSet:
    """`tag in this` is True for explicitly-registered tags AND dormant UC-010 tags."""

    def __init__(self, registered: set[str], uc010_wired: set[str]) -> None:
        self._registered = registered
        self._uc010_wired = uc010_wired

    def __contains__(self, tag: object) -> bool:
        if tag in self._registered:
            return True
        return isinstance(tag, str) and tag.startswith("T-UC-010-") and tag not in self._uc010_wired


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


def _find_stale_citations() -> list[tuple[str, int, tuple[str, ...]]]:
    """Every (relative_file, lineno, owning_tags) whose body cites #1592 but has graduated.

    A scenario is "graduated" (stale citation) when NONE of its ``@T-UC-...``
    tags are present in ``_active_xfail_tags()`` — i.e. the scenario is no
    longer registered under any xfail-registration structure, so whatever
    "#1592" text remains in its Gherkin comments describes a gap that (per
    the mechanical tag-membership rule) no longer blocks it.
    """
    active = _active_xfail_tags()
    violations: list[tuple[str, int, tuple[str, ...]]] = []

    for path_str in sorted(glob.glob(str(_FEATURES_DIR / "*.feature"))):
        path = Path(path_str)
        rel_name = path.name
        lines = path.read_text(encoding="utf-8").splitlines()

        for tags, start, end in _parse_scenarios(lines):
            uc_tags = _uc_tags(tags)
            body = lines[start:end]
            citation_lines = [start + offset + 1 for offset, text in enumerate(body) if _CITATION_SUBSTRING in text]
            if not citation_lines:
                continue
            if any(tag in active for tag in uc_tags):
                continue  # still genuinely registered — not stale
            for lineno in citation_lines:
                if (rel_name, lineno) in _ALLOWLIST:
                    continue
                violations.append((rel_name, lineno, tuple(uc_tags)))

    return violations


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
        active = _active_xfail_tags()
        live_hits: set[tuple[str, int]] = set()
        for path_str in sorted(glob.glob(str(_FEATURES_DIR / "*.feature"))):
            path = Path(path_str)
            rel_name = path.name
            lines = path.read_text(encoding="utf-8").splitlines()
            for tags, start, end in _parse_scenarios(lines):
                uc_tags = _uc_tags(tags)
                if any(tag in active for tag in uc_tags):
                    continue
                body = lines[start:end]
                for offset, text in enumerate(body):
                    if _CITATION_SUBSTRING in text:
                        live_hits.add((rel_name, start + offset + 1))

        stale_allowlist_entries = _ALLOWLIST - live_hits
        assert not stale_allowlist_entries, (
            "Stale _ALLOWLIST entries (no longer a real citation, or the tag is now "
            f"registered): {sorted(stale_allowlist_entries)}. Remove them from _ALLOWLIST."
        )


# ── Meta-tests (the guard catches the disease, and tolerates the corrected form) ──


class TestGuardMechanics:
    """Verify the tag-resolution and scenario-parsing logic in isolation."""

    def test_active_tags_include_known_xfail_tags_dict_key(self) -> None:
        """A known _XFAIL_TAGS key is recognized as active."""
        active = _active_xfail_tags()
        assert "T-UC-010-main" in active

    def test_active_tags_include_known_selective_xfail_tag(self) -> None:
        """A known _SELECTIVE_XFAIL tag is recognized as active."""
        active = _active_xfail_tags()
        assert "T-UC-010-v31-creative-approval-mode" in active

    def test_active_tags_exclude_graduated_tag(self) -> None:
        """A tag documented as removed/graduated in conftest.py is NOT active."""
        active = _active_xfail_tags()
        # T-UC-010-ext-a was removed from _XFAIL_TAGS on graduation (salesagent-rldj);
        # it must not resurface via any of the three structures.
        assert "T-UC-010-ext-a" not in active

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
        assert "T-UC-010-main" not in flagged_tags, (
            "T-UC-010-main is still a live _XFAIL_TAGS entry citing #1592 for a "
            "genuinely open gap (reporting_delivery_methods) — must not be flagged."
        )
