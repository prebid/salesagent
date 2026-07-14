"""Reproduction test for salesagent-s9eg: Cross-tenant Creative query leak.

Bug: Several queries fetch Creative rows by creative_id alone without
tenant_id filter. After the composite PK migration (bfbf084c), creative_id
is no longer unique across tenants — two tenants can have the same
creative_id. Missing tenant_id filter → cross-tenant data leak.

These tests compile the actual SQLAlchemy statements used in production code
and assert they include the tenant_id filter. Each test fails today and
will pass once the queries are fixed.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import extract_select_calls

ROOT = Path(__file__).resolve().parents[2]


def _extract_select_calls_in_function(file_path: str, func_name: str) -> list[dict]:
    """Extract Creative-related select() call info from a function using AST.

    Returns list of dicts with:
    - model: the model name being selected (e.g., "Creative", "CreativeModel")
    - has_tenant_filter: whether .filter/.where includes tenant_id
    - lineno: line number of the select() call
    """
    return extract_select_calls(
        ROOT / file_path,
        func_name,
        model_predicate=lambda name: "creative" in name.lower(),
    )


def _function_calls_repo_get_by_ids(file_path: str, func_name: str) -> bool:
    """True if *func_name* calls CreativeRepository(...).get_by_ids(...).

    The repository pins tenant_id in its constructor and requires
    principal_id, so routing through it IS the tenant-isolation fix —
    stronger than an inline select with a hand-written tenant filter.
    """
    source_path = ROOT / file_path
    tree = ast.parse(source_path.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get_by_ids"):
                continue
            base = func.value
            if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "CreativeRepository":
                return True
    return False


class TestCreativeQueryTenantIsolation:
    """Every Creative/CreativeReview query must include tenant_id filter.

    After the composite PK migration (bfbf084c), creative_id is no longer
    globally unique. Queries without tenant_id can return rows from other
    tenants — a cross-tenant data leak.
    """

    def test_fetch_creative_approvals_scopes_by_tenant(self):
        """_fetch_creative_approvals must load Creative via CreativeRepository.

        Originally this pinned an inline tenant-filtered select(). The lookup
        now routes through CreativeRepository.get_by_ids (tenant_id pinned in
        the constructor, principal_id required) — the centralized form of the
        same isolation fix (salesagent-s9eg, DRY'd in salesagent-ol24). An
        inline select(Creative) reappearing here is a regression.
        """
        selects = _extract_select_calls_in_function(
            "src/core/tools/media_buy_list.py",
            "_fetch_creative_approvals",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert not creative_selects, (
            f"Inline Creative select() at media_buy_list.py:"
            f"{[s['lineno'] for s in creative_selects]} — Creative loads must go "
            f"through CreativeRepository.get_by_ids (salesagent-s9eg/salesagent-ol24)."
        )
        assert _function_calls_repo_get_by_ids("src/core/tools/media_buy_list.py", "_fetch_creative_approvals"), (
            "_fetch_creative_approvals must load creatives via CreativeRepository(...).get_by_ids()."
        )

    def test_execute_approved_creative_lookup_scopes_by_tenant(self):
        """execute_approved_media_buy must load Creative via CreativeRepository.

        Originally this pinned an inline tenant-filtered select(). The lookup
        now routes through CreativeRepository.get_by_ids (tenant_id pinned in
        the constructor, principal_id required) — the centralized form of the
        same isolation fix (salesagent-s9eg, DRY'd in salesagent-ol24). An
        inline select(Creative) reappearing here is a regression.
        """
        selects = _extract_select_calls_in_function(
            "src/core/tools/media_buy_create.py",
            "execute_approved_media_buy",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert not creative_selects, (
            f"Inline Creative select() at media_buy_create.py:"
            f"{[s['lineno'] for s in creative_selects]} — Creative loads must go "
            f"through CreativeRepository.get_by_ids (salesagent-s9eg/salesagent-ol24)."
        )
        assert _function_calls_repo_get_by_ids("src/core/tools/media_buy_create.py", "execute_approved_media_buy"), (
            "execute_approved_media_buy must load creatives via CreativeRepository(...).get_by_ids()."
        )

    def test_get_creative_with_latest_review_scopes_by_tenant(self):
        """get_creative_with_latest_review must accept and filter by tenant_id.

        Both the Creative and CreativeReview queries filter only by
        creative_id. The function doesn't even accept tenant_id as a
        parameter — it needs to be added to the signature.
        """
        selects = _extract_select_calls_in_function(
            "src/core/database/queries.py",
            "get_creative_with_latest_review",
        )

        # Every select on a tenant-scoped model must include tenant_id
        for s in selects:
            assert s["has_tenant_filter"], (
                f"Query at queries.py:{s['lineno']} on {s['model']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-s9eg)."
            )

    def test_get_creative_reviews_scopes_by_tenant(self):
        """get_creative_reviews must accept and filter by tenant_id.

        The function filters CreativeReview only by creative_id. Since
        creative_id is buyer-scoped (not globally unique), this can
        return reviews from other tenants.
        """
        selects = _extract_select_calls_in_function(
            "src/core/database/queries.py",
            "get_creative_reviews",
        )

        for s in selects:
            assert s["has_tenant_filter"], (
                f"Query at queries.py:{s['lineno']} on {s['model']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-s9eg)."
            )
