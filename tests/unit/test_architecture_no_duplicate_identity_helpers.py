"""Guard: auth-edge identity helpers must live once, on BaseTestEnv.

``invalid_token_identity()`` / ``anonymous_identity()`` were previously
hand-rolled on ``CapabilitiesEnv`` (each independently calling
``PrincipalFactory.make_identity(...)``) instead of being defined once on
``BaseTestEnv`` for every Env subclass to inherit (salesagent-weip). This
guard pins that fix: no Env subclass under ``tests/harness/`` may redefine
either method locally.

Scanning approach: AST -- walk every class definition in ``tests/harness/*.py``
and flag a class (other than ``BaseTestEnv`` itself) that defines a method
named ``invalid_token_identity`` or ``anonymous_identity`` in its own body.

beads: salesagent-weip
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"

_GUARDED_METHOD_NAMES = {"invalid_token_identity", "anonymous_identity"}

# The one class allowed to define these methods -- every other Env subclass
# must inherit them from here.
_CANONICAL_OWNER = "BaseTestEnv"


def _iter_class_method_violations() -> list[str]:
    """Yield 'file:line ClassName.method_name' for any non-owner redefinition."""
    violations = []
    for py_file in sorted(_HARNESS_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name == _CANONICAL_OWNER:
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in _GUARDED_METHOD_NAMES:
                    relative = py_file.relative_to(_HARNESS_DIR.parent.parent)
                    violations.append(f"{relative}:{item.lineno} {node.name}.{item.name}")
    return violations


class TestNoDuplicateIdentityHelpers:
    """Structural guard: invalid_token_identity/anonymous_identity live once."""

    @pytest.mark.arch_guard
    def test_no_env_subclass_redefines_identity_helpers(self):
        """No Env subclass other than BaseTestEnv may redefine these methods."""
        violations = _iter_class_method_violations()
        assert not violations, (
            "Found Env subclass(es) redefining invalid_token_identity/anonymous_identity "
            f"instead of inheriting from {_CANONICAL_OWNER} (tests/harness/_base.py):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    @pytest.mark.arch_guard
    def test_canonical_owner_defines_both_methods(self):
        """BaseTestEnv itself must define both methods (positive control)."""
        base_file = _HARNESS_DIR / "_base.py"
        tree = ast.parse(base_file.read_text(), filename=str(base_file))
        defined_on_base = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == _CANONICAL_OWNER:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in _GUARDED_METHOD_NAMES:
                        defined_on_base.add(item.name)
        assert defined_on_base == _GUARDED_METHOD_NAMES, (
            f"{_CANONICAL_OWNER} must define both {_GUARDED_METHOD_NAMES}, "
            f"found {defined_on_base} -- did the hoist get reverted?"
        )

    @pytest.mark.arch_guard
    def test_regression_case_would_be_caught(self, tmp_path):
        """Negative meta-test: a re-introduced duplicate on a fake Env subclass must be caught.

        Writes a throwaway harness-shaped file defining a subclass that
        redefines invalid_token_identity, and confirms the scan (run against
        that directory) flags it -- proves the guard isn't vacuously passing.
        """
        fake_dir = tmp_path / "harness"
        fake_dir.mkdir()
        (fake_dir / "_base.py").write_text(
            "class BaseTestEnv:\n"
            "    def invalid_token_identity(self):\n"
            "        pass\n"
            "    def anonymous_identity(self):\n"
            "        pass\n"
        )
        (fake_dir / "regressed_env.py").write_text(
            "class RegressedEnv:\n    def invalid_token_identity(self):\n        return None\n"
        )

        violations = []
        for py_file in sorted(fake_dir.glob("*.py")):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name == _CANONICAL_OWNER:
                    continue
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in _GUARDED_METHOD_NAMES:
                        violations.append(f"{py_file.name}:{item.lineno} {node.name}.{item.name}")

        assert violations == ["regressed_env.py:2 RegressedEnv.invalid_token_identity"]
