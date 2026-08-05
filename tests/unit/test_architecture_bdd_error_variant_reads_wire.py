"""Guard: a BDD oracle must not introspect the reconstructed exception for content.

then_no_accounts_in_response / then_no_dry_run_field
used to call vars(error) / error.model_dump() / getattr(error, "dry_run", None) on
ctx["error"] (or _get_error(ctx)) — the harness-RECONSTRUCTED AdCPError. Its __dict__
has a fixed key set that can never carry an application-specific leaked field
(accounts, dry_run, ...) regardless of what production actually emits, so any
assertion built on it is structurally unfalsifiable. The fix reads
ctx["wire_error_envelope"] / ctx["synthesized_error_envelope"] instead (see
tests/CLAUDE.md's Error Verification Policy — the reconstruction is lossy by design).

This guard scans every tests/bdd/steps/**/*.py file for `vars(error)`,
`error.model_dump()`, or `error.__dict__` — the disease's exact literal shapes,
keyed on the variable name "error" (the established convention this codebase uses
for `error = ctx.get("error")` / `error = _get_error(ctx)`).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_detector_catches_ast_snippets, rel

_BDD_STEPS_ROOT = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_ERROR_VAR_NAME = "error"


def _is_error_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == _ERROR_VAR_NAME


def _find_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of vars(error)/error.model_dump()/error.__dict__."""
    violations: list[int] = []
    for node in ast.walk(tree):
        # vars(error)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars":
            if node.args and _is_error_name(node.args[0]):
                violations.append(node.lineno)
        # error.model_dump() / error.__dict__
        if isinstance(node, ast.Attribute) and _is_error_name(node.value):
            if node.attr in ("model_dump", "__dict__"):
                violations.append(node.lineno)
    return violations


def _iter_bdd_step_files() -> list[Path]:
    return sorted(p for p in _BDD_STEPS_ROOT.rglob("*.py") if "__pycache__" not in str(p))


def test_no_bdd_step_introspects_the_reconstructed_error_for_content() -> None:
    """Every real tests/bdd/steps/ file must be free of the disease's literal shapes."""
    violations: dict[str, list[int]] = {}
    for path in _iter_bdd_step_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _find_violations(tree)
        if lines:
            violations[rel(path)] = lines

    assert not violations, (
        "vars(error)/error.model_dump()/error.__dict__ (an unfalsifiable-oracle "
        "shape) found at:\n" + "\n".join(f"  {f}:{ln}" for f, lns in violations.items() for ln in lns) + "\n"
        "Read ctx['wire_error_envelope'] (or ctx['synthesized_error_envelope'] for IMPL) instead — "
        "see tests/CLAUDE.md's Error Verification Policy."
    )


def test_detector_catches_the_disease_shapes() -> None:
    """Meta-test (positive): all three literal shapes must be flagged."""
    assert_detector_catches_ast_snippets(
        _find_violations,
        snippets={
            "vars_error": ("def then_x(ctx):\n    error = ctx.get('error')\n    payload = vars(error)\n"),
            "model_dump": ("def then_x(ctx):\n    error = ctx.get('error')\n    payload = error.model_dump()\n"),
            "dunder_dict": ("def then_x(ctx):\n    error = ctx.get('error')\n    payload = error.__dict__\n"),
        },
    )


def test_detector_allows_wire_envelope_reads() -> None:
    """Meta-test (negative): reading the wire envelope must NOT be flagged."""
    source = (
        "def then_x(ctx):\n"
        "    envelope = ctx.get('wire_error_envelope') or ctx.get('synthesized_error_envelope')\n"
        "    assert 'accounts' not in envelope\n"
    )
    tree = ast.parse(source, filename="<known-good>")
    assert not _find_violations(tree)


def test_detector_ignores_unrelated_vars_calls() -> None:
    """A vars() call on something NOT named 'error' is not this disease."""
    source = "def then_x(ctx):\n    response = ctx.get('response')\n    payload = vars(response)\n"
    tree = ast.parse(source, filename="<unrelated-vars-call>")
    assert not _find_violations(tree)
