"""Tests for tests/unit/_architecture_helpers.py.

Without this meta-guard, a regression in the shared helpers can silently
break every guard. Every helper API exercised here is also called by guards
in this directory.
"""

from pathlib import Path

import pytest
from tests.unit._architecture_helpers import (
    assert_anchor_consistency,
    assert_violations_match_allowlist,
    iter_action_uses,
    iter_compose_files,
    iter_function_defs,
    iter_workflow_files,
    parse_module,
    repo_root,
    src_python_files,
)


def test_parse_module_caches_by_mtime(tmp_path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n")
    a = parse_module(p)
    b = parse_module(p)
    assert a is b, "parse_module must cache by mtime+path"


def test_iter_function_defs_yields_sync_and_async(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("def a(): pass\nasync def b(): pass\n")
    names = sorted(fn.name for fn in iter_function_defs(parse_module(p)))
    assert names == ["a", "b"]


def test_iter_workflow_files_finds_yml_and_yaml():
    files = list(iter_workflow_files(repo_root()))
    assert len(files) > 0
    assert all(f.suffix in {".yml", ".yaml"} for f in files)


def test_iter_compose_files_matches_glob():
    files = list(iter_compose_files(repo_root()))
    assert all("docker-compose" in f.name or f.name == "compose.yaml" for f in files)


def test_iter_action_uses_matches_actions_only():
    sample = "      - uses: actions/checkout@abc123  # v4\n      - run: echo ok\n"
    refs = list(iter_action_uses(sample))
    assert refs == [("actions/checkout", "abc123", "  # v4")]


def test_src_python_files_excludes_original_gam():
    files = list(src_python_files(repo_root()))
    assert not any("google_ad_manager_original" in str(f) for f in files)


def test_assert_violations_match_allowlist_detects_stale():
    found = {("a.py", 1)}
    allowed = {("a.py", 1), ("b.py", 99)}  # b.py:99 is stale
    with pytest.raises(AssertionError, match="stale"):
        assert_violations_match_allowlist(found, allowed)


def test_assert_violations_match_allowlist_detects_new():
    found = {("a.py", 1), ("c.py", 5)}  # c.py:5 is new
    allowed = {("a.py", 1)}
    with pytest.raises(AssertionError, match="new"):
        assert_violations_match_allowlist(found, allowed)


def test_assert_anchor_consistency_passes_on_match():
    sources = [(Path("/x/Dockerfile"), "ARG PYTHON_VERSION=3.12"), (Path("/x/.python-version"), "3.12\n")]
    pattern_map = {
        "/x/Dockerfile": r"ARG PYTHON_VERSION=([\d.]+)",
        "/x/.python-version": r"^([\d.]+)",
    }
    assert_anchor_consistency(sources, pattern_map, label="python")  # no raise


def test_assert_anchor_consistency_raises_on_drift():
    sources = [(Path("/x/Dockerfile"), "ARG PYTHON_VERSION=3.12"), (Path("/x/.python-version"), "3.13\n")]
    pattern_map = {
        "/x/Dockerfile": r"ARG PYTHON_VERSION=([\d.]+)",
        "/x/.python-version": r"^([\d.]+)",
    }
    with pytest.raises(AssertionError, match="drift"):
        assert_anchor_consistency(sources, pattern_map, label="python")
