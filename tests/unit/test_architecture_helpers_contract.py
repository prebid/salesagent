"""Contract tests for shared structural-guard helpers (PAT-01, PR 4 of #1234)."""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_anchor_consistency,
    assert_violations_match_allowlist,
    forwarding_gaps,
    iter_call_expressions,
    iter_git_tracked_files,
    postgres_image_ref,
    postgres_tag_pattern_map,
    raw_wrapper_param_names,
    stale_forwarding_allowlist_entries,
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


# ---------------------------------------------------------------------------
# raw_wrapper_param_names — shared by the REST/A2A transport-parity guards.
# ---------------------------------------------------------------------------


def _sample_raw_wrapper(media_buy_id, tags=None, *args, ctx=None, identity=None, fields=None, **kwargs):
    """Stand-in for a ``*_raw`` wrapper covering every parameter kind."""


@pytest.mark.arch_guard
def test_raw_wrapper_param_names_drops_caller_supplied_plumbing() -> None:
    names = raw_wrapper_param_names(_sample_raw_wrapper, plumbing={"ctx", "identity"})
    assert names == {"media_buy_id", "tags", "fields"}, (
        f"expected buyer-facing params only (plumbing + *args/**kwargs excluded), got {sorted(names)}"
    )


@pytest.mark.arch_guard
def test_raw_wrapper_param_names_plumbing_set_is_per_caller() -> None:
    """Each guard owns its plumbing set — a wider set must drop strictly more params."""
    a2a_like = raw_wrapper_param_names(_sample_raw_wrapper, plumbing={"ctx", "identity"})
    rest_like = raw_wrapper_param_names(_sample_raw_wrapper, plumbing={"ctx", "identity", "fields"})
    assert a2a_like - rest_like == {"fields"}
    assert not rest_like - a2a_like


# ---------------------------------------------------------------------------
# forwarding_gaps / stale_forwarding_allowlist_entries — the comparison the
# REST *Body guard and the A2A skill-handler guard both run. Pinned here so the
# shared comparison itself has an oracle, not just its two callers.
# ---------------------------------------------------------------------------

_SAMPLE_PLUMBING = {"ctx", "identity"}


@pytest.mark.arch_guard
def test_forwarding_gaps_reports_undeclared_params() -> None:
    gaps = forwarding_gaps(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={},
    )
    assert gaps == {"tags", "fields"}, f"expected the two undeclared buyer-facing params, got {sorted(gaps)}"


@pytest.mark.arch_guard
def test_forwarding_gaps_suppresses_allowlisted_params_only() -> None:
    """An allowlist entry hides exactly its own param — never widens the pass."""
    gaps = forwarding_gaps(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={"tags": "justified omission"},
    )
    assert gaps == {"fields"}, f"only the allowlisted param may be suppressed, got {sorted(gaps)}"


@pytest.mark.arch_guard
def test_forwarding_gaps_empty_when_surface_declares_everything() -> None:
    """Negative control: a complete surface has no gaps (the guard is not always-red)."""
    assert not forwarding_gaps(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id", "tags", "fields"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={},
    )


@pytest.mark.arch_guard
def test_stale_allowlist_entry_flagged_when_not_a_wrapper_param() -> None:
    stale = stale_forwarding_allowlist_entries(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={"not_a_param": "obsolete"},
        declared_desc="declared on the Body",
        entry_prefix="SampleBody.",
    )
    assert len(stale) == 1, f"expected exactly one stale line, got {stale}"
    assert "SampleBody.not_a_param" in stale[0]
    assert "not a parameter of _sample_raw_wrapper()" in stale[0]


@pytest.mark.arch_guard
def test_stale_allowlist_entry_flagged_once_surface_declares_it() -> None:
    """The shrink ratchet: a param the surface now declares must leave the allowlist."""
    stale = stale_forwarding_allowlist_entries(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id", "tags"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={"tags": "was missing"},
        declared_desc="forwarded by the handler",
    )
    assert len(stale) == 1, f"expected exactly one stale line, got {stale}"
    assert "tags: now forwarded by the handler" in stale[0]
    assert not stale[0].startswith("  SampleBody"), "entry_prefix defaults to empty for single-surface guards"


@pytest.mark.arch_guard
def test_stale_allowlist_entries_empty_for_a_live_justified_omission() -> None:
    """Negative control: a real, still-missing param is NOT stale."""
    assert not stale_forwarding_allowlist_entries(
        raw_fn=_sample_raw_wrapper,
        declared={"media_buy_id"},
        plumbing=_SAMPLE_PLUMBING,
        allowlist={"tags": "justified omission"},
        declared_desc="declared on the Body",
    )
