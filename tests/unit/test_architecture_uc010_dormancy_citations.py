"""Guard: the UC-010 dormancy xfail cites a tracking issue PER TAG, never one shared string.

A single hardcoded reason string served all 33 dormant ``T-UC-010-*`` tags and cited #1855
for every one of them. That is right for the media_buy presence-object cluster and wrong for
the signing, identity and unbacked-capability clusters — and because it was a hardcoded
fallback rather than a per-tag reason, neither stale-citation guard could see it: they read
``.feature`` comments and ``_XFAIL_TAGS``, not this branch (#1721 review F2).

A citation that is plausible but wrong is worse than none: it reads as tracked work, so
nobody re-checks it.

Where the obligation lives NOW
------------------------------
#1858 deleted ``_harness_env``'s ``elif`` chain and ``_detect_uc``. Routing is declarative:
``EnvRoute`` rows in ``ENV_ROUTES``, resolved by ``storyboard_spec.resolve_env_route`` over
the marker set from ``derive_marker_names``. The UC-010 dormancy branch became
``_uc010_dormancy_rows()`` — ONE row generated per ``_UC010_DORMANT_TRACKING`` entry, each
carrying that tag's own ``xfail_reason``, plus a citation-free ``uc010-not-wired`` catch-all
for a dormant tag with no established tracking home.

The property is unchanged, so the guard is unchanged in force; only its LOCATOR moved. The
previous locator pinned the deleted shape (an inline ``pytest.xfail(...)`` inside an
``if not (marker_names ...)`` branch) and, worse, degraded silently: its source-slicing
helper returned ``""`` when the anchor vanished, so the consult-check was asserting against
an empty string rather than against production.

What this guard pins, and why each check earns its place:

* Its own subjects still resolve (:func:`assert_guard_subject_resolves`). A guard that
  reaches production by string keeps passing after a rename — it finds nothing, which is
  indistinguishable from finding nothing wrong. That is exactly how this file broke.
* The reason a dormant tag ACTUALLY xfails with — obtained by running the real resolver over
  the real registry, not by reading source — must cite exactly that tag's own issue. One
  shared literal cannot satisfy this for tags with different tracking homes, so reverting to
  a shared string cannot stay green whether or not the map survives beside it.
* No string literal in a UC-010 dormancy reason may hardcode an issue number. This is the
  structural complement: once the map shrinks to a single distinct issue, the behavioral
  check alone could no longer tell a hardcoded literal from a map lookup.
* Every mapped tag must actually be dormant, so the map shrinks as scenarios get wired
  instead of accumulating stale entries.
* Every ``_UC010_PARKED_TAGS`` narrative — the text saying a tag cannot be wired HONESTLY
  yet, as opposed to merely not got to — has a map row to be built into. The row composes
  "<park text> — tracked by <map issue>", so a park entry outside the map reaches no
  scenario and the distinction it records is lost without anything going red.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
from collections.abc import Mapping
from typing import Any

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_guard_subject_resolves,
    assert_scanned_paths_exist,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "tests/bdd/conftest.py"
FEATURE = REPO_ROOT / "tests/bdd/features/BR-UC-010-discover-seller-capabilities.feature"

CONFTEST_MODULE = "tests.bdd.conftest"
MAP_NAME = "_UC010_DORMANT_TRACKING"
PARK_NAME = "_UC010_PARKED_TAGS"
ROUTES_NAME = "ENV_ROUTES"
WIRED_ACCESSOR = "_uc010_wired_tags"
REASON_KWARG = "xfail_reason"

_ISSUE_RE = re.compile(r"#\d{3,5}")


def _bdd_conftest() -> Any:
    """The BDD conftest as a module — same dotted name pytest imports it under."""
    return importlib.import_module(CONFTEST_MODULE)


def _tracking_map() -> dict[str, str]:
    return dict(getattr(_bdd_conftest(), MAP_NAME))


def _routed_xfail_reason(tag: str) -> str | None:
    """The reason a scenario carrying only *tag* would ACTUALLY xfail with.

    Runs the production resolver over the production registry, so it answers for whatever
    shape the routing takes — an inline branch, a generated row, or something later. A tag
    that resolves to no row at all, or to a row that builds an env instead of xfailing,
    yields ``None`` and is reported as an uncited tag rather than passing quietly.
    """
    conftest = _bdd_conftest()
    from scripts.audit import storyboard_spec

    route = storyboard_spec.resolve_env_route(frozenset({tag}), getattr(conftest, ROUTES_NAME))
    return None if route is None else route.xfail_reason


def _issue_refs(text: str) -> frozenset[str]:
    return frozenset(_ISSUE_RE.findall(text))


def _miscitations(reasons: Mapping[str, str | None], tracking: Mapping[str, str]) -> list[str]:
    """Mapped tags whose actual dormancy reason does not cite exactly their own issue.

    Extracted from the test body so the meta-tests below can feed it a synthetic shared
    reason string and prove the comparison rejects it.
    """
    offenders: list[str] = []
    for tag, issue in sorted(tracking.items()):
        reason = reasons.get(tag)
        cited = frozenset() if reason is None else _issue_refs(reason)
        if cited != {issue}:
            offenders.append(f"{tag}: cites {sorted(cited) or 'nothing'}, must cite exactly [{issue}] — {reason!r}")
    return offenders


def _reason_keywords(tree: ast.AST) -> list[ast.keyword]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == REASON_KWARG]


def find_hardcoded_issue_literals(tree: ast.Module) -> list[int]:
    """Line numbers of string literals carrying an issue ref inside a UC-010 dormancy reason.

    Two independent anchors, unioned, because either alone is a rename away from finding
    nothing:

    * a reason built in a scope that READS ``_UC010_DORMANT_TRACKING`` — this is the
      generated-row shape, and the map name is itself pinned by
      ``test_guard_subjects_still_resolve``;
    * a reason whose source text names UC-010 — this catches a reason that stopped consulting
      the map, which is the regression itself.

    Literal parts of an f-string count: the number can hide beside the interpolation.
    """
    keywords: dict[int, ast.keyword] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            isinstance(sub, ast.Name) and sub.id == MAP_NAME for sub in ast.walk(node)
        ):
            keywords.update({id(kw): kw for kw in _reason_keywords(node)})

    for keyword in _reason_keywords(tree):
        if "UC-010" in ast.unparse(keyword.value):
            keywords[id(keyword)] = keyword

    return sorted(
        node.lineno
        for keyword in keywords.values()
        for node in ast.walk(keyword.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ISSUE_RE.search(node.value)
    )


@pytest.mark.arch_guard
def test_guard_subjects_still_resolve() -> None:
    """Every production name this guard reaches for must still exist.

    This file's previous locator pinned a shape #1858 deleted and kept passing on the checks
    that could not tell absence from compliance. Resolving the names makes the next such move
    fail loudly here instead.
    """
    assert_guard_subject_resolves(
        CONFTEST_MODULE,
        MAP_NAME,
        PARK_NAME,
        ROUTES_NAME,
        WIRED_ACCESSOR,
        why="The UC-010 dormancy citations would go unguarded: nothing else checks that a "
        "dormant tag xfails against its OWN tracking issue rather than a shared one.",
    )
    assert_scanned_paths_exist(
        [str(CONFTEST), str(FEATURE)],
        why="This guard reads both by path; a rename would drain it silently.",
    )


@pytest.mark.arch_guard
def test_the_tracking_map_still_has_entries_to_guard() -> None:
    """An empty map makes every check below vacuous — retire the mechanism deliberately."""
    assert _tracking_map(), (
        f"{MAP_NAME} is empty. If every UC-010 scenario is now wired, delete the map, the "
        "dormancy rows that consume it, and this guard together — do not leave a guard whose "
        "subject no longer exists standing green."
    )


@pytest.mark.arch_guard
def test_dormancy_reason_is_built_from_the_per_tag_map() -> None:
    """The reason a dormant tag really xfails with must cite that tag's OWN issue.

    Asked of the production resolver, not of source text: a shared reason string cites the
    same issue for tags with different tracking homes, so it fails here for every tag it is
    wrong about — the defect this map replaced (#1721 review F2).
    """
    tracking = _tracking_map()
    reasons = {tag: _routed_xfail_reason(tag) for tag in tracking}
    offenders = _miscitations(reasons, tracking)
    assert not offenders, (
        f"Dormant UC-010 tags whose xfail reason does not cite exactly their {MAP_NAME} entry:\n"
        + "\n".join(f"  {line}" for line in offenders)
    )


@pytest.mark.arch_guard
def test_dormancy_reason_hardcodes_no_issue_number() -> None:
    """Any issue number in the reason must come from the map, not from the literal."""
    tree = ast.parse(CONFTEST.read_text(), filename=str(CONFTEST))
    offenders = find_hardcoded_issue_literals(tree)
    assert not offenders, (
        "A UC-010 dormancy reason hardcodes an issue number at "
        f"{CONFTEST}:{offenders}. One literal cannot be correct for every dormant tag — "
        f"add the tag to {MAP_NAME} instead."
    )


@pytest.mark.arch_guard
def test_every_mapped_tag_is_actually_dormant() -> None:
    """A wired tag must be removed from the map — it no longer needs a dormancy citation."""
    wired = set(getattr(_bdd_conftest(), WIRED_ACCESSOR)())
    assert wired, f"{WIRED_ACCESSOR}() is empty — this guard would compare against nothing."
    stale = sorted(set(_tracking_map()) & wired)
    assert not stale, f"Tags in {MAP_NAME} that are now WIRED — delete them: {stale}"


@pytest.mark.arch_guard
def test_every_parked_narrative_has_a_tracking_entry() -> None:
    """A park text for a tag outside the map would be built into no row at all.

    The dormancy rows are generated FROM the map, taking the park's text as the reason
    where one exists. A parked tag missing from the map therefore loses its text
    silently — the scenario would xfail with the generic dormancy sentence and nobody
    would see that the "cannot be wired honestly yet" narrative had stopped being read.
    """
    orphans = sorted(set(getattr(_bdd_conftest(), PARK_NAME)) - set(_tracking_map()))
    assert not orphans, f"{PARK_NAME} entries with no {MAP_NAME} row, so their text reaches no scenario: {orphans}"


@pytest.mark.arch_guard
def test_every_mapped_tag_exists_in_the_feature_file() -> None:
    """A citation for a tag no scenario carries is dead weight and misleads."""
    tags_in_feature = set(re.findall(r"@(T-UC-010-[\w-]+)", FEATURE.read_text()))
    assert tags_in_feature, f"No T-UC-010-* tags found in {FEATURE} — this guard needs updating."
    unknown = sorted(set(_tracking_map()) - tags_in_feature)
    assert not unknown, f"{MAP_NAME} names tags absent from the feature file: {unknown}"


@pytest.mark.arch_guard
def test_every_citation_is_a_github_issue() -> None:
    """Local beads ids do not resolve for outside contributors."""
    bad = sorted(tag for tag, ref in _tracking_map().items() if not _ISSUE_RE.fullmatch(ref))
    assert not bad, f"{MAP_NAME} entries that are not a bare GitHub issue ref: {bad}"


class TestDetectorMetaTests:
    """The per-tag- and hardcode-checks must actually fail on the shapes they forbid."""

    TRACKING = {"T-UC-010-signing": "#1291", "T-UC-010-governance": "#1855"}

    @pytest.mark.arch_guard
    def test_one_shared_reason_is_caught(self) -> None:
        """The exact pre-fix shape: one string, one citation, every tag."""
        shared = "UC-010 harness wiring not extended (dormant, never graded) — tracked by #1855"
        offenders = _miscitations(dict.fromkeys(self.TRACKING, shared), self.TRACKING)
        assert [line.split(":")[0] for line in offenders] == ["T-UC-010-signing"]

    @pytest.mark.arch_guard
    def test_a_citation_free_shared_reason_is_caught(self) -> None:
        """Dropping the citation entirely is not a fix — every mapped tag must still cite."""
        bare = "UC-010 harness wiring not extended (dormant, never graded)"
        offenders = _miscitations(dict.fromkeys(self.TRACKING, bare), self.TRACKING)
        assert len(offenders) == len(self.TRACKING)

    @pytest.mark.arch_guard
    def test_an_unrouted_tag_is_caught(self) -> None:
        """A tag that resolves to no dormancy row at all must not read as compliant."""
        assert len(_miscitations({}, self.TRACKING)) == len(self.TRACKING)

    @pytest.mark.arch_guard
    def test_map_driven_reasons_are_not_flagged(self) -> None:
        """The fixed shape must pass — a guard that fails it would be uninstallable."""
        reasons = {tag: f"UC-010 dormant — tracked by {issue}" for tag, issue in self.TRACKING.items()}
        assert not _miscitations(reasons, self.TRACKING)

    @pytest.mark.arch_guard
    def test_hardcoded_issue_shapes_are_caught(self) -> None:
        """Both anchors, and the f-string literal part the plain-constant scan would miss."""
        assert_detector_catches_ast_snippets(
            find_hardcoded_issue_literals,
            snippets={
                "shared literal in the map-consuming builder": (
                    "def _uc010_dormancy_rows():\n"
                    "    return [\n"
                    '        EnvRoute(tag=t, xfail_reason="dormant, never graded — tracked by #1855")\n'
                    f"        for t, issue in sorted({MAP_NAME}.items())\n"
                    "    ]\n"
                ),
                "shared literal on a row that no longer reads the map": (
                    'EnvRoute(tag="uc010-not-wired", xfail_reason="UC-010 dormant — tracked by #1855")'
                ),
                "issue number hidden in an f-string literal part": (
                    'EnvRoute(tag="uc010-not-wired", xfail_reason=f"UC-010 dormant{suffix} — tracked by #1855")'
                ),
            },
        )

    @pytest.mark.arch_guard
    def test_the_map_driven_row_shape_is_not_flagged(self) -> None:
        """Production's real shape, and an unrelated row's legitimate citation, stay clean."""
        good = (
            "def _uc010_dormancy_rows():\n"
            "    rows = [\n"
            '        EnvRoute(tag=f"uc010-dormant-{tag}", xfail_reason=f"UC-010 dormant — tracked by {issue}")\n'
            f"        for tag, issue in sorted({MAP_NAME}.items())\n"
            "    ]\n"
            '    rows.append(EnvRoute(tag="uc010-not-wired", xfail_reason="UC-010 dormant, never graded"))\n'
            "    return rows\n"
            'OTHER = EnvRoute(tag="uc018", xfail_reason="UC-018 harness wiring is tracked in #1652")\n'
        )
        assert not find_hardcoded_issue_literals(ast.parse(good))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
