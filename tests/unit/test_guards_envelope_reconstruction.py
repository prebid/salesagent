"""Guard: _envelope_to_adcp_error must extract all wire fields including suggestion.

Regression for #1417: _envelope_to_adcp_error extracted code/message/
recovery/details from the two-layer envelope but NOT suggestion. After REST round-
trip, result.error.suggestion was None even though the wire had it.

This guard checks the function body (via AST) passes suggestion to
_adcp_error_from_code, so a future edit that drops it fails immediately.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_BASE = REPO_ROOT / "tests" / "harness" / "_base.py"
TARGET_FN = "_envelope_to_adcp_error"
CALLED_FN = "_adcp_error_from_code"
REQUIRED_KWARG = "suggestion"


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _is_empty_constant(node: ast.AST) -> bool:
    """True if the node is a literal ``None`` or empty string/collection.

    #1417: ``suggestion=None`` satisfies "the kwarg is present" but
    still drops the suggestion on the wire, so an empty literal must NOT count
    as passing the value through.
    """
    return isinstance(node, ast.Constant) and not node.value


def _calls_with_keyword(fn_node: ast.AST, callee: str, kwarg: str) -> bool:
    """Return True if the function body calls `callee` passing a NON-EMPTY `kwarg`.

    A literal ``kwarg=None`` (or empty string) does not count — it is present but
    drops the value (#1417).
    """
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fn_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if fn_name != callee:
            continue
        # Keyword: kwarg=<expr>, but reject a literal None/"" (value dropped).
        if any(kw.arg == kwarg and not _is_empty_constant(kw.value) for kw in node.keywords):
            return True
        # Positional: for _adcp_error_from_code(error_code, message, recovery, details,
        # suggestion, field), suggestion is index 4 (0-based) — accept >=5 positional
        # args unless the index-4 arg is an empty literal.
        if len(node.args) >= 5 and not _is_empty_constant(node.args[4]):
            return True
    return False


class TestEnvelopeReconstructionPassesSuggestion:
    """_envelope_to_adcp_error must pass suggestion to _adcp_error_from_code."""

    def _parse(self) -> ast.Module:
        return ast.parse(HARNESS_BASE.read_text())

    def test_target_function_exists(self):
        """_envelope_to_adcp_error must exist in tests/harness/_base.py."""
        tree = self._parse()
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None, f"{TARGET_FN} not found in {HARNESS_BASE.relative_to(REPO_ROOT)}"

    def test_suggestion_passed_to_adcp_error_from_code(self):
        """_envelope_to_adcp_error calls _adcp_error_from_code with suggestion."""
        tree = self._parse()
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None, f"{TARGET_FN} not found"
        assert _calls_with_keyword(fn, CALLED_FN, REQUIRED_KWARG), (
            f"{TARGET_FN} does not pass '{REQUIRED_KWARG}' to {CALLED_FN}. "
            "This means REST error reconstruction drops the suggestion field. "
            "#1417 regression: add suggestion extraction and pass it."
        )

    def test_guard_catches_missing_suggestion(self):
        """Negative meta-test: guard catches a call that omits suggestion."""
        source = """
def _adcp_error_from_code(code, msg, recovery=None, details=None, suggestion=None, field=None):
    pass

def _envelope_to_adcp_error(envelope):
    code = envelope.get('code')
    msg = envelope.get('message')
    recovery = envelope.get('recovery')
    details = envelope.get('details')
    return _adcp_error_from_code(code, msg, recovery, details)  # missing suggestion
"""
        tree = ast.parse(source)
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None
        result = _calls_with_keyword(fn, CALLED_FN, REQUIRED_KWARG)
        assert not result, "Negative meta-test: guard should detect missing suggestion"

    def test_guard_catches_literal_none_suggestion(self):
        """Negative meta-test (#1417): a literal suggestion=None is not a pass-through."""
        source = """
def _adcp_error_from_code(code, msg, recovery=None, details=None, suggestion=None, field=None):
    pass

def _envelope_to_adcp_error(envelope):
    return _adcp_error_from_code('CODE', 'msg', recovery='terminal', details=None, suggestion=None)
"""
        tree = ast.parse(source)
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None
        result = _calls_with_keyword(fn, CALLED_FN, REQUIRED_KWARG)
        assert not result, "Guard should reject suggestion=None (value dropped, not passed through)"

    def test_guard_passes_with_keyword_arg(self):
        """Positive meta-test: guard accepts a call that passes suggestion as keyword."""
        source = """
def _adcp_error_from_code(code, msg, recovery=None, details=None, suggestion=None, field=None):
    pass

def _envelope_to_adcp_error(envelope):
    suggestion = envelope.get('suggestion')
    return _adcp_error_from_code('CODE', 'msg', recovery='terminal', details=None, suggestion=suggestion)
"""
        tree = ast.parse(source)
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None
        result = _calls_with_keyword(fn, CALLED_FN, REQUIRED_KWARG)
        assert result, "Positive meta-test: guard should accept suggestion keyword arg"

    def test_guard_passes_with_positional_arg(self):
        """Positive meta-test: guard accepts a call that passes suggestion as positional."""
        source = """
def _adcp_error_from_code(code, msg, recovery=None, details=None, suggestion=None, field=None):
    pass

def _envelope_to_adcp_error(envelope):
    suggestion = envelope.get('suggestion')
    return _adcp_error_from_code('CODE', 'msg', None, None, suggestion)  # positional at index 4
"""
        tree = ast.parse(source)
        fn = _find_function(tree, TARGET_FN)
        assert fn is not None
        result = _calls_with_keyword(fn, CALLED_FN, REQUIRED_KWARG)
        assert result, "Positive meta-test: guard should accept suggestion as positional arg"
