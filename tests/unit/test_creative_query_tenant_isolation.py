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

ROOT = Path(__file__).resolve().parents[2]


def _extract_select_calls_in_function(file_path: str, func_name: str) -> list[dict]:
    """Extract select() call info from a function using AST.

    Returns list of dicts with:
    - model: the model name being selected (e.g., "Creative", "CreativeModel")
    - has_tenant_filter: whether .filter/.where includes tenant_id
    - lineno: line number of the select() call
    """
    source_path = ROOT / file_path
    tree = ast.parse(source_path.read_text())
    results = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue

        # Walk the function body for select() calls
        source_text = source_path.read_text()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue

            # Find select(Model) calls
            func = child.func
            if not (isinstance(func, ast.Name) and func.id == "select"):
                continue
            if not child.args:
                continue

            # Get the model name
            model_arg = child.args[0]
            model_name = None
            if isinstance(model_arg, ast.Name):
                model_name = model_arg.id
            elif isinstance(model_arg, ast.Attribute):
                model_name = model_arg.attr

            if not model_name:
                continue

            # Check if this is a Creative/CreativeReview/CreativeModel query
            if "Creative" not in model_name and "creative" not in model_name.lower():
                continue

            # Now walk UP the chain from this select() to find the full
            # statement including .filter()/.where()/.filter_by() calls.
            # We scan the function source lines around the select() call
            # for tenant_id references.
            select_line = child.lineno

            # Get surrounding lines (the full statement can span multiple lines)
            lines = source_text.splitlines()
            # Look at lines from select() to the next statement (up to 10 lines)
            stmt_text = "\n".join(lines[select_line - 1 : select_line + 10])

            has_tenant_filter = "tenant_id" in stmt_text

            results.append(
                {
                    "model": model_name,
                    "has_tenant_filter": has_tenant_filter,
                    "lineno": select_line,
                }
            )

    return results


class TestCreativeQueryTenantIsolation:
    """Every Creative/CreativeReview query must include tenant_id filter.

    After the composite PK migration (bfbf084c), creative_id is no longer
    globally unique. Queries without tenant_id can return rows from other
    tenants — a cross-tenant data leak.
    """

    def test_fetch_creative_approvals_scopes_by_tenant(self):
        """_fetch_creative_approvals must filter Creative by tenant_id.

        The function already has tenant_id as a parameter and uses it on
        CreativeAssignment queries, but the Creative query at line 414
        only filters by creative_id.
        """
        selects = _extract_select_calls_in_function(
            "src/core/tools/media_buy_list.py",
            "_fetch_creative_approvals",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert creative_selects, "Expected at least one Creative select() call"

        for s in creative_selects:
            assert s["has_tenant_filter"], (
                f"Creative query at media_buy_list.py:{s['lineno']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-s9eg)."
            )

    def test_execute_approved_creative_lookup_scopes_by_tenant(self):
        """execute_approved_media_buy must filter Creative by tenant_id.

        The function opens its own session and queries CreativeModel by
        creative_id alone at line 783. The tenant dict is available
        as tenant_dict["tenant_id"].
        """
        selects = _extract_select_calls_in_function(
            "src/core/tools/media_buy_create.py",
            "execute_approved_media_buy",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert creative_selects, "Expected at least one Creative select() call"

        for s in creative_selects:
            assert s["has_tenant_filter"], (
                f"Creative query at media_buy_create.py:{s['lineno']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-s9eg)."
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
