"""Guard: auth/require helper signatures stay consistent.

Two conventions, both about the "centralize the guard, raise the typed error"
helper families:

1. **auth.py guard helpers echo request context.** Every ``require_*`` /
   ``*_or_raise`` helper in ``src/core/auth.py`` raises a typed auth
   ``AdCPError`` when identity/principal/tenant resolution fails. Each accepts a
   keyword-only ``context=`` parameter so callers can pass ``req.context`` and the
   buyer agent can correlate the failure to its request. A new helper that omits
   ``context=`` (or declares it positionally) regresses the convention.

   This guards the SIGNATURE only — not whether every call site passes
   ``context=`` (that is a non-guardable convention, like call order: some helpers
   are called where no request context is in scope).

2. **Adapter ``_require_*`` accessors declare their return type.** Helpers like
   ``_require_config`` / ``_require_creatives_manager`` return a value with ``None``
   stripped (they raise ``AdCPConfigurationError`` when it is absent), so callers
   can rebind to narrow the type. The return annotation is what makes that
   narrowing real. mypy does not enforce it here (``disallow_untyped_defs = False``
   project-wide), so this guard is the only thing that keeps the contract.

Both allowlists are empty: every helper conforms. A new violation fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._ast_helpers import iter_module_trees

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_MODULE = REPO_ROOT / "src" / "core" / "auth.py"
ADAPTERS_DIR = REPO_ROOT / "src" / "adapters"

# Allowlists must only shrink, never grow. Keyed by (relative_path, function_name).
AUTH_KNOWN_VIOLATIONS: set[tuple[str, str]] = set()
ADAPTER_KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_auth_guard_helper(name: str) -> bool:
    """True for the auth.py raise-guard helper family (``require_*`` / ``*_or_raise``)."""
    return name.startswith("require_") or name.endswith("_or_raise")


def _has_keyword_only_param(node: ast.FunctionDef | ast.AsyncFunctionDef, param: str) -> bool:
    return any(arg.arg == param for arg in node.args.kwonlyargs)


def _iter_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, FuncDef):
            yield node


def _find_auth_helpers_missing_context() -> list[tuple[str, str]]:
    tree = ast.parse(AUTH_MODULE.read_text(), filename=str(AUTH_MODULE))
    rel = str(AUTH_MODULE.relative_to(REPO_ROOT))
    return [
        (rel, node.name)
        for node in _iter_functions(tree)
        if _is_auth_guard_helper(node.name) and not _has_keyword_only_param(node, "context")
    ]


def _find_adapter_require_helpers_missing_return() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for tree, rel_path in iter_module_trees([ADAPTERS_DIR]):
        for node in _iter_functions(tree):
            if node.name.startswith("_require_") and node.returns is None:
                out.append((rel_path, node.name))
    return out


def test_auth_guard_helpers_accept_keyword_context():
    """Every auth.py require_*/*_or_raise helper declares a keyword-only context= param."""
    violations = [v for v in _find_auth_helpers_missing_context() if v not in AUTH_KNOWN_VIOLATIONS]
    assert not violations, (
        f"Found {len(violations)} auth guard helper(s) without a keyword-only `context=` parameter.\n"
        "These helpers raise a typed auth AdCPError; `context=` lets callers echo `req.context` into the "
        'error envelope so buyers can correlate the failure. Add `*, context: "ContextObject | dict[str, Any] '
        '| None" = None` and pass it to the raise:\n\n' + "\n".join(f"  {rel} :: {name}()" for rel, name in violations)
    )


def test_adapter_require_helpers_have_return_annotation():
    """Every adapter _require_* accessor declares a return type (the narrow-and-raise contract)."""
    violations = [v for v in _find_adapter_require_helpers_missing_return() if v not in ADAPTER_KNOWN_VIOLATIONS]
    assert not violations, (
        f"Found {len(violations)} adapter `_require_*` helper(s) without a return annotation.\n"
        "These accessors raise when the value is absent and return it otherwise; the return annotation is what "
        "lets callers narrow the type. mypy does not enforce this project-wide, so annotate explicitly:\n\n"
        + "\n".join(f"  {rel} :: {name}()" for rel, name in violations)
    )


def test_auth_known_violations_not_stale():
    """Every allowlisted auth helper must still be missing context= (else remove it)."""
    actual = set(_find_auth_helpers_missing_context())
    stale = AUTH_KNOWN_VIOLATIONS - actual
    assert not stale, "Stale auth allowlist entries (now fixed):\n" + "\n".join(
        f"  {rel} :: {name}" for rel, name in sorted(stale)
    )


def test_adapter_known_violations_not_stale():
    """Every allowlisted adapter helper must still lack a return annotation (else remove it)."""
    actual = set(_find_adapter_require_helpers_missing_return())
    stale = ADAPTER_KNOWN_VIOLATIONS - actual
    assert not stale, "Stale adapter allowlist entries (now fixed):\n" + "\n".join(
        f"  {rel} :: {name}" for rel, name in sorted(stale)
    )
