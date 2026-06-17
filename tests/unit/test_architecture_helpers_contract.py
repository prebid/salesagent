"""Contract tests for shared structural-guard helpers (PAT-01, PR 4 of #1234)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_anchor_consistency, assert_violations_match_allowlist


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
        (Path("ci.yml"), "UV_VERSION: '0.11.15'\n"),
        (Path("Dockerfile"), "ARG UV_VERSION=0.11.14\n"),
    ]
    pattern_map = {
        "ci.yml": r"UV_VERSION:\s*['\"]([\d.]+)['\"]",
        "Dockerfile": r"ARG UV_VERSION=([\d.]+)",
    }
    with pytest.raises(AssertionError, match="uv drift"):
        assert_anchor_consistency(sources, pattern_map, label="uv")
