"""Guard: tuple[bool, ...]-returning validators must be unpacked, never truth-tested.

A ``tuple[bool, str | None]`` result is ALWAYS truthy, so ``if result:`` takes
the error branch unconditionally (salesagent-jrb5: the mock-adapter config
write path was dead code behind exactly this). This guard scans src/ for the
two shapes that produced it:

- ``if validator(...):`` — direct truth test of a tuple-returning call
- ``x = validator(...)`` followed by ``if x:`` in the same function

where ``validator`` is any function annotated ``-> tuple[bool, ...]`` in src/.
"""

import ast
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent.parent / "src"

ALLOWLIST: set[str] = set()


def _tuple_bool_validators(src_root: Path) -> set[str]:
    names: set[str] = set()
    for path in src_root.rglob("*.py"):
        for m in re.finditer(r"def (\w+)\([^)]*\)[^:]*-> tuple\[bool", path.read_text()):
            names.add(m.group(1))
    return names


def _call_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)


def _truthiness_violations(tree: ast.AST, validators: set[str]) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _call_name(node.test) in validators:
            lines.append(node.lineno)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            assigned: dict[str, str] = {}
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Assign)
                    and len(sub.targets) == 1
                    and isinstance(sub.targets[0], ast.Name)
                    and (name := _call_name(sub.value)) in validators
                    and name is not None
                ):
                    assigned[sub.targets[0].id] = name
            for sub in ast.walk(node):
                if isinstance(sub, ast.If) and isinstance(sub.test, ast.Name) and sub.test.id in assigned:
                    lines.append(sub.lineno)
    return lines


class TestNoTupleTruthiness:
    @pytest.mark.arch_guard
    def test_no_truth_test_of_tuple_bool_validators(self):
        validators = _tuple_bool_validators(_SRC)
        assert validators, "scan precondition: tuple[bool validators exist in src/"
        violations: list[str] = []
        for path in sorted(_SRC.rglob("*.py")):
            rel = str(path.relative_to(_SRC.parent))
            if rel in ALLOWLIST:
                continue
            for lineno in _truthiness_violations(ast.parse(path.read_text()), validators):
                violations.append(f"{rel}:{lineno}")
        assert not violations, (
            "tuple[bool, ...] validator result used in a boolean context (always truthy) — "
            f"unpack it: is_valid, error = validator(...): {violations}"
        )

    @pytest.mark.arch_guard
    def test_guard_detects_planted_direct_if(self):
        tree = ast.parse("if validate_product_config(cfg):\n    pass\n")
        assert _truthiness_violations(tree, {"validate_product_config"}) == [1]

    @pytest.mark.arch_guard
    def test_guard_detects_planted_assigned_if(self):
        tree = ast.parse(
            "def view(cfg):\n"
            "    validation_errors = self.validate_product_config(cfg)\n"
            "    if validation_errors:\n"
            "        pass\n"
        )
        assert _truthiness_violations(tree, {"validate_product_config"}) == [3]

    @pytest.mark.arch_guard
    def test_guard_ignores_unpacked_result(self):
        tree = ast.parse(
            "def view(cfg):\n"
            "    is_valid, error = self.validate_product_config(cfg)\n"
            "    if not is_valid:\n"
            "        pass\n"
        )
        assert _truthiness_violations(tree, {"validate_product_config"}) == []

    @pytest.mark.arch_guard
    def test_guard_ignores_list_returning_validators(self):
        """Would-be-missed case: list-returning validators are legitimately truth-tested."""
        tree = ast.parse(
            "def view(data):\n"
            "    validation_errors = validate_gam_config(data)\n"
            "    if validation_errors:\n"
            "        pass\n"
        )
        # validate_gam_config is not in the tuple[bool validator set — no violation.
        assert _truthiness_violations(tree, {"validate_product_config"}) == []
