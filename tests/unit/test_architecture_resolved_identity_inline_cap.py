# pylint: disable=duplicate-code
"""Structural guard: inline ``ResolvedIdentity(...)`` constructions in test files must shrink toward zero.

Tests should construct ``ResolvedIdentity`` via the canonical
``PrincipalFactory.make_identity(...)`` factory from
``tests.factories.principal``. The factory is the single source of truth for
``ResolvedIdentity`` defaults — adding a spec-mandated field to
``ResolvedIdentity`` should require updating exactly one place rather than
every test file that constructs identities by hand.

A strict zero-tolerance guard already covers the A2A test surface
(``tests/unit/test_architecture_a2a_test_uses_factory.py`` — fails on any
inline ``ResolvedIdentity(...)`` in A2A test files). The broader test surface
has ~60 files with ~115 pre-existing inline constructions; this guard pins
those files at their current count via a per-file cap dict that can only
shrink. New files with inline sites fail immediately; existing files with
fewer sites force the cap down to match (no silent regressions).

A2A test files are skipped here because the stricter A2A-only guard already
enforces zero on them — including A2A files in this dict would double-cap
them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

# Per-file caps for inline ``ResolvedIdentity(...)`` constructions in
# ``tests/`` (excluding ``test_a2a*.py`` files — those are zero-tolerance
# under ``test_architecture_a2a_test_uses_factory``). Caps frozen at the
# current state on substrate landing; can only shrink.
RESOLVED_IDENTITY_PER_FILE_CAP: dict[str, int] = {
    "tests/bdd/steps/domain/uc004_delivery.py": 2,
    "tests/bdd/steps/domain/uc011_accounts.py": 2,
    "tests/factories/principal.py": 1,
    "tests/integration/conftest.py": 1,
    "tests/integration/test_account_resolution_error_codes.py": 1,
    "tests/integration/test_create_media_buy_behavioral.py": 3,
    "tests/integration/test_create_media_buy_v24.py": 5,
    "tests/integration/test_creative_assignment_principal_id.py": 1,
    "tests/integration/test_creative_lifecycle_mcp.py": 2,
    "tests/integration/test_creative_v3.py": 2,
    "tests/integration/test_cross_principal_security.py": 4,
    "tests/integration/test_delivery_v3.py": 1,
    "tests/integration/test_duplicate_product_validation.py": 2,
    "tests/integration/test_gam_pricing_models_integration.py": 6,
    "tests/integration/test_gam_pricing_restriction.py": 4,
    "tests/integration/test_get_products_anonymous_pricing.py": 1,
    "tests/integration/test_get_products_auth_obligations.py": 1,
    "tests/integration/test_get_products_behavioral.py": 1,
    "tests/integration/test_get_products_filter_semantics.py": 1,
    "tests/integration/test_get_products_policy_obligations.py": 1,
    "tests/integration/test_get_products_response_constraints.py": 3,
    "tests/integration/test_inventory_profile_media_buy.py": 1,
    "tests/integration/test_list_authorized_properties_integration.py": 6,
    "tests/integration/test_media_buy_v3.py": 1,
    "tests/integration/test_minimum_spend_validation.py": 7,
    "tests/integration/test_pricing_models_integration.py": 8,
    "tests/integration/test_product_principal_access_pipeline.py": 1,
    "tests/integration/test_product_v3.py": 1,
    "tests/integration/test_property_list_crud.py": 1,
    "tests/integration/test_property_list_validation.py": 1,
    "tests/integration/test_update_media_buy_creative_assignment.py": 5,
    "tests/integration/test_update_media_buy_persistence.py": 1,
    "tests/unit/test_adcp_25_creative_management.py": 1,
    "tests/unit/test_auth_consistency.py": 3,
    "tests/unit/test_auth_context_middleware_population.py": 0,
    "tests/unit/test_auth_requirements.py": 6,
    "tests/unit/test_authorized_properties_behavioral.py": 2,
    "tests/unit/test_brand_manifest_policy.py": 1,
    "tests/unit/test_context_management.py": 1,
    "tests/unit/test_delivery.py": 2,
    "tests/unit/test_delivery_poll_behavioral.py": 3,
    "tests/unit/test_dry_run_no_persistence.py": 1,
    "tests/unit/test_error_format_consistency.py": 9,
    "tests/unit/test_gam_placement_targeting.py": 2,
    "tests/unit/test_get_media_buys_architecture.py": 2,
    "tests/unit/test_get_products_impl_coverage.py": 1,
    "tests/unit/test_impl_resolved_identity.py": 2,
    "tests/unit/test_media_buy.py": 9,
    "tests/unit/test_no_contextvar_in_a2a.py": 2,
    "tests/unit/test_performance_index_behavioral.py": 3,
    "tests/unit/test_pr1071_review_fixes.py": 3,
    "tests/unit/test_property_list_schema.py": 1,
    "tests/unit/test_quiet_failure_propagation.py": 2,
    "tests/unit/test_resolved_identity.py": 7,
    "tests/unit/test_rest_api_endpoints.py": 1,
    "tests/unit/test_rest_api_products.py": 1,
    "tests/unit/test_rest_depends_auth.py": 2,
    "tests/unit/test_rest_tenant_resolution.py": 2,
    "tests/unit/test_sync_creatives_a2a_account.py": 1,
    "tests/unit/test_sync_creatives_async_fix.py": 3,
    "tests/unit/test_sync_creatives_format_validation.py": 1,
    "tests/unit/test_task_management_auth.py": 2,
    "tests/unit/test_task_management_tools.py": 3,
}


def _is_a2a_test_file(path: Path) -> bool:
    """A2A test files are governed by the stricter zero-tolerance guard."""
    name = path.name
    return name.startswith("test_a2a") or name.startswith("test_architecture_a2a")


def _count_inline_resolved_identity(filepath: Path) -> list[int]:
    """Return line numbers of inline ``ResolvedIdentity(...)`` calls.

    Catches both direct (``ResolvedIdentity(...)``) and attribute
    (``mod.ResolvedIdentity(...)``) call shapes. Aliased imports
    (``from ... import ResolvedIdentity as RI; RI(...)``) are not caught —
    matching the precedent of the existing A2A guard. In practice the test
    surface uses direct or module-attribute construction.
    """
    if _is_a2a_test_file(filepath):
        return []
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except (OSError, SyntaxError):
        return []
    lines: list[int] = []
    for node in iter_call_expressions(tree):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "ResolvedIdentity":
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "ResolvedIdentity":
            lines.append(node.lineno)
    return lines


# Anchor scan paths on REPO_ROOT so the guard works from any cwd
# (matches pattern from VALUE_ERROR_PER_FILE_CAP / Pattern A guards).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = [_REPO_ROOT / "tests"]


def _rel(path: Path) -> str:
    """Return the path relative to repo root, with forward slashes."""
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


from tests.unit._per_file_cap_guard import (
    assert_capped_files_still_exist,
    assert_caps_only_shrink,
    assert_per_file_caps,
)


@pytest.mark.arch_guard
def test_resolved_identity_inline_sites_within_caps() -> None:
    """Sister guard to ``test_architecture_a2a_test_uses_factory`` — the A2A guard
    enforces zero on A2A test files; this guard caps non-A2A test files at
    their current count so new files can't introduce the anti-pattern and
    existing files can only shrink.
    """
    assert_per_file_caps(
        cap_dict=RESOLVED_IDENTITY_PER_FILE_CAP,
        count_sites=_count_inline_resolved_identity,
        scan_dirs=_SCAN_DIRS,
        site_label="inline ResolvedIdentity(...)",
        typed_raise_hint="use PrincipalFactory.make_identity(...) from tests.factories.principal",
        rel=_rel,
    )


@pytest.mark.arch_guard
def test_resolved_identity_capped_files_still_exist() -> None:
    """Stale-cap detection — every capped file path must still exist on disk."""
    assert_capped_files_still_exist(
        RESOLVED_IDENTITY_PER_FILE_CAP,
        "RESOLVED_IDENTITY_PER_FILE_CAP",
        repo_root=_REPO_ROOT,
    )


@pytest.mark.arch_guard
def test_resolved_identity_caps_only_shrink() -> None:
    """If a file has fewer inline sites than its cap, lower the cap to match."""
    assert_caps_only_shrink(
        RESOLVED_IDENTITY_PER_FILE_CAP,
        _count_inline_resolved_identity,
        repo_root=_REPO_ROOT,
    )
