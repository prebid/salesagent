"""Guard: BDD Then step functions must not have empty or no-op bodies.

A Then step that claims to verify behavior must actually assert something.
This guard catches three failure modes:

1. **Empty body**: ``pass``, ellipsis, or docstring-only
2. **No code**: no assert, call, or raise at all
3. **No-op delegation**: body has zero ``assert`` statements and only delegates
   to non-assertion helpers (like ``_pending(ctx, step)``). This catches any
   LLM-invented "placeholder" helper — not by name, but by the structural
   pattern: a Then step that calls something but never asserts.

The no-op delegation check works by requiring that if a Then step has no
``assert`` statements, it must call at least one function whose name starts
with an assertion-like prefix (``assert_``, ``_assert_``, ``check_``,
``_check_``, ``verify_``, ``_verify_``), or calls ``pytest.skip``/``xfail``/
``fail``, or calls an ``env.*`` method. Everything else (``_pending``,
``_tbd``, ``_not_implemented``, or whatever creative name an LLM invents)
does not count as "doing something".

beads: beads-5rt
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Prefixes that indicate a function is assertion-like
_ASSERTION_PREFIXES = ("assert_", "_assert_", "check_", "_check_", "verify_", "_verify_")

# Pre-existing violations: (relative_path, function_name)
# These Then steps delegate to _pending() which asserts nothing.
# FIXME: wire these steps to real harness assertions as UC-004 harness matures.
# Allowlist can only shrink — never add new entries.
_NOOP_DELEGATION_ALLOWLIST: set[tuple[str, str]] = set()


def _is_then_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @then(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id == "then":
                return True
        if isinstance(dec, ast.Name) and dec.id == "then":
            return True
    return False


def _body_is_empty(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body is effectively empty (pass, ellipsis, or docstring-only)."""
    stmts = func.body
    effective = []
    for i, stmt in enumerate(stmts):
        if (
            i == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue  # skip docstring
        effective.append(stmt)

    if not effective:
        return True
    if len(effective) == 1:
        s = effective[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
            return True
    return False


def _body_has_assert_or_meaningful_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body contains an assert, raise, or meaningful call.

    A "meaningful call" is one that is likely to assert something:
    - Function with assertion-like name (assert_*, _assert_*, check_*, etc.)
    - pytest.skip / pytest.xfail / pytest.fail
    - env.* (harness method)

    Calls to generic helpers (_pending, _tbd, etc.) do NOT count.
    If the body has at least one ``assert`` statement, all calls are accepted
    (the step does its own verification).
    """
    has_assert = False
    has_meaningful_call = False
    has_any_call = False

    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            has_assert = True
        if isinstance(node, ast.Raise):
            return True  # explicit failure is always meaningful

        if isinstance(node, ast.Call):
            fn = node.func

            # Skip ctx.* calls (data access, not assertions)
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "ctx":
                continue
            # Skip builtins
            if isinstance(fn, ast.Name) and fn.id in (
                "getattr",
                "str",
                "type",
                "len",
                "list",
                "dict",
                "set",
                "print",
                "int",
                "float",
                "bool",
                "tuple",
                "range",
                "enumerate",
                "zip",
                "sorted",
                "reversed",
                "any",
                "all",
            ):
                continue

            has_any_call = True

            # Assertion-like function name
            if isinstance(fn, ast.Name) and fn.id.startswith(_ASSERTION_PREFIXES):
                has_meaningful_call = True
            # Method on env (harness call)
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "env":
                has_meaningful_call = True
            # pytest.skip/xfail/fail
            elif (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "pytest"
                and fn.attr in ("skip", "xfail", "fail")
            ):
                has_meaningful_call = True

    # If body has assert statements, any delegation is fine
    if has_assert:
        return True
    # If body has a meaningful call (assertion helper, harness, pytest), OK
    if has_meaningful_call:
        return True
    # If body has calls but none are meaningful and no asserts — no-op delegation
    if has_any_call:
        return False
    # No asserts, no calls at all
    return False


def _scan_bdd_steps() -> list[tuple[str, str, str]]:
    """Find Then steps with empty, code-free, or no-op bodies.

    Returns list of (relative_path, function_name, reason).
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
            if not _is_then_decorated(node):
                continue
            if _body_is_empty(node):
                violations.append((relative, node.name, "empty body (pass/docstring-only)"))
            elif not _body_has_assert_or_meaningful_call(node):
                violations.append((relative, node.name, "no-op delegation (calls helper but never asserts)"))

    return violations


class TestBddNoPassSteps:
    """Structural guard: Then steps must assert something."""

    def test_no_empty_then_steps(self):
        """Every @then step must contain an assert, meaningful call, or raise."""
        violations = _scan_bdd_steps()
        new_violations = [
            (path, name, reason) for path, name, reason in violations if (path, name) not in _NOOP_DELEGATION_ALLOWLIST
        ]
        assert not new_violations, (
            f"Found {len(new_violations)} Then step(s) with empty/no-op bodies:\n"
            + "\n".join(f"  {path}:{name} — {reason}" for path, name, reason in new_violations)
            + "\n\nEach @then step must either:"
            + "\n  - Contain at least one `assert` statement with a comparison"
            + "\n  - Delegate to an assertion helper (_assert_*, check_*, verify_*)"
            + "\n  - Call pytest.skip/xfail/fail"
            + "\n  - NOT delegate to a no-op helper like _pending()"
        )

    def test_noop_allowlist_entries_still_exist(self):
        """Every allowlisted no-op delegation must still exist in source.

        When a Then step is fixed (real assertions added), remove it from
        the allowlist. This test fails if an entry is stale.
        """
        violations = _scan_bdd_steps()
        current = {(path, name) for path, name, _ in violations}
        stale = _NOOP_DELEGATION_ALLOWLIST - current
        assert not stale, (
            "Stale allowlist entries (violations were fixed — remove from _NOOP_DELEGATION_ALLOWLIST):\n"
            + "\n".join(f'  ("{path}", "{name}"),' for path, name in sorted(stale))
        )
