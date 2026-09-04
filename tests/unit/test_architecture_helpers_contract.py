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
    rel,
    scan_src,
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
# Accessors promoted into _architecture_helpers (PR #1699): positive + negative
# contracts so a stubbed ``return True`` / wrong-name miss cannot stay green.
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_function_def_positive_and_negative() -> None:
    from tests.unit._architecture_helpers import function_def

    tree = ast.parse("def alpha():\n    pass\nasync def beta():\n    pass\n")
    assert function_def(tree, "alpha").name == "alpha"
    assert function_def(tree, "beta").name == "beta"
    with pytest.raises(AssertionError, match="Function 'missing'"):
        function_def(tree, "missing")


@pytest.mark.arch_guard
def test_assign_tuple_strs_positive_and_negative() -> None:
    from tests.unit._architecture_helpers import assign_tuple_strs

    tree = ast.parse('NAMES = ("a", "b")\nOTHER = 1\n')
    assert assign_tuple_strs(tree, "NAMES") == ("a", "b")
    with pytest.raises(AssertionError, match="Constant sequence 'OTHER'"):
        assign_tuple_strs(tree, "OTHER")
    with pytest.raises(AssertionError, match="Constant sequence 'missing'"):
        assign_tuple_strs(tree, "missing")


@pytest.mark.arch_guard
def test_call_names_positive_and_negative() -> None:
    from tests.unit._architecture_helpers import call_names

    tree = ast.parse("f()\nx.g()\n")
    names = call_names(tree)
    assert "f" in names
    assert "g" in names
    assert "missing" not in names
    assert call_names(ast.parse("x = 1\n")) == set()


@pytest.mark.arch_guard
def test_imports_name_from_positive_and_negative() -> None:
    from tests.unit._architecture_helpers import imports_name_from

    tree = ast.parse("from tests.e2e.stack_readiness import compose_argv, e2e_ports\nimport os\n")
    assert imports_name_from(tree, "tests.e2e.stack_readiness", "compose_argv") is True
    assert imports_name_from(tree, "tests.e2e.stack_readiness", "e2e_ports") is True
    # Wrong name on the right module.
    assert imports_name_from(tree, "tests.e2e.stack_readiness", "missing") is False
    # Wrong module.
    assert imports_name_from(tree, "tests.e2e.other", "compose_argv") is False
    # Plain ``import os`` is not ImportFrom — must not count as importing ``os``.
    assert imports_name_from(tree, "os", "path") is False
    assert imports_name_from(ast.parse("import os\n"), "os", "os") is False


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


# ---------------------------------------------------------------------------
# scan_src: the two suppression axes, and why their liveness rules differ.
#
# These run over a temporary tree via ``scan_dirs=`` rather than the real
# ``src/``, so they grade the RULE rather than whatever the repo happens to
# contain today. Both halves are tested: the exempt raise is exercised
# incidentally by a caller, but the skip_prefixes raise -- the semantically
# distinct half, and the one that makes a guard's scope boundary honest -- was
# graded by nothing until these landed.
# ---------------------------------------------------------------------------


def _flag_marker(tree: ast.Module) -> list[int]:
    """Trivial detector: every ``MARKER`` name reference is a violation."""
    return sorted(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "MARKER")


def _tree(tmp_path: Path, **files: str) -> list[Path]:
    root = tmp_path / "src"
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return [root]


def test_scan_src_reports_only_files_the_detector_flags(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n", "clean.py": "x = 1\n"})

    found = scan_src(_flag_marker, scan_dirs=dirs)

    assert set(found) == {rel(dirs[0] / "dirty.py")}, "a file with no findings must be omitted entirely"


def test_scan_src_suppresses_an_exempt_file(tmp_path):
    dirs = _tree(tmp_path, **{"sanctioned.py": "MARKER\n"})

    found = scan_src(_flag_marker, exempt=frozenset({rel(dirs[0] / "sanctioned.py")}), scan_dirs=dirs)

    assert found == {}, "a live exemption must suppress its file"


def test_scan_src_raises_on_an_exemption_that_suppresses_nothing(tmp_path):
    """An exempt entry the detector never flags is worse than no entry.

    It reads as a considered decision while doing nothing, and pre-authorizes
    the violation if the file ever acquires one.
    """
    dirs = _tree(tmp_path, **{"clean.py": "x = 1\n"})

    with pytest.raises(AssertionError, match="dead exemption"):
        scan_src(_flag_marker, exempt=frozenset({rel(dirs[0] / "clean.py")}), scan_dirs=dirs)


def test_scan_src_raises_on_an_exemption_for_a_path_that_does_not_exist(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead exemption"):
        scan_src(_flag_marker, exempt=frozenset({"src/nonexistent.py"}), scan_dirs=dirs)


def test_scan_src_suppresses_a_prefix_when_at_least_one_file_under_it_is_flagged(tmp_path):
    """A scope boundary need only cover ONE violation, unlike an exemption.

    Most files under a legitimately-excluded subtree are clean; requiring each
    to be flagged would red the build on innocent files, which is why this rule
    is deliberately weaker than the exempt one.
    """
    dirs = _tree(tmp_path, **{"pkg/dirty.py": "MARKER\n", "pkg/clean.py": "x = 1\n", "outside.py": "MARKER\n"})

    found = scan_src(_flag_marker, skip_prefixes=(rel(dirs[0] / "pkg"),), scan_dirs=dirs)

    assert set(found) == {rel(dirs[0] / "outside.py")}, "the whole prefix must be excluded, clean files included"


def test_scan_src_raises_on_a_prefix_that_excludes_nothing(tmp_path):
    """A boundary with no violation behind it would silently permit the first one."""
    dirs = _tree(tmp_path, **{"pkg/clean.py": "x = 1\n", "outside.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead scope boundary"):
        scan_src(_flag_marker, skip_prefixes=(rel(dirs[0] / "pkg"),), scan_dirs=dirs)


def test_scan_src_raises_on_a_prefix_matching_no_file_at_all(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead scope boundary"):
        scan_src(_flag_marker, skip_prefixes=("src/nonexistent/",), scan_dirs=dirs)


def test_scan_src_does_not_raise_when_nothing_is_suppressed(tmp_path):
    """Empty suppression sets have nothing that could be dead."""
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    assert scan_src(_flag_marker, exempt=frozenset(), skip_prefixes=(), scan_dirs=dirs)
