"""Guard: BDD Then steps must use strong assertions, not weak structural checks.

This guard catches assertion anti-patterns that pass syntactically but test
nothing meaningful. Each pattern is tautological or too permissive:

**Pattern 1 — ``assert hasattr(obj, attr)``**: Always True on Pydantic models
because schema-declared fields exist as class attributes regardless of value.
Use ``assert obj.field is not None`` to check population.

**Pattern 2 — ``assert getattr(obj, attr, None) is not None``**: Proves the
field is populated but never checks the VALUE. Use
``assert obj.field == expected`` for correctness.

**Pattern 3 — ``assert len(items) > 0``**: Proves something exists but not
that it's the RIGHT thing. Use element-level assertions or set comparisons.

**Pattern 4 — ``if ctx.get("error"): return``**: Short-circuits the Then step
by checking test-harness state instead of the production response. The step
must assert on the actual response, not bail out early on a ctx flag.

beads: salesagent-beq4
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import iter_then_functions

# ── Allowlists (ratcheting — may only shrink) ───────────────────────────
# Each entry is "relative/path.py:func_lineno func_name".
# Every allowlisted violation MUST have a # FIXME(salesagent-beq4) comment
# at the source location.

_HASATTR_ALLOWLIST: set[str] = {
    # FIXME(salesagent-beq4): replace hasattr with value assertion
    "bdd/steps/domain/uc003_update_media_buy.py:763 then_implementation_date_null",
    "bdd/steps/domain/uc003_update_media_buy.py:773 then_implementation_date_not_null",
    "bdd/steps/domain/uc003_update_media_buy.py:892 then_response_has_sandbox",
    "bdd/steps/domain/uc004_delivery.py:1823 then_has_errors_field",
    "bdd/steps/domain/uc011_accounts.py:1183 then_has_accounts_array",
    "bdd/steps/domain/uc011_accounts.py:1202 then_response_is_success_variant",
    "bdd/steps/domain/uc011_accounts.py:1437 then_webhook_registered",
    "bdd/steps/domain/uc011_accounts.py:1505 then_push_sent",
    "bdd/steps/domain/uc011_accounts.py:1823 then_only_agent_a_deactivated",
    "bdd/steps/domain/uc011_accounts.py:420 then_accounts_array_count",
    "bdd/steps/domain/uc011_accounts.py:541 then_empty_accounts",
    "bdd/steps/domain/uc011_accounts.py:612 then_accounts_from_first_page",
    "bdd/steps/domain/uc011_accounts.py:704 then_response_outcome",
    "bdd/steps/domain/uc011_accounts.py:855 then_success_with_accounts",
    "bdd/steps/domain/uc019_query_media_buys.py:998 then_package_details",
}

_GETATTR_EXISTENCE_ALLOWLIST: set[str] = {
    # FIXME(salesagent-beq4): replace getattr existence check with value comparison
    "bdd/steps/domain/uc003_update_media_buy.py:746 then_response_has_buyer_ref",
    "bdd/steps/domain/uc019_query_media_buys.py:998 then_package_details",
}

_COUNT_ONLY_ALLOWLIST: set[str] = {
    # FIXME(salesagent-beq4): replace count-only check with element-level assertion
    "bdd/steps/domain/uc002_create_media_buy.py:2134 then_creatives_assigned_to_packages",
    "bdd/steps/domain/uc002_nfr.py:184 then_protocol_audit_logged",
    "bdd/steps/domain/uc003_update_media_buy.py:823 then_affected_packages_present",
    "bdd/steps/domain/uc003_update_media_buy.py:857 then_affected_package_budget",
    "bdd/steps/domain/uc004_delivery.py:1075 then_has_metrics",
    "bdd/steps/domain/uc004_delivery.py:1094 then_has_packages",
    "bdd/steps/domain/uc004_delivery.py:1139 then_has_mb_status",
    "bdd/steps/domain/uc004_delivery.py:1246 then_only_status",
    "bdd/steps/domain/uc004_delivery.py:1506 then_log_auth_rejection",
    "bdd/steps/domain/uc004_delivery.py:1795 then_has_deliveries_field",
    "bdd/steps/domain/uc004_delivery.py:1918 then_packages_include_breakdown",
    "bdd/steps/domain/uc004_delivery.py:1937 then_packages_exclude_breakdown",
    "bdd/steps/domain/uc004_delivery.py:1957 then_packages_limited",
    "bdd/steps/domain/uc004_delivery.py:2034 then_packages_include_field",
    "bdd/steps/domain/uc004_delivery.py:2054 then_packages_include_two",
    "bdd/steps/domain/uc004_delivery.py:2080 then_packages_exclude_field",
    "bdd/steps/domain/uc004_delivery.py:2093 then_geo_system",
    "bdd/steps/domain/uc004_delivery.py:2116 then_placement_sorted_fallback",
    "bdd/steps/domain/uc004_delivery.py:2141 then_placement_sorted",
    "bdd/steps/domain/uc011_accounts.py:1183 then_has_accounts_array",
    "bdd/steps/domain/uc011_accounts.py:1268 then_all_accounts_action",
    "bdd/steps/domain/uc011_accounts.py:1284 then_failed_has_errors",
    "bdd/steps/domain/uc011_accounts.py:1391 then_setup_has_url",
    "bdd/steps/domain/uc011_accounts.py:1742 then_account_in_db",
    "bdd/steps/domain/uc011_accounts.py:1823 then_only_agent_a_deactivated",
    "bdd/steps/domain/uc011_accounts.py:2345 then_no_production_accounts",
    "bdd/steps/domain/uc011_accounts.py:2458 then_governance_agents_stored",
    "bdd/steps/domain/uc011_accounts.py:430 then_accounts_have_fields",
    "bdd/steps/domain/uc011_accounts.py:451 then_accounts_are_agent_scoped",
    "bdd/steps/domain/uc011_accounts.py:484 then_only_status",
    "bdd/steps/domain/uc011_accounts.py:502 then_other_statuses_excluded",
    "bdd/steps/domain/uc011_accounts.py:704 then_response_outcome",
    "bdd/steps/domain/uc011_accounts.py:959 then_all_accounts_echo_brand",
    "bdd/steps/domain/uc019_query_media_buys.py:1048 then_creative_approval_state",
    "bdd/steps/domain/uc019_query_media_buys.py:1089 then_buyer_refs_for_correlation",
    "bdd/steps/domain/uc019_query_media_buys.py:1923 then_either_status_returned",
    "bdd/steps/domain/uc019_query_media_buys.py:1940 then_any_status_returned",
    "bdd/steps/domain/uc019_query_media_buys.py:998 then_package_details",
    "bdd/steps/domain/uc026_package_media_buy.py:1643 then_package_has_id",
    "bdd/steps/domain/uc026_package_media_buy.py:1729 then_package_default_formats",
    "bdd/steps/domain/uc026_package_media_buy.py:1796 then_package_formats_to_provide",
    "bdd/steps/domain/uc026_package_media_buy.py:1929 then_package_all_fields",
    "bdd/steps/domain/uc026_package_media_buy.py:2386 then_new_pkg_in_mb",
    "bdd/steps/domain/uc026_package_media_buy.py:2419 then_new_pkg_created",
    "bdd/steps/generic/then_media_buy.py:301 then_webhook_notification",
    "bdd/steps/generic/then_media_buy.py:74 then_response_has_packages",
    "bdd/steps/generic/then_media_buy.py:738 then_response_has_success_fields",
    "bdd/steps/generic/then_payload.py:146 then_format_assets",
    "bdd/steps/generic/then_payload.py:85 then_has_referrals",
    "bdd/steps/generic/then_payload.py:99 then_referral_fields",
}

# Pattern 4 has zero current violations — purely regression prevention.
_CTX_ERROR_FALLBACK_ALLOWLIST: set[str] = set()


# ── Pattern scanners ─────────────────────────────────────────────────────


def _find_assert_hasattr(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Detect ``assert hasattr(obj, attr)`` in a Then function.

    Catches both direct ``assert hasattr(...)`` and inside BoolOps like
    ``assert hasattr(x, y) and x.y == z``. The hasattr part is still
    tautological even when combined with a real assertion.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        # Direct: assert hasattr(...)
        if isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "hasattr":
            return True
        # In BoolOp: assert hasattr(...) and ...
        if isinstance(test, ast.BoolOp):
            for val in test.values:
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "hasattr":
                    return True
    return False


def _find_getattr_existence_only(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect ``assert getattr(obj, attr, None) is not None``.

    This pattern proves a field is populated but never checks its value.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.IsNot):
            continue
        if len(test.comparators) != 1:
            continue
        comp = test.comparators[0]
        if not (isinstance(comp, ast.Constant) and comp.value is None):
            continue
        left = test.left
        if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "getattr"):
            continue
        if len(left.args) >= 3:
            default = left.args[2]
            if isinstance(default, ast.Constant) and default.value is None:
                return True
    return False


def _is_len_gt_zero(test: ast.Compare) -> bool:
    """Check if a Compare node is ``len(x) > 0``, ``len(x) >= 1``, or ``0 < len(x)``."""
    if len(test.ops) != 1 or len(test.comparators) != 1:
        return False

    left = test.left
    op = test.ops[0]
    comp = test.comparators[0]

    def _is_len_call(node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"

    def _is_constant(node: ast.expr, value: int) -> bool:
        return isinstance(node, ast.Constant) and node.value == value

    # len(x) > 0
    if _is_len_call(left) and isinstance(op, ast.Gt) and _is_constant(comp, 0):
        return True
    # len(x) >= 1
    if _is_len_call(left) and isinstance(op, ast.GtE) and _is_constant(comp, 1):
        return True
    # 0 < len(x)
    if _is_constant(left, 0) and isinstance(op, ast.Lt) and _is_len_call(comp):
        return True
    # 1 <= len(x)
    if _is_constant(left, 1) and isinstance(op, ast.LtE) and _is_len_call(comp):
        return True

    return False


def _find_count_only_assertion(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect ``assert len(items) > 0`` as the sole collection check."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert):
            continue
        if isinstance(node.test, ast.Compare) and _is_len_gt_zero(node.test):
            return True
    return False


def _find_ctx_error_fallback(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Detect ``if ctx.get("error"): return`` in Then steps.

    This pattern short-circuits assertion logic by checking test-harness
    state instead of inspecting the production response.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_ctx_error = False

        # ctx.get("error")
        if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute):
            if test.func.attr == "get" and isinstance(test.func.value, ast.Name) and test.func.value.id == "ctx":
                if test.args and isinstance(test.args[0], ast.Constant) and test.args[0].value == "error":
                    is_ctx_error = True

        # ctx["error"]
        if isinstance(test, ast.Subscript) and isinstance(test.value, ast.Name):
            if test.value.id == "ctx" and isinstance(test.slice, ast.Constant):
                if test.slice.value == "error":
                    is_ctx_error = True

        if is_ctx_error:
            # Check if the if-body contains a return statement
            for child in node.body:
                for n in ast.walk(child):
                    if isinstance(n, ast.Return):
                        return True
    return False


# ── Scan orchestrator ────────────────────────────────────────────────────


def _scan_pattern(
    detector: object,
    allowlist: set[str],
    pattern_label: str,
) -> tuple[list[str], list[str]]:
    """Run a detector across all Then steps.

    Returns (new_violations, stale_allowlist_entries).
    """
    new_violations = []
    seen_in_allowlist = set()

    for key, func in iter_then_functions():
        if detector(func):  # type: ignore[operator]
            if key in allowlist:
                seen_in_allowlist.add(key)
            else:
                new_violations.append(f"{key} [{pattern_label}]")

    stale = sorted(allowlist - seen_in_allowlist)
    return new_violations, stale


# ── Assertion helper ──────────────────────────────────────────────────────


def _assert_no_violations(
    detector: object,
    allowlist: set[str],
    pattern_label: str,
    allowlist_name: str,
) -> None:
    """Run a pattern scan and assert no new violations / no stale allowlist entries."""
    new_violations, stale = _scan_pattern(detector, allowlist, pattern_label)
    errors = []
    if new_violations:
        errors.append(
            f"Found {len(new_violations)} new {pattern_label} violation(s):\n"
            + "\n".join(f"  {v}" for v in new_violations)
        )
    if stale:
        errors.append(
            f"Stale allowlist entries (violations were fixed — remove from "
            f"{allowlist_name}):\n" + "\n".join(f"  {s}" for s in stale)
        )
    assert not errors, "\n\n".join(errors)


# ── Test class ───────────────────────────────────────────────────────────


class TestBddAssertionStrength:
    """Structural guard: Then steps must use strong assertion patterns.

    Catches four anti-patterns that produce tautological or overly permissive
    assertions in BDD Then steps.
    """

    def test_no_assert_hasattr(self) -> None:
        """``assert hasattr(obj, attr)`` is always True on Pydantic models.

        Use ``assert obj.field is not None`` to check population, or
        ``assert obj.field == expected`` to check correctness.
        """
        _assert_no_violations(_find_assert_hasattr, _HASATTR_ALLOWLIST, "assert-hasattr", "_HASATTR_ALLOWLIST")

    def test_no_getattr_existence_only(self) -> None:
        """``assert getattr(obj, attr, None) is not None`` proves presence, not correctness.

        Replace with ``assert obj.field == expected_value``.
        """
        _assert_no_violations(
            _find_getattr_existence_only,
            _GETATTR_EXISTENCE_ALLOWLIST,
            "getattr-existence-only",
            "_GETATTR_EXISTENCE_ALLOWLIST",
        )

    def test_no_count_only_assertions(self) -> None:
        """``assert len(items) > 0`` proves existence, not correctness.

        Use element-level assertions (``assert items[0].id == expected``)
        or set comparisons (``assert {i.id for i in items} == expected_ids``).
        """
        _assert_no_violations(
            _find_count_only_assertion,
            _COUNT_ONLY_ALLOWLIST,
            "count-only",
            "_COUNT_ONLY_ALLOWLIST",
        )

    def test_no_ctx_error_fallback(self) -> None:
        """``if ctx.get("error"): return`` checks test harness, not production code.

        Then steps must assert on the actual response payload, not bail
        out early when the test harness recorded an error.
        """
        _assert_no_violations(
            _find_ctx_error_fallback,
            _CTX_ERROR_FALLBACK_ALLOWLIST,
            "ctx-error-fallback",
            "_CTX_ERROR_FALLBACK_ALLOWLIST",
        )
