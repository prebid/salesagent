"""Guard: no hand-rolled ValidationError→AdCPValidationError translation in src/.

Regression for salesagent-ah98: tools translated pydantic ``ValidationError``
by hand — ``raise AdCPValidationError(format_validation_error(e, ...))`` —
which dropped the error.json top-level ``suggestion`` (and ``field``) that the
shared boundary attaches. The ONE sanctioned translation point is
``adcp_validation_boundary`` (src/core/validation_helpers.py); this guard
AST-scans ``src/`` and fails on any ``AdCPValidationError(...)`` construction
whose arguments contain a ``format_validation_error(...)`` call — the
hand-rolled boundary signature — outside validation_helpers.py itself.

Non-raise uses of ``format_validation_error`` (message reconstruction in
media_buy_create / creatives sync) are fine and deliberately not flagged —
only the exception-constructor pattern is the disease. Ships with ZERO
violations; no allowlist (repo hard rule: allowlists never grow).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src"
SANCTIONED = SCAN_ROOT / "core" / "validation_helpers.py"


def _validation_error_class_names() -> frozenset[str]:
    """AdCPValidationError and every subclass — hand-rolling the boundary with a
    SUBCLASS (e.g. AdCPInvalidRequestError) is the same disease, and a matcher
    keyed on the one literal name would miss it."""
    import src.core.exceptions as exceptions_module
    from src.core.exceptions import AdCPValidationError

    return frozenset(
        name
        for name in dir(exceptions_module)
        if isinstance(getattr(exceptions_module, name), type)
        and issubclass(getattr(exceptions_module, name), AdCPValidationError)
    )


VALIDATION_ERROR_NAMES = _validation_error_class_names()


def _contains_format_validation_error_call(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "format_validation_error":
                return True
    return False


def find_handrolled_boundaries(tree: ast.AST) -> list[str]:
    """Unparsed source for AdCPValidationError(-subclass)(...) wrapping format_validation_error(...)."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name not in VALIDATION_ERROR_NAMES:
            continue
        args_and_kwargs: list[ast.AST] = [*node.args, *[kw.value for kw in node.keywords]]
        if any(_contains_format_validation_error_call(a) for a in args_and_kwargs):
            offenders.append(ast.unparse(node))
    return offenders


def test_no_handrolled_validation_boundary_in_src():
    violations: list[str] = []
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if path == SANCTIONED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for offender in find_handrolled_boundaries(tree):
            violations.append(f"{path.relative_to(REPO_ROOT)}: {offender}")
    assert not violations, (
        "Hand-rolled ValidationError translation — use `with adcp_validation_boundary"
        "(context=...)` (src/core/validation_helpers.py) so the rejection carries the "
        "error.json top-level suggestion and field (salesagent-ah98). Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[str]:
    return find_handrolled_boundaries(ast.parse(snippet))


class TestGuardDetector:
    def test_positive_bare_message_arg(self):
        assert _detect('raise AdCPValidationError(format_validation_error(e, context="x request")) from e')

    def test_positive_with_suggestion_kwarg_still_handrolled(self):
        # Passing suggestion= by hand is STILL the disease (duplicates the
        # boundary, drops field=) — a detector keying on "no suggestion kwarg"
        # would miss it.
        assert _detect(
            "raise AdCPValidationError(format_validation_error(e), suggestion=suggest_validation_fix(e)) from e"
        )

    def test_positive_subclass_still_handrolled(self):
        # A SUBCLASS wrapping the formatter is the same disease — a matcher
        # keyed on the literal 'AdCPValidationError' name would miss it.
        assert "AdCPInvalidRequestError" in VALIDATION_ERROR_NAMES
        assert _detect('raise AdCPInvalidRequestError(format_validation_error(e, context="x request")) from e')

    def test_negative_semantic_error_without_formatter(self):
        assert not _detect('raise AdCPValidationError("Start date must be before end date", suggestion="fix it")')

    def test_negative_unrelated_error_class_with_formatter(self):
        # Non-validation AdCP errors legitimately embed formatter output in
        # messages (e.g. reconstruction logging) — only the validation family
        # constitutes a hand-rolled boundary.
        assert not _detect('raise AdCPAdapterError(f"reconstruct failed: {format_validation_error(ve)}")')

    def test_negative_non_raise_message_reconstruction(self):
        assert not _detect("msg = f'Failed to reconstruct: {format_validation_error(ve)}'")
