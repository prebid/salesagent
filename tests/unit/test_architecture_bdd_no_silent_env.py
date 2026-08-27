"""Guard: BDD step functions must not silently degrade when env is absent.

Two anti-patterns that create phantom coverage:

1. **``ctx.get("env")``** — returns ``None`` when the harness fixture is missing
   instead of raising ``KeyError``. Steps that use ``ctx.get("env")`` combined
   with ``if env:`` or ``hasattr(env, ...)`` silently become no-ops.
   Canonical pattern: ``ctx["env"]`` (guaranteed by autouse ``_harness_env``).

2. **``hasattr(env, "method")``** — probes the harness at runtime instead of
   relying on typed protocols. If the env lacks a method, the step silently
   skips its work. The correct fix is xfail at the scenario level, not silent
   degradation at the step level.

Both patterns violate the "No Quiet Failures" principle from CLAUDE.md.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions
from tests.unit._bdd_guard_helpers import iter_bdd_steps

# ── Pre-existing violations ──────────────────────────────────────────────
# FIXME: replace ctx.get("env") with ctx["env"] and hasattr() with typed
# protocol checks as UC-004 harness matures. Allowlist can only shrink.

_CTX_GET_ENV_ALLOWLIST: set[tuple[str, str]] = set()

_HASATTR_ENV_ALLOWLIST: set[tuple[str, str]] = set()


def _has_ctx_get_env(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function calls ctx.get("env")."""
    for node in iter_call_expressions(func, name="get"):
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ctx"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "env"
        ):
            return True
    return False


def _has_hasattr_env(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function calls hasattr(env, ...)."""
    for node in iter_call_expressions(func, name="hasattr"):
        if len(node.args) >= 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "env":
            return True
    return False


def _scan_bdd_steps(check_fn) -> list[tuple[str, str]]:
    """Find step functions matching a check function.

    Returns list of (relative_path, function_name).
    """
    return [(step.relative, step.name) for step in iter_bdd_steps() if check_fn(step.node)]


class TestBddNoCtxGetEnv:
    """Structural guard: step functions must use ctx["env"], not ctx.get("env").

    The harness env is guaranteed by the autouse _harness_env fixture.
    Using ctx.get("env") masks setup failures by returning None.
    """

    @pytest.mark.arch_guard
    def test_no_new_ctx_get_env(self):
        """No step function uses ctx.get("env") outside the allowlist."""
        violations = _scan_bdd_steps(_has_ctx_get_env)
        new = [(p, n) for p, n in violations if (p, n) not in _CTX_GET_ENV_ALLOWLIST]
        assert not new, (
            f'Found {len(new)} step(s) using ctx.get("env") — use ctx["env"] instead:\n'
            + "\n".join(f"  {p}:{n}" for p, n in new)
            + '\n\nThe harness env is guaranteed by the autouse fixture. Use ctx["env"].'
        )

    @pytest.mark.arch_guard
    def test_ctx_get_env_allowlist_not_stale(self):
        """Every allowlisted entry must still exist (forces cleanup)."""
        current = set(_scan_bdd_steps(_has_ctx_get_env))
        assert_violations_match_allowlist(
            current & _CTX_GET_ENV_ALLOWLIST,
            _CTX_GET_ENV_ALLOWLIST,
            fix_hint="Remove fixed entries from _CTX_GET_ENV_ALLOWLIST.",
        )


class TestBddNoHasattrEnv:
    """Structural guard: step functions must not use hasattr(env, "method").

    If the harness env doesn't support a method, the scenario should be
    xfailed at collection time — not silently degraded at step execution.
    """

    @pytest.mark.arch_guard
    def test_no_new_hasattr_env(self):
        """No step function uses hasattr(env, ...) outside the allowlist."""
        violations = _scan_bdd_steps(_has_hasattr_env)
        new = [(p, n) for p, n in violations if (p, n) not in _HASATTR_ENV_ALLOWLIST]
        assert not new, (
            f"Found {len(new)} step(s) using hasattr(env, ...) — call directly or xfail:\n"
            + "\n".join(f"  {p}:{n}" for p, n in new)
            + "\n\nIf the env doesn't support a method, xfail the scenario. Don't silently skip."
        )

    @pytest.mark.arch_guard
    def test_hasattr_env_allowlist_not_stale(self):
        """Every allowlisted entry must still exist (forces cleanup)."""
        current = set(_scan_bdd_steps(_has_hasattr_env))
        assert_violations_match_allowlist(
            current & _HASATTR_ENV_ALLOWLIST,
            _HASATTR_ENV_ALLOWLIST,
            fix_hint="Remove fixed entries from _HASATTR_ENV_ALLOWLIST.",
        )
