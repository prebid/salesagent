"""Guard: normalize_agent_url must delegate authority validation to the signing layer.

#1291. ``src.core.validation.normalize_agent_url`` hand-rolled its own
URL normalization and never checked whether the authority was malformed per
RFC 9421's canonicalization rules -- a URL the signing verifier
(``src.core.signing.canonical``) would reject could still be silently
accepted, normalized, and used for agent-registration comparison. The fix
makes ``normalize_agent_url`` call the signing layer's
``reject_malformed_target`` before its own suffix-stripping logic. This
structural guard pins that delegation so a future edit cannot silently drop it
back to hand-rolled (re-derived) authority logic.

A whole-tree AST guard for "hand-rolled netloc/urlparse authority parsing" is
intentionally NOT used: the codebase-wide disease scan (in the bead) found 13
other urlparse/netloc/hostname call sites serving genuinely different purposes
(admin-form validation, SSRF/open-redirect guards, DB DSN parsing, GAM/Slack
integration URLs) -- a tree-wide checker would need to permanently allowlist
all 13 just to exist, which is disproportionate for a bug whose disposition
scan confirmed exactly one live instance. The targeted pin below is precise
instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATION_MODULE = REPO_ROOT / "src" / "core" / "validation.py"
FUNCTION_NAME = "normalize_agent_url"
REQUIRED_CALLEE = "reject_malformed_target"

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def function_delegates_to_signing_gate(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff *func*'s body calls ``reject_malformed_target`` anywhere.

    Shared predicate so the real-source test and the positive/negative
    meta-tests exercise the exact same logic.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
            if name == REQUIRED_CALLEE:
                return True
    return False


def _find_function(module_path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(module_path.read_text(), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, FuncDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module_path} — did it move?")


def test_normalize_agent_url_delegates_to_signing_gate():
    """normalize_agent_url must call the signing layer's malformed-authority gate."""
    func = _find_function(VALIDATION_MODULE, FUNCTION_NAME)
    assert function_delegates_to_signing_gate(func), (
        f"{FUNCTION_NAME} no longer calls {REQUIRED_CALLEE}() -- it would silently accept a URL "
        "the RFC 9421 signing verifier rejects as malformed (#1291). Re-add the "
        "`from src.core.signing import reject_malformed_target` delegation before any hand-rolled "
        "normalization."
    )


# -- Meta-tests: prove the predicate catches the disease and accepts the cure --

_GUARDED_SAMPLE = """
def normalize_agent_url(url):
    if not url:
        return url
    reject_malformed_target(url)
    normalized = url.rstrip("/")
    return normalized
"""

_UNGUARDED_SAMPLE = """
def normalize_agent_url(url):
    if not url:
        return url
    normalized = url.rstrip("/")
    return normalized
"""

_FACADE_CALL_SAMPLE = """
def normalize_agent_url(url):
    if not url:
        return url
    signing.reject_malformed_target(url)
    normalized = url.rstrip("/")
    return normalized
"""


def _parse_single_func(src: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))


def test_meta_guard_accepts_delegating_function():
    """Positive: a function that calls reject_malformed_target passes."""
    assert function_delegates_to_signing_gate(_parse_single_func(_GUARDED_SAMPLE))


def test_meta_guard_accepts_attribute_call_form():
    """Positive: calling it as `module.reject_malformed_target(...)` also counts."""
    assert function_delegates_to_signing_gate(_parse_single_func(_FACADE_CALL_SAMPLE))


def test_meta_guard_rejects_hand_rolled_function():
    """Negative: a function with no call to the gate is caught."""
    assert not function_delegates_to_signing_gate(_parse_single_func(_UNGUARDED_SAMPLE))
