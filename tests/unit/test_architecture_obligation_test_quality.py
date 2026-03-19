"""Guard: Obligation-tagged tests must CALL production code, not just import it.

A test with ``Covers: <obligation-id>`` claims to verify a behavioral contract.
If the test body never **calls** production code from ``src.*``, it is a sham —
it inflates coverage metrics without providing assurance.

**Rule (Level 2 — "must call")**: Every non-xfail test function whose docstring
contains ``Covers: <id>`` must contain an ``ast.Call`` node that invokes a name
imported from ``src.*``, ``tests.harness.*``, ``tests.helpers.*``, or
``tests.factories.*`` — or a helper that transitively does so.

This is strictly stronger than the previous "must reference" check. A test that
does ``from src.core.tools.products import _get_products_impl  # noqa: F401``
without ever calling the function will now be flagged.

**xfail exemption**: Tests with ``pytest.mark.xfail`` or ``_XFAIL*`` decorators
are stubs for unimplemented features. They must still import from ``src.*`` in
the body (showing intent), but don't need to call the function (it may not exist).

Scanning approach: AST — parse each test file, find ``Covers:`` in docstrings,
then check for ``ast.Call`` nodes targeting production names.

beads: salesagent-9q5g
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_OBLIGATION_ID_RE = re.compile(r"[A-Z][A-Z0-9]+-[\w-]+-\d{2}")
_COVERS_RE = re.compile(r"Covers:\s+([\w-]+)")

# Directories and files to scan (same scope as obligation_coverage guard)
_TEST_ROOT = Path(__file__).resolve().parents[1]
_UNIT_DIR = _TEST_ROOT / "unit"
_INTEGRATION_DIR = _TEST_ROOT / "integration"
# integration_v2 merged into integration — no separate directory

_ALLOWLIST_FILE = Path(__file__).resolve().parent / "obligation_test_quality_allowlist.json"

# Files that carry Covers: tags (must match obligation_coverage guard)
_UNIT_ENTITY_FILES = [
    "test_media_buy.py",
    "test_creative.py",
    "test_delivery.py",
    "test_product.py",
    "test_product_schema_obligations.py",
    "test_property_list_schema.py",
    "test_quiet_failure_propagation.py",
    "test_get_products_impl_coverage.py",
]

# Former integration_v2 files with Covers: tags (merged into integration/)
_INTEGRATION_FORMER_V2_FILES = [
    "test_creative_formats_aggregation.py",
    "test_creative_formats_catalog.py",
    "test_creative_formats_discovery.py",
    "test_creative_formats_id_filters.py",
    "test_creative_formats_mcp_filters.py",
    "test_creative_formats_ordering.py",
    "test_creative_formats_pagination.py",
    "test_creative_formats_protocol.py",
    "test_creative_formats_validation_a.py",
    "test_creative_formats_validation_b.py",
    "test_get_products_anonymous_pricing.py",
    "test_get_products_auth_obligations.py",
    "test_get_products_device_type_filter.py",
    "test_get_products_filter_semantics.py",
    "test_get_products_policy_obligations.py",
    "test_get_products_response_constraints.py",
    "test_property_list_crud.py",
    "test_property_list_resolution.py",
    "test_property_list_validation.py",
    "test_targeting_validation_chain.py",
]


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _get_src_imports(tree: ast.Module) -> set[str]:
    """Extract all names imported from ``src.*`` at module level.

    Returns the set of local names bound by those imports. For example::

        from src.core.tools.products import _get_products_impl
        # -> {"_get_products_impl"}

        from src.core.schemas.product import Product as SchemaProduct
        # -> {"SchemaProduct"}

        import src.core.exceptions
        # -> {"src"}
    """
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    names.add(alias.asname or alias.name)
    return names


def _get_test_helper_imports(tree: ast.Module) -> set[str]:
    """Extract names imported from test infrastructure packages.

    Recognized packages: ``tests.helpers.*``, ``tests.factories.*``,
    ``tests.harness.*``. These are test utilities that wrap production code
    (e.g., ProductEnv calls ``_get_products_impl`` internally).
    """
    _HELPER_PREFIXES = ("tests.helpers", "tests.factories", "tests.harness")
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and any(node.module.startswith(p) for p in _HELPER_PREFIXES)
        ):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _function_body_uses_names(func_node: ast.FunctionDef, names: set[str]) -> bool:
    """Check if a function body references any of the given names.

    Used for helper detection (transitivity) and xfail checks. The main
    obligation check uses ``_function_body_calls_names`` instead.
    """
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names:
            return True
    return False


def _function_body_calls_names(func_node: ast.FunctionDef, names: set[str]) -> bool:
    """Check if a function body CALLS (not just references) any of the given names.

    Looks for ``ast.Call`` nodes where the callable resolves to a production name::

        _get_products_impl(req, identity)     # Call(func=Name("_get_products_impl"))
        await env.call_impl(brief="test")     # Call(func=Attr(Name("env"), "call_impl"))
                                              # but env must be in names for this to match
        ProductEnv()                          # Call(func=Name("ProductEnv"))
        self._call_helper()                   # Call(func=Attr(Name("self"), "_call_helper"))

    This catches the gaming pattern: ``import _get_products_impl  # noqa: F401``
    passes the old "references" check but fails this "calls" check.
    """
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        # Direct call: name(...)
        if isinstance(func, ast.Name) and func.id in names:
            return True

        # Method call on a known name: name.method(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in names:
                return True
            # self.helper(...) where helper is a transitive production helper
            if func.value.id == "self" and func.attr in names:
                return True

    return False


def _function_body_has_src_call_in_body(func_node: ast.FunctionDef) -> bool:
    """Check if the function body has a local ``src.*`` import AND calls the imported name.

    Handles the common xfail stub pattern::

        def test_something(self):
            from src.core.tools.property_list import _create_property_list_impl
            await _create_property_list_impl(req, identity)

    Returns True only if the locally imported name appears in an ``ast.Call``.
    """
    local_src_names: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            for alias in node.names:
                local_src_names.add(alias.asname or alias.name)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    local_src_names.add(alias.asname or alias.name)

    if not local_src_names:
        return False

    return _function_body_calls_names(func_node, local_src_names)


def _get_production_helpers(tree: ast.Module, src_names: set[str]) -> set[str]:
    """Find module-level/class-level functions that use production code.

    If a helper function ``_call_get_products`` calls ``get_products_raw``
    (imported from ``src.*``), then any test calling ``_call_get_products``
    is transitively using production code.

    Also includes fixture functions (decorated with @pytest.fixture) that use
    production code.

    Note: helpers use the weaker "references" check (not "calls") because a
    helper that creates a ``ResolvedIdentity`` via construction is still
    exercising production code transitively.
    """
    helpers: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                continue
            if _function_body_uses_names(node, src_names) or _function_has_src_import_in_body(node):
                helpers.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name.startswith("test_"):
                        continue
                    if _function_body_uses_names(child, src_names) or _function_has_src_import_in_body(child):
                        helpers.add(child.name)
    return helpers


def _function_has_src_import_in_body(func_node: ast.FunctionDef) -> bool:
    """Check if the function body contains a local import from ``src.*``.

    Some tests do late imports inside the function body::

        def test_something(self):
            from src.core.tools.products import _get_products_impl
            result = _get_products_impl(...)
    """
    for node in ast.walk(func_node):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    return True
    return False


def _has_xfail_marker(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with ``pytest.mark.xfail`` or a variable containing 'xfail'.

    Recognizes::

        @pytest.mark.xfail(...)
        @pytest.mark.xfail
        @_XFAIL_NO_IMPL
        @_xfail_something
    """
    for dec in func_node.decorator_list:
        # @pytest.mark.xfail(...)
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Attribute) and func.attr == "xfail":
                return True
        # @pytest.mark.xfail (without call)
        if isinstance(dec, ast.Attribute) and dec.attr == "xfail":
            return True
        # @_XFAIL_NO_IMPL or similar variable
        if isinstance(dec, ast.Name) and "xfail" in dec.id.lower():
            return True
    return False


def _get_covers_id(func_node: ast.FunctionDef) -> str | None:
    """Extract the obligation ID from a ``Covers: <id>`` tag in the docstring."""
    docstring = ast.get_docstring(func_node)
    if not docstring:
        return None
    m = _COVERS_RE.search(docstring)
    if m and _OBLIGATION_ID_RE.match(m.group(1)):
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path) -> list[tuple[str, str, str]]:
    """Scan a test file for obligation-tagged tests that don't call production code.

    Returns list of (file:class.method, obligation_id, reason).

    **Non-xfail tests** must CALL production code (ast.Call).
    **xfail tests** must at minimum IMPORT from src.* (showing intent).
    """
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    src_names = _get_src_imports(tree)
    helper_names = _get_test_helper_imports(tree)
    direct_production = src_names | helper_names
    # Also find module-level helpers that transitively call production code
    transitive_helpers = _get_production_helpers(tree, direct_production)
    production_names = direct_production | transitive_helpers

    violations: list[tuple[str, str, str]] = []
    relative = path.relative_to(_TEST_ROOT.parent)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue

        covers_id = _get_covers_id(node)
        if not covers_id:
            continue

        is_xfail = _has_xfail_marker(node)

        if is_xfail:
            # xfail stubs: import from src.* is sufficient (the call may fail)
            ok = _function_body_uses_names(node, production_names) or _function_has_src_import_in_body(node)
            reason = "xfail stub without src.* import — add the import you intend to call"
        else:
            # Real tests: must CALL production code, not just reference it
            ok = _function_body_calls_names(node, production_names) or _function_body_has_src_call_in_body(node)
            if ok:
                reason = ""  # Not a violation
            elif _function_body_uses_names(node, production_names) or _function_has_src_import_in_body(node):
                reason = "references src.* but never calls it — add a real function call"
            else:
                reason = "no production code in test body — test only exercises Python builtins or adcp library"

        if not ok:
            # Determine the class context
            class_name = ""
            for parent in ast.walk(tree):
                if isinstance(parent, ast.ClassDef):
                    for child in ast.iter_child_nodes(parent):
                        if child is node:
                            class_name = f"{parent.name}."
                            break

            test_id = f"{relative}:{class_name}{node.name}"
            violations.append((test_id, covers_id, reason))

    return violations


def _scan_all_files() -> list[tuple[str, str, str]]:
    """Scan all obligation-tagged test files."""
    violations: list[tuple[str, str, str]] = []

    # Unit entity files
    for name in _UNIT_ENTITY_FILES:
        path = _UNIT_DIR / name
        if path.exists():
            violations.extend(_scan_file(path))

    # Integration tests — v3 files (original scope)
    for path in sorted(_INTEGRATION_DIR.glob("test_*_v3.py")):
        violations.extend(_scan_file(path))

    # Former integration_v2 files with Covers: tags (merged into integration/)
    for name in _INTEGRATION_FORMER_V2_FILES:
        path = _INTEGRATION_DIR / name
        if path.exists():
            violations.extend(_scan_file(path))

    return violations


def _load_allowlist() -> set[str]:
    """Load the known violations allowlist."""
    if not _ALLOWLIST_FILE.exists():
        return set()
    return set(json.loads(_ALLOWLIST_FILE.read_text()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestObligationTestQuality:
    """Structural guard: obligation-tagged tests must CALL production code."""

    def test_no_new_sham_tests(self):
        """Every obligation-tagged test must call production code or be allowlisted.

        A test with Covers: <id> that never CALLS src.* is a sham — it claims
        to verify behavior but only tests Python language features or the
        upstream adcp library. Importing production code without calling it
        does not count.
        """
        violations = _scan_all_files()
        allowlist = _load_allowlist()

        new_violations = [(t, oid, reason) for t, oid, reason in violations if t not in allowlist]

        assert not new_violations, (
            f"Found {len(new_violations)} obligation-tagged test(s) that don't call production code.\n"
            f"Fix the test to CALL an _impl function or harness method, "
            f"or add to the allowlist (obligation_test_quality_allowlist.json):\n"
            + "\n".join(f"  {t} (Covers: {oid}) — {reason}" for t, oid, reason in sorted(new_violations))
        )

    def test_allowlist_entries_still_violations(self):
        """Every allowlist entry must still be a violation.

        When a test is fixed (now calls production code), remove it from the
        allowlist. This prevents the allowlist from becoming stale.
        """
        violations = _scan_all_files()
        violation_ids = {t for t, _, _ in violations}
        allowlist = _load_allowlist()

        stale = allowlist - violation_ids
        assert not stale, (
            f"Found {len(stale)} allowlist entries that are no longer violations.\n"
            f"These tests now call production code — remove from "
            f"obligation_test_quality_allowlist.json:\n" + "\n".join(f"  {t}" for t in sorted(stale))
        )

    def test_violation_count_tracked(self):
        """Track the total violation count for monitoring."""
        violations = _scan_all_files()
        allowlist = _load_allowlist()

        print("\n  Total obligation-tagged tests scanned")
        print(f"  Violations (don't CALL src.*): {len(violations)}")
        print(f"  Allowlisted:                   {len(allowlist)}")
        print(f"  New (not allowlisted):          {len([v for v in violations if v[0] not in allowlist])}")

        # Allowlist size must match violation count exactly
        violation_ids = {t for t, _, _ in violations}
        assert len(allowlist) == len(violation_ids & allowlist), (
            f"Allowlist size ({len(allowlist)}) doesn't match violation count "
            f"({len(violation_ids & allowlist)} actual violations in allowlist). "
            f"Update the allowlist."
        )
