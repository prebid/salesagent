"""Guard: `*_raw` transport wrappers must use the NOT_PROVIDED sentinel, not `None`.

salesagent-tb8c: `identity: ResolvedIdentity | None = None` cannot distinguish
"caller explicitly passed identity=None" (exercise the anonymous/no-tenant path)
from "caller omitted the argument" (resolve identity from ambient transport
context) — both look like `None` inside the function body. Every `*_raw`
wrapper (A2A/REST) used to guard with `if identity is None: identity =
resolve_identity_from_context(...)`, which silently re-resolves an explicit
`identity=None` via FastMCP's ambient `_current_http_request` ContextVar
whenever one happens to be active in the current async task — upgrading an
intentionally-anonymous caller to a real tenant.

The fix (src/core/transport_helpers.py) introduces a typed sentinel,
`NOT_PROVIDED` (an Enum member folded into the parameter's `Literal` union so
`mypy src/` accepts it as the default for a `ResolvedIdentity | None`-typed
parameter), and a shared helper, `resolve_identity_if_not_provided()`, that
only re-resolves when the sentinel is present.

This guard scans every `*_raw` function in `src/core/tools/` (the transport
boundary — see CLAUDE.md Pattern #5) for two regressions:

1. The buggy shape reappearing: `if identity is None:` directly followed by
   (or containing) a call to `resolve_identity_from_context(`. This is the
   exact re-resolution-on-None shape the fix removed; if it comes back
   (a new `*_raw` wrapper copy-pasted from an old one, or a revert), the
   ambient-context bleed bug reappears silently.
2. A `*_raw` function whose `identity` parameter defaults to `None` instead
   of the `NOT_PROVIDED` sentinel — the precondition for regression #1 even
   existing (`resolve_identity_if_not_provided` is a no-op if the sentinel is
   never the default, since `identity is NOT_PROVIDED` is never true for a
   caller-omitted argument).

`test_architecture_no_handrolled_identity_guard.py` deliberately exempts
`*_raw` wrappers (its scope is business-logic `_impl` raising-guards). This
guard is the dedicated `*_raw`-scoped complement — nothing else currently
enforces the sentinel pattern at these 15 call sites.

Allowlist is empty: all `*_raw` wrappers with an `identity` parameter must use
the sentinel default and the shared helper.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIR = REPO_ROOT / "src" / "core" / "tools"

# (relative_path, function_name) — empty: every *_raw wrapper must use the sentinel.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _is_resolve_identity_from_context_call(node: ast.AST) -> bool:
    """True if `node` is (or contains, one level of assignment) a call to
    `resolve_identity_from_context(...)`."""
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    for call in calls:
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "resolve_identity_from_context":
            return True
    return False


def _is_buggy_if_identity_is_none_reresolve(node: ast.AST) -> bool:
    """True if `node` is `if identity is None: ...resolve_identity_from_context(...)...`.

    This is the exact pre-fix shape: a bare `is None` guard (not the
    NOT_PROVIDED sentinel) that re-resolves via ambient context.
    """
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)):
        return False
    if len(test.comparators) != 1:
        return False
    comp = test.comparators[0]
    if not (isinstance(comp, ast.Constant) and comp.value is None):
        return False
    if ast.unparse(test.left) != "identity":
        return False
    return _is_resolve_identity_from_context_call(ast.Module(body=node.body, type_ignores=[]))


def _identity_param_default_is_none(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if `func` has an `identity` parameter defaulting to bare `None`."""
    args = func.args
    positional = args.posonlyargs + args.args
    all_params = positional + args.kwonlyargs
    all_defaults: list[ast.expr | None] = (
        [None] * (len(positional) - len(args.defaults)) + list(args.defaults) + list(args.kw_defaults)
    )
    for param, default in zip(all_params, all_defaults, strict=True):
        if param.arg == "identity":
            return isinstance(default, ast.Constant) and default.value is None
    return False


def _scan_file(py_file: Path) -> list[tuple[str, int, str]]:
    """Return (function_name, lineno, kind) for *_raw wrapper violations."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (OSError, SyntaxError):
        return []

    hits: list[tuple[str, int, str]] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.endswith("_raw"):
                if _identity_param_default_is_none(child):
                    hits.append((child.name, child.lineno, "identity: ... = None (must default to NOT_PROVIDED)"))
                for sub in ast.walk(child):
                    if _is_buggy_if_identity_is_none_reresolve(sub):
                        hits.append(
                            (
                                child.name,
                                sub.lineno,
                                "if identity is None: ...resolve_identity_from_context(...) "
                                "(ambient-context bleed — salesagent-tb8c)",
                            )
                        )
            walk(child)

    walk(tree)
    return hits


def _find_violations() -> list[tuple[str, str, int, str]]:
    """Return (relative_path, function, lineno, kind) across src/core/tools."""
    out: list[tuple[str, str, int, str]] = []
    for py_file in sorted(SCAN_DIR.rglob("*.py")):
        rel = str(py_file.relative_to(REPO_ROOT))
        for func, lineno, kind in _scan_file(py_file):
            out.append((rel, func, lineno, kind))
    return out


def _scan_source(source: str) -> list[tuple[str, int, str]]:
    """Like `_scan_file`, but parses `source` directly (for meta-tests)."""
    tree = ast.parse(source, filename="<snippet>")
    hits: list[tuple[str, int, str]] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.endswith("_raw"):
                if _identity_param_default_is_none(child):
                    hits.append((child.name, child.lineno, "identity: ... = None (must default to NOT_PROVIDED)"))
                for sub in ast.walk(child):
                    if _is_buggy_if_identity_is_none_reresolve(sub):
                        hits.append(
                            (
                                child.name,
                                sub.lineno,
                                "if identity is None: ...resolve_identity_from_context(...) "
                                "(ambient-context bleed — salesagent-tb8c)",
                            )
                        )
            walk(child)

    walk(tree)
    return hits


class TestNoAmbientIdentityBleed:
    """`*_raw` wrappers must default `identity` to NOT_PROVIDED, never bare None."""

    def test_raw_wrappers_use_not_provided_sentinel(self):
        found = {(rel, func) for rel, func, _lineno, _kind in _find_violations()}
        assert_violations_match_allowlist(
            found,
            KNOWN_VIOLATIONS,
            fix_hint=(
                "Fix: default identity to src.core.transport_helpers.NOT_PROVIDED "
                "(type it as IdentityOrNotProvided) and call "
                "resolve_identity_if_not_provided(identity, ctx, ...) instead of "
                "`if identity is None: identity = resolve_identity_from_context(...)`. "
                "See salesagent-tb8c."
            ),
        )

    def test_detector_catches_known_bad_snippets(self):
        """Positive meta-test: the detector must flag every known-bad shape."""
        bad_snippets = {
            "bare_none_default": (
                "async def get_products_raw(brief=None, ctx=None, identity: ResolvedIdentity | None = None):\n"
                "    return await _get_products_impl(brief=brief, identity=identity)\n"
            ),
            "if_is_none_reresolve": (
                "async def list_accounts_raw(ctx=None, identity=NOT_PROVIDED):\n"
                "    if identity is None:\n"
                "        identity = resolve_identity_from_context(ctx, require_valid_token=False)\n"
                "    return await _list_accounts_impl(identity=identity)\n"
            ),
            "attribute_call_reresolve": (
                "async def sync_accounts_raw(ctx=None, identity=NOT_PROVIDED):\n"
                "    if identity is None:\n"
                "        identity = transport_helpers.resolve_identity_from_context(ctx)\n"
                "    return await _sync_accounts_impl(identity=identity)\n"
            ),
        }
        missed = [label for label, source in bad_snippets.items() if not _scan_source(source)]
        assert not missed, "Detector missed known-bad snippet(s): " + ", ".join(missed)

    def test_detector_ignores_safe_snippets(self):
        """Negative meta-test: the detector must NOT flag the fixed pattern or unrelated code."""
        safe_snippets = {
            "sentinel_default_and_helper": (
                "async def get_products_raw(brief=None, ctx=None, identity: IdentityOrNotProvided = NOT_PROVIDED):\n"
                "    identity = resolve_identity_if_not_provided(identity, ctx)\n"
                "    return await _get_products_impl(brief=brief, identity=identity)\n"
            ),
            "not_a_raw_function": (
                "async def get_products_impl(brief=None, identity: ResolvedIdentity | None = None):\n"
                "    return _query(brief, identity)\n"
            ),
            "if_is_none_but_no_reresolve": (
                "async def list_accounts_raw(ctx=None, identity=NOT_PROVIDED):\n"
                "    if identity is None:\n"
                "        logger.warning('anonymous caller')\n"
                "    return await _list_accounts_impl(identity=identity)\n"
            ),
        }
        for label, source in safe_snippets.items():
            assert not _scan_source(source), f"False positive on known-safe snippet: {label}"
