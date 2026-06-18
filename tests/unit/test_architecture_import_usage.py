"""Guard: classes/functions used in src/ must be imported.

Tree-wide port of .pre-commit-hooks/check_import_usage.py (PR 4 of #1234).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import repo_root, src_python_files

_HOOK_PATH = Path(__file__).resolve().parents[2] / ".pre-commit-hooks" / "check_import_usage.py"
_spec = importlib.util.spec_from_file_location("check_import_usage", _HOOK_PATH)
assert _spec and _spec.loader
_check_import_usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_import_usage)
check_file = _check_import_usage.check_file


@pytest.mark.arch_guard
def test_no_unimported_class_usage_in_src() -> None:
    repo = repo_root()
    violations: list[str] = []
    for path in src_python_files(repo):
        if path.name == "__init__.py":
            continue
        violations.extend(check_file(path))
    assert not violations, "\n".join(violations)


@pytest.mark.arch_guard
def test_import_usage_detector_catches_known_bad_snippet(tmp_path) -> None:
    bad_file = tmp_path / "src" / "core" / "probe.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text(
        "def f():\n    return UndefinedSymbol()\n",
        encoding="utf-8",
    )
    hits = check_file(bad_file)
    assert hits, "check_file must flag symbol used without import"
