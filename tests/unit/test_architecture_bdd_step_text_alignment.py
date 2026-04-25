"""Guard: concrete BDD step text must align with the fields asserted in code.

These checks target a recurring class of false-positive BDD steps where the
step text promises validation for one field, but the body inspects a different
field instead.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_INSPECT_SCRIPT = Path(__file__).resolve().parents[2] / ".claude" / "scripts" / "inspect_bdd_steps.py"


def _load_extract_bdd_steps():
    """Load the shared BDD inspection script and return extract_bdd_steps()."""
    spec = importlib.util.spec_from_file_location("inspect_bdd_steps", _INSPECT_SCRIPT)
    assert spec is not None and spec.loader is not None, f"Could not load {_INSPECT_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["inspect_bdd_steps"] = module
    spec.loader.exec_module(module)
    return module.extract_bdd_steps


def _iter_then_steps() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Yield Then step nodes plus their extracted step text."""
    extract_bdd_steps = _load_extract_bdd_steps()

    text_by_location: dict[tuple[str, int], str] = {}
    for step in extract_bdd_steps(_BDD_STEPS_DIR):
        if step.step_type == "then":
            text_by_location[(str(Path(step.file_path).resolve()), step.line_number)] = step.step_text

    results = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        resolved = str(py_file.resolve())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            step_text = text_by_location.get((resolved, node.lineno))
            if step_text is not None:
                results.append((py_file, node, step_text))
    return results


def _field_names_referenced(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect likely field names referenced in a function body."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


class TestBddStepTextAlignment:
    """Structural guard: literal field names in Then steps must be referenced in code."""

    def test_account_id_steps_reference_account_id(self):
        """Then steps mentioning account_id must inspect account_id somewhere in the body."""
        violations = []
        for py_file, func, step_text in _iter_then_steps():
            if "account_id" not in step_text:
                continue
            referenced = _field_names_referenced(func)
            if "account_id" not in referenced:
                violations.append(
                    f"{py_file.relative_to(Path.cwd())}:{func.lineno} {func.name} — step mentions account_id"
                )

        assert not violations, (
            f"Found {len(violations)} Then step(s) mentioning account_id without referencing it in code:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_literal_response_field_steps_reference_the_named_field(self):
        """Then steps about literal response fields must reference those field names in code."""
        violations = []
        pattern = re.compile(r'the response should (?:not )?contain "([^"{}/]+)" field')
        for py_file, func, step_text in _iter_then_steps():
            match = pattern.search(step_text)
            if match is None:
                continue
            field_name = match.group(1)
            referenced = _field_names_referenced(func)
            if field_name not in referenced:
                violations.append(
                    f"{py_file.relative_to(Path.cwd())}:{func.lineno} {func.name} — step claims response field '{field_name}'"
                )

        assert not violations, (
            f"Found {len(violations)} response-field Then step(s) that do not reference the named field:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
