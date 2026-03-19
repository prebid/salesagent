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
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# ── Pre-existing violations ──────────────────────────────────────────────
# FIXME: replace ctx.get("env") with ctx["env"] and hasattr() with typed
# protocol checks as UC-004 harness matures. Allowlist can only shrink.

_CTX_GET_ENV_ALLOWLIST: set[tuple[str, str]] = set()

_HASATTR_ENV_ALLOWLIST: set[tuple[str, str]] = set()


def _is_step_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @given, @when, or @then."""
    step_names = {"given", "when", "then"}
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id in step_names:
                return True
        if isinstance(dec, ast.Name) and dec.id in step_names:
            return True
    return False


def _has_ctx_get_env(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function calls ctx.get("env")."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
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
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "hasattr"
            and len(node.args) >= 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "env"
        ):
            return True
    return False


def _scan_bdd_steps(check_fn, label: str) -> list[tuple[str, str]]:
    """Find step functions matching a check function.

    Returns list of (relative_path, function_name).
    """
    violations = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR.parent.parent))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_step_decorated(node):
                continue
            if check_fn(node):
                violations.append((relative, node.name))

    return violations


class TestBddNoCtxGetEnv:
    """Structural guard: step functions must use ctx["env"], not ctx.get("env").

    The harness env is guaranteed by the autouse _harness_env fixture.
    Using ctx.get("env") masks setup failures by returning None.
    """

    def test_no_new_ctx_get_env(self):
        """No step function uses ctx.get("env") outside the allowlist."""
        violations = _scan_bdd_steps(_has_ctx_get_env, 'ctx.get("env")')
        new = [(p, n) for p, n in violations if (p, n) not in _CTX_GET_ENV_ALLOWLIST]
        assert not new, (
            f'Found {len(new)} step(s) using ctx.get("env") — use ctx["env"] instead:\n'
            + "\n".join(f"  {p}:{n}" for p, n in new)
            + '\n\nThe harness env is guaranteed by the autouse fixture. Use ctx["env"].'
        )

    def test_ctx_get_env_allowlist_not_stale(self):
        """Every allowlisted entry must still exist (forces cleanup)."""
        current = set(_scan_bdd_steps(_has_ctx_get_env, 'ctx.get("env")'))
        stale = _CTX_GET_ENV_ALLOWLIST - current
        assert not stale, "Stale _CTX_GET_ENV_ALLOWLIST entries (fixed — remove):\n" + "\n".join(
            f'  ("{p}", "{n}"),' for p, n in sorted(stale)
        )


class TestBddNoHasattrEnv:
    """Structural guard: step functions must not use hasattr(env, "method").

    If the harness env doesn't support a method, the scenario should be
    xfailed at collection time — not silently degraded at step execution.
    """

    def test_no_new_hasattr_env(self):
        """No step function uses hasattr(env, ...) outside the allowlist."""
        violations = _scan_bdd_steps(_has_hasattr_env, "hasattr(env, ...)")
        new = [(p, n) for p, n in violations if (p, n) not in _HASATTR_ENV_ALLOWLIST]
        assert not new, (
            f"Found {len(new)} step(s) using hasattr(env, ...) — call directly or xfail:\n"
            + "\n".join(f"  {p}:{n}" for p, n in new)
            + "\n\nIf the env doesn't support a method, xfail the scenario. Don't silently skip."
        )

    def test_hasattr_env_allowlist_not_stale(self):
        """Every allowlisted entry must still exist (forces cleanup)."""
        current = set(_scan_bdd_steps(_has_hasattr_env, "hasattr(env, ...)"))
        stale = _HASATTR_ENV_ALLOWLIST - current
        assert not stale, "Stale _HASATTR_ENV_ALLOWLIST entries (fixed — remove):\n" + "\n".join(
            f'  ("{p}", "{n}"),' for p, n in sorted(stale)
        )
