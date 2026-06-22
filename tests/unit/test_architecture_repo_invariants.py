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


@pytest.mark.arch_guard
def test_repo_invariants_fn_detector_catches_known_bad_snippet(tmp_path) -> None:
    bad_file = tmp_path / "src" / "core" / "probe.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("def f():\n    return core_get_products_tool.fn()\n", encoding="utf-8")
    hits = check_no_fn_calls([bad_file])
    assert hits, "check_no_fn_calls must flag .fn() in src/"
