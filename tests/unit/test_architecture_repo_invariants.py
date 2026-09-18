"""Guard: repo-invariants pre-commit hook catches known-bad patterns.

Self-tests for `.pre-commit-hooks/check_repo_invariants.py` (PR 4 of #1234).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK_PATH = Path(__file__).resolve().parents[2] / ".pre-commit-hooks" / "check_repo_invariants.py"
_spec = importlib.util.spec_from_file_location("check_repo_invariants", _HOOK_PATH)
assert _spec and _spec.loader
_check_repo_invariants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_repo_invariants)
check_no_fn_calls = _check_repo_invariants.check_no_fn_calls
check_no_skip_tests = _check_repo_invariants.check_no_skip_tests
check_no_developer_paths = _check_repo_invariants.check_no_developer_paths


@pytest.mark.arch_guard
def test_repo_invariants_fn_detector_catches_known_bad_snippet(tmp_path) -> None:
    bad_file = tmp_path / "src" / "core" / "probe.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("def f():\n    return core_get_products_tool.fn()\n", encoding="utf-8")
    hits = check_no_fn_calls([bad_file])
    assert hits, "check_no_fn_calls must flag .fn() in src/"


@pytest.mark.arch_guard
def test_repo_invariants_skip_detector_catches_bare_skip(tmp_path) -> None:
    bad_file = tmp_path / "tests" / "unit" / "test_probe.py"
    bad_file.parent.mkdir(parents=True)
    bare_skip = "@pytest.mark." + "skip"
    bad_file.write_text(f"{bare_skip}(reason='temporary')\ndef test_probe():\n    pass\n", encoding="utf-8")
    hits = check_no_skip_tests([bad_file])
    assert hits, "check_no_skip_tests must flag bare skip marker"


@pytest.mark.arch_guard
def test_repo_invariants_skip_detector_allows_skipif(tmp_path) -> None:
    ok_file = tmp_path / "tests" / "integration" / "test_probe.py"
    ok_file.parent.mkdir(parents=True)
    skipif_dec = "@pytest.mark." + "skipif"  # keep the literal out of this file's source
    ok_file.write_text(
        f'{skipif_dec}(True, reason="conditional")\ndef test_probe():\n    pass\n',
        encoding="utf-8",
    )
    hits = check_no_skip_tests([ok_file])
    assert not hits, "check_no_skip_tests must not flag @pytest.mark.skipif"


@pytest.mark.arch_guard
def test_developer_path_detector_catches_a_hardcoded_home(tmp_path) -> None:
    """The content half: an absolute home path in a doc/formula/script."""
    doc = tmp_path / "formula.yaml"
    # Composed, never written literally: this file is itself scanned by the
    # tree-wide ratchet below, and a literal here would be a real violation.
    home = "/" + "Users" + "/alice"
    doc.write_text(f"  cwd: {home}/projects/adcp\n", encoding="utf-8")
    hits = check_no_developer_paths([doc])
    assert hits, "a hardcoded /Users/<name>/ path must be flagged"
    assert f"{home}/" in hits[0]


@pytest.mark.arch_guard
def test_developer_path_detector_catches_a_symlink_into_a_home(tmp_path) -> None:
    """The symlink half — the one that breaks git, not just portability.

    A tracked symlink into one machine makes its subtree unreachable:
    `fatal: pathspec '<dir>/f.md' is beyond a symbolic link`, so files written
    there cannot be committed from a worktree at all. #2228 removed two of these.
    """
    link = tmp_path / "research"
    home = "/" + "Users" + "/bob"
    link.symlink_to(f"{home}/projects/salesagent/.claude/research")
    hits = check_no_developer_paths([link])
    assert hits, "a symlink pointing into a developer home must be flagged"
    assert "symlink" in hits[0]


@pytest.mark.arch_guard
@pytest.mark.parametrize("path", ["/home/user/x", "/" + "Users" + "/<you>/x", "$HOME/x", "~/x", "../adcp/x"])
def test_developer_path_detector_allows_placeholders_and_relative_paths(tmp_path, path) -> None:
    """Documentation stand-ins and portable paths are not machines."""
    doc = tmp_path / "README.md"
    doc.write_text(f"see {path}\n", encoding="utf-8")
    assert not check_no_developer_paths([doc]), f"{path} is portable/documentation, not a developer home"


@pytest.mark.arch_guard
def test_no_developer_paths_remain_in_the_tree() -> None:
    """The ratchet: what #2228 removed must not come back.

    Scans tracked files rather than a sample, so a new offender fails here even if
    it is committed with --no-verify.
    """
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    files = [repo / f for f in tracked if f]
    hits = check_no_developer_paths([f for f in files if f.exists() or f.is_symlink()])
    assert not hits, "developer-specific paths reintroduced:\n" + "\n".join(hits[:10])
