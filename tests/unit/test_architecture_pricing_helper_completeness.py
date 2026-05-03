"""Guard: pricing accessor helpers must inspect both v2 and v3 field names.

Issue #1246 was caused by ``pricing_option_has_rate`` (now renamed
``pricing_option_is_priced``) checking only the v2 field name ``rate`` after
the AdCP 3.0 library migration silently switched the production wire shape to
v3 names (``fixed_price`` / ``floor_price``). The bug shipped because no
structural check enforced that pricing accessor helpers stay in lockstep with
the spec's rename matrix.

This guard scans every function in ``src/core/`` whose name starts with
``pricing_option_`` (the codebase's accessor-helper naming convention) plus
every function in ``src/core/helpers/pricing_helpers.py`` (the dedicated
helper module). For each function body, it walks the AST and collects every
string-literal constant. If the body mentions a v2 field name but does NOT
mention any v3 partner field, the guard fires.

This catches the **class** of bug, not just the instance: any future helper
that introspects ``rate`` or ``is_fixed`` without also recognizing the v3
field set will fail the build at ``make quality`` time.

Allowlisted exceptions (e.g., the v2-compat dump path that intentionally
*writes* v2 names without reading v3) must cite a FIXME and shrink over time.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_CORE = ROOT / "src" / "core"
PRICING_HELPERS_FILE = "src/core/helpers/pricing_helpers.py"

# Field-rename pairs: if a function body contains any string in `v2_fields`,
# it MUST also contain at least one string in `v3_fields`. The directionality
# matters — v3 → v2 backward-compat writers are allowed via the ALLOWLIST below
# (they read v3 and write v2 by design).
REQUIRED_FIELD_PAIRS: list[tuple[frozenset[str], frozenset[str]]] = [
    (frozenset({"rate"}), frozenset({"fixed_price", "floor_price"})),
    (frozenset({"is_fixed"}), frozenset({"fixed_price", "floor_price"})),
]

# Functions allowed to mention v2 field names without v3 equivalents.
# Each entry must cite WHY in a comment alongside it.
# This list shrinks over time; never add new entries to dodge a real fix.
ALLOWLIST: set[tuple[str, str]] = set()


def _is_in_scope(rel_path: str, fn_name: str) -> bool:
    """Return True for functions this guard should police."""
    if rel_path == PRICING_HELPERS_FILE:
        return True
    return fn_name.startswith("pricing_option_")


def _string_constants_in_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect every string-literal constant in a function body.

    Skips the docstring (first statement if it's a string Expr) so that
    documentation mentioning a v2 field name doesn't mask a missing v3 check.
    """
    constants: set[str] = set()
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop docstring
    for node in body:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                constants.add(child.value)
    return constants


def _iter_in_scope_functions():
    """Yield (rel_path, function_node) for every function this guard polices."""
    for py_file in sorted(SRC_CORE.rglob("*.py")):
        rel_path = str(py_file.relative_to(ROOT))
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_func and _is_in_scope(rel_path, node.name):
                yield rel_path, node


def _scan_for_violations() -> list[tuple[str, str, int, frozenset[str], frozenset[str]]]:
    """Return (rel_path, fn_name, lineno, missing_v2_set, missing_v3_set) for each violator."""
    violations: list[tuple[str, str, int, frozenset[str], frozenset[str]]] = []
    for rel_path, node in _iter_in_scope_functions():
        constants = _string_constants_in_function(node)
        for v2_fields, v3_fields in REQUIRED_FIELD_PAIRS:
            has_v2 = bool(constants & v2_fields)
            has_v3 = bool(constants & v3_fields)
            if has_v2 and not has_v3:
                violations.append((rel_path, node.name, node.lineno, v2_fields, v3_fields))
    return violations


class TestPricingHelperCompleteness:
    """Pricing accessor helpers stay in lockstep with the v2/v3 rename matrix."""

    def test_no_v2_only_pricing_accessors(self) -> None:
        """No pricing_option_* helper checks v2 field names without v3 partners."""
        unallowed = [
            (rel, fn, ln, v2, v3) for rel, fn, ln, v2, v3 in _scan_for_violations() if (rel, fn) not in ALLOWLIST
        ]
        assert not unallowed, (
            "Pricing accessor helpers checking only v2 field names — must also recognize v3:\n"
            + "\n".join(
                f"  {rel}:{ln} {fn}() — has {sorted(v2)} but missing all of {sorted(v3)}"
                for rel, fn, ln, v2, v3 in unallowed
            )
            + "\n\nFix: extend the helper to recognize the v3 field name(s), or add to "
            "ALLOWLIST with a FIXME comment if the function is genuinely v2-only "
            "(e.g., a v2-compat dump writer)."
        )

    def test_allowlist_entries_still_violate(self) -> None:
        """Stale-entry detection: every allowlisted entry must currently be a violation.

        When someone fixes an allowlisted helper, this test reminds them to
        remove the entry from ALLOWLIST.
        """
        violators = {(rel, fn) for rel, fn, _, _, _ in _scan_for_violations()}
        stale = ALLOWLIST - violators
        assert not stale, (
            f"Found {len(stale)} allowlisted entries that are no longer violations:\n"
            + "\n".join(f"  {rel}::{fn}" for rel, fn in sorted(stale))
            + "\n\nFix: remove these from ALLOWLIST."
        )

    def test_helper_module_not_empty(self) -> None:
        """Sanity: the guard is actually scanning pricing_option_is_priced.

        If the file is renamed, deleted, or the scan misses the helper module,
        this test catches the silent regression.
        """
        helper_file = ROOT / PRICING_HELPERS_FILE
        assert helper_file.exists(), f"Expected {PRICING_HELPERS_FILE} to exist"
        tree = ast.parse(helper_file.read_text())
        funcs = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert "pricing_option_is_priced" in funcs, (
            f"{PRICING_HELPERS_FILE} must define pricing_option_is_priced — "
            "the guard is meaningless if it's not scanning the right function."
        )


class TestGuardIsCorrectlyScoped:
    """Sanity tests that the guard does NOT flag legitimate v2-only writers.

    The DB layer (``src/core/database/product_pricing.py``) and ORM model
    column readers legitimately use v2 names because the database column is
    literally ``rate``. The guard's scope predicate (``_is_in_scope``) excludes
    these by design — neither lives in pricing_helpers.py nor uses the
    ``pricing_option_*`` naming convention.
    """

    def test_database_product_pricing_not_flagged(self) -> None:
        """The DB-layer accessor at src/core/database/product_pricing.py is correctly excluded.

        It legitimately reads/writes the ``rate`` column (which is the v2 name
        but the actual column name) and does NOT need to mention v3 fields.
        """
        for rel, _, _, _, _ in _scan_for_violations():
            assert rel != "src/core/database/product_pricing.py", (
                "Guard scope is too broad — database/product_pricing.py is the v2 ORM "
                "column accessor and is correctly outside this guard's policing surface."
            )
