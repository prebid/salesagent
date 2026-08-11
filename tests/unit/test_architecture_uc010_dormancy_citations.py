"""Guard: the UC-010 dormancy xfail cites a tracking issue PER TAG, never one shared string.

A single hardcoded reason string served all 33 dormant ``T-UC-010-*`` tags and cited #1855
for every one of them. That is right for the media_buy presence-object cluster and wrong for
the signing, identity and unbacked-capability clusters — and because it was a hardcoded
fallback rather than a per-tag reason, neither stale-citation guard could see it: they read
``.feature`` comments and ``_XFAIL_TAGS``, not this branch (#1721 review F2).

A citation that is plausible but wrong is worse than none: it reads as tracked work, so
nobody re-checks it.

What this guard pins, and why each check earns its place:

* The fallback must CONSULT ``_UC010_DORMANT_TRACKING``. Without this, reverting to a shared
  string while leaving the dict in place would keep a contents-only test green — which was
  the reviewer's objection to the first draft of this guard.
* The fallback must not hardcode an issue number of its own. That is the exact regression.
* Every mapped tag must actually be dormant, so the map shrinks as scenarios get wired
  instead of accumulating stale entries.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "tests/bdd/conftest.py"
FEATURE = REPO_ROOT / "tests/bdd/features/BR-UC-010-discover-seller-capabilities.feature"

MAP_NAME = "_UC010_DORMANT_TRACKING"
_ISSUE_RE = re.compile(r"#\d{3,5}")


def _conftest_tree() -> ast.Module:
    return ast.parse(CONFTEST.read_text(), filename=str(CONFTEST))


def _dormancy_xfail_call() -> ast.Call:
    """The ``pytest.xfail(...)`` call in the UC-010 dormancy branch."""
    for node in ast.walk(_conftest_tree()):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "xfail":
            continue
        rendered = ast.unparse(node)
        if "UC-010 harness wiring not extended" in rendered:
            return node
    raise AssertionError("UC-010 dormancy xfail call not found — this guard needs updating")


def _tracking_map() -> dict[str, str]:
    for node in ast.walk(_conftest_tree()):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == MAP_NAME
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{MAP_NAME} not found in tests/bdd/conftest.py")


def _dormancy_branch_source() -> str:
    """Source of the statements around the dormancy xfail, for consult/hardcode checks."""
    text = CONFTEST.read_text()
    marker = "UC-010 harness wiring not extended"
    idx = text.index(marker)
    start = text.rfind("if not (marker_names", 0, idx)
    end = text.index("\n", idx + len(marker))
    return text[start:end]


@pytest.mark.arch_guard
def test_dormancy_reason_is_built_from_the_per_tag_map() -> None:
    """The fallback must READ the map, not just coexist with it."""
    branch = _dormancy_branch_source()
    assert MAP_NAME in branch, (
        f"The UC-010 dormancy xfail no longer consults {MAP_NAME}. A shared reason string "
        "cites the same issue for tags with different tracking homes — the defect this map "
        "replaced (#1721 review F2)."
    )


@pytest.mark.arch_guard
def test_dormancy_reason_hardcodes_no_issue_number() -> None:
    """Any issue number in the reason must come from the map, not from the literal."""
    call = _dormancy_xfail_call()
    literals = [node.value for node in ast.walk(call) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    offenders = [text for text in literals if _ISSUE_RE.search(text)]
    assert not offenders, (
        "The UC-010 dormancy reason hardcodes an issue number: "
        f"{offenders}. One literal cannot be correct for every dormant tag — "
        f"add the tag to {MAP_NAME} instead."
    )


@pytest.mark.arch_guard
def test_every_mapped_tag_is_actually_dormant() -> None:
    """A wired tag must be removed from the map — it no longer needs a dormancy citation."""
    conf = CONFTEST.read_text()
    match = re.search(r"_UC010_WIRED_TAGS[^{]*\{(.*?)\n        \}\n", conf, re.S)
    assert match is not None, "_UC010_WIRED_TAGS not found — this guard needs updating"
    wired = set(re.findall(r'"(T-UC-010-[\w-]+)"', match.group(1)))
    stale = sorted(set(_tracking_map()) & wired)
    assert not stale, f"Tags in {MAP_NAME} that are now WIRED — delete them: {stale}"


@pytest.mark.arch_guard
def test_every_mapped_tag_exists_in_the_feature_file() -> None:
    """A citation for a tag no scenario carries is dead weight and misleads."""
    tags_in_feature = set(re.findall(r"@(T-UC-010-[\w-]+)", FEATURE.read_text()))
    unknown = sorted(set(_tracking_map()) - tags_in_feature)
    assert not unknown, f"{MAP_NAME} names tags absent from the feature file: {unknown}"


@pytest.mark.arch_guard
def test_every_citation_is_a_github_issue() -> None:
    """Local beads ids do not resolve for outside contributors."""
    bad = sorted(tag for tag, ref in _tracking_map().items() if not _ISSUE_RE.fullmatch(ref))
    assert not bad, f"{MAP_NAME} entries that are not a bare GitHub issue ref: {bad}"


class TestDetectorMetaTests:
    """The consult- and hardcode-checks must actually fail on the shapes they forbid."""

    @staticmethod
    def _literals_with_issue(source: str) -> list[str]:
        call = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "xfail"
        )
        return [
            node.value
            for node in ast.walk(call)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ISSUE_RE.search(node.value)
        ]

    @pytest.mark.arch_guard
    def test_hardcoded_issue_in_a_shared_reason_is_caught(self) -> None:
        """The exact pre-fix shape."""
        assert self._literals_with_issue('pytest.xfail("dormant, never graded — tracked by #1855")')

    @pytest.mark.arch_guard
    def test_hardcoded_issue_inside_an_fstring_is_caught(self) -> None:
        """A would-be-missed case: the number hides in an f-string's literal part."""
        assert self._literals_with_issue('pytest.xfail(f"dormant{suffix} — tracked by #1855")')

    @pytest.mark.arch_guard
    def test_map_driven_reason_is_not_flagged(self) -> None:
        """The fixed shape must pass — a guard that fails it would be uninstallable."""
        assert not self._literals_with_issue('pytest.xfail(f"dormant, never graded{suffix}")')

    @pytest.mark.arch_guard
    def test_consult_check_fails_when_the_map_is_dropped(self) -> None:
        """Reverting to a shared string while keeping the dict must NOT stay green."""
        reverted = 'if not (marker_names & _UC010_WIRED_TAGS):\n    pytest.xfail("dormant, never graded")'
        assert MAP_NAME not in reverted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
