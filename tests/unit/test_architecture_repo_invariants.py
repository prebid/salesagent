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
    ok_file.write_text(
        'skip_no_agent = pytest.mark.skipif(True, reason="no agent")\n@skip_no_agent\ndef test_probe():\n    pass\n',
        encoding="utf-8",
    )
    hits = check_no_skip_tests([ok_file])
    assert not hits, "check_no_skip_tests must not flag conditional skip marker"
