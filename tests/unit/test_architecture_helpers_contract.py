"""Contract tests for shared structural-guard helpers (PAT-01, PR 4 of #1234)."""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_anchor_consistency,
    assert_violations_match_allowlist,
    iter_call_expressions,
    iter_git_tracked_files,
    postgres_image_ref,
    postgres_tag_pattern_map,
    uv_version_pattern_map,
)

_ITER_CALL_SOURCE = """
f()
g.h()
other.i()

def inner():
    inner_call()
"""


@pytest.mark.arch_guard
def test_iter_call_expressions_yields_all_calls_unfiltered() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    calls = list(iter_call_expressions(tree))
    assert len(calls) == 4


@pytest.mark.arch_guard
def test_iter_call_expressions_filters_by_name() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    f_calls = list(iter_call_expressions(tree, name="f"))
    assert len(f_calls) == 1
    assert isinstance(f_calls[0].func, ast.Name)
    assert f_calls[0].func.id == "f"

    h_calls = list(iter_call_expressions(tree, name="h"))
    assert len(h_calls) == 1
    assert isinstance(h_calls[0].func, ast.Attribute)
    assert h_calls[0].func.attr == "h"


@pytest.mark.arch_guard
def test_iter_call_expressions_name_matches_bare_and_attribute() -> None:
    tree = ast.parse("h()\nx.h()")
    h_calls = list(iter_call_expressions(tree, name="h"))
    assert len(h_calls) == 2


@pytest.mark.arch_guard
def test_iter_call_expressions_subtree_scope() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    inner_func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    calls = list(iter_call_expressions(inner_func))
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Name)
    assert calls[0].func.id == "inner_call"


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_passes_when_sets_equal() -> None:
    allowlist = {("tests/unit/test_foo.py", "test_bar")}
    assert_violations_match_allowlist(allowlist, allowlist)


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_flags_new_violations() -> None:
    with pytest.raises(AssertionError, match="new violations"):
        assert_violations_match_allowlist(
            {("src/core/new.py", "bad_fn")},
            set(),
            fix_hint="Fix the violation or add to the guard allowlist.",
        )


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_flags_stale_entries() -> None:
    with pytest.raises(AssertionError, match="stale entries"):
        assert_violations_match_allowlist(
            set(),
            {("src/core/fixed.py", "was_bad")},
            fix_hint="Remove fixed entries from the allowlist.",
        )


@pytest.mark.arch_guard
def test_assert_anchor_consistency_passes_when_values_match() -> None:
    sources = [
        (Path("Dockerfile"), "FROM python:3.12-slim"),
        (Path(".python-version"), "3.12.4\n"),
    ]
    pattern_map = {
        "Dockerfile": r"FROM python:([0-9]+\.[0-9]+)",
        ".python-version": r"^\s*([0-9]+\.[0-9]+)",
    }
    assert_anchor_consistency(sources, pattern_map, label="python")


@pytest.mark.arch_guard
def test_assert_anchor_consistency_flags_drift() -> None:
    sources = [
        (Path(".uv-version"), "0.11.15\n"),
        (Path("Dockerfile"), "ARG UV_VERSION=0.11.14\n"),
    ]
    with pytest.raises(AssertionError, match="uv version drift"):
        assert_anchor_consistency(sources, uv_version_pattern_map(), label="uv version")


@pytest.mark.arch_guard
def test_assert_anchor_consistency_flags_intra_file_drift() -> None:
    sources = [
        (
            Path("ci.yml"),
            f"services:\n  db1:\n    image: {postgres_image_ref('17-alpine')}\n  db2:\n    image: {postgres_image_ref('15-alpine')}\n",
        ),
    ]

    with pytest.raises(AssertionError, match="postgres image drift"):
        assert_anchor_consistency(sources, postgres_tag_pattern_map(), label="postgres image")


# ---------------------------------------------------------------------------
# iter_git_tracked_files fallback (PR #1567 round-3): the filesystem-walk
# fallback must be LOUD (RuntimeWarning) and hermetic w.r.t. untracked local
# dirs (.claude/ notes were producing spurious version-anchor guard failures
# in the in-network container, where the bind-mounted worktree's .git
# back-reference is unreachable and the fallback engages).
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_git_unavailable_fallback_warns_and_prunes_claude(tmp_path: Path) -> None:
    """Positive fixture: no git metadata -> fallback engages loudly, .claude pruned."""
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "notes.md").write_text("untracked local notes\n", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="git ls-files.*failed.*falling back"):
        files = list(iter_git_tracked_files(tmp_path))

    names = {f.relative_to(tmp_path).as_posix() for f in files}
    assert "kept.py" in names, f"fallback walk must yield regular files, got {sorted(names)}"
    assert not any(n.startswith(".claude/") for n in names), (
        f"fallback walk must prune .claude/ (untracked local notes leak into guard scans), got {sorted(names)}"
    )


@pytest.mark.arch_guard
def test_git_available_never_engages_fallback(tmp_path: Path) -> None:
    """Negative fixture: working git -> tracked files only, no fallback warning."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        files = list(iter_git_tracked_files(tmp_path))

    names = {f.relative_to(tmp_path).as_posix() for f in files}
    assert names == {"tracked.py"}, (
        f"with working git the helper must yield exactly the tracked set (hermetic), got {sorted(names)}"
    )
