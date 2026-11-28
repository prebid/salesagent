"""Reproduction test for : Cross-tenant Creative query leak.

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

import pytest

from tests.unit._architecture_helpers import SelectCall, extract_select_calls

ROOT = Path(__file__).resolve().parents[2]

#: The Creative-family models, whose rows a creative_id alone no longer identifies.
_CREATIVE_MODELS = ("Creative", "CreativeReview", "CreativeAssignment")


def _extract_select_calls_in_function(file_path: str, func_name: str) -> list[SelectCall]:
    """The queries on a Creative-family model in *func_name*, each resolved by import
    (``DBCreative`` -> "Creative") and marked with whether it filters that model's tenant_id."""
    return extract_select_calls(ROOT / file_path, func_name, models=_CREATIVE_MODELS)


def _function_calls_repo_get_by_ids(file_path: str, func_name: str) -> bool:
    """True if *func_name* calls CreativeRepository(...).get_by_ids(...).

    The repository pins tenant_id in its constructor and requires
    principal_id, so routing through it IS the tenant-isolation fix —
    stronger than an inline select with a hand-written tenant filter.
    """
    source_path = ROOT / file_path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

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


#: Functions that load creatives through CreativeRepository.get_by_ids (tenant_id pinned in
#: the constructor, principal_id required). Each originally pinned an inline tenant-filtered
#: select(); an inline select(Creative) reappearing in one is a regression.
_LOADED_THROUGH_THE_REPOSITORY = [
    ("src/core/tools/media_buy_list.py", "_fetch_creative_approvals"),
    ("src/core/tools/media_buy_create.py", "execute_approved_media_buy"),
]

#: (path, function, model): no query on *model* left inline in the function.
_NO_INLINE_QUERY = [(path, function, "Creative") for path, function in _LOADED_THROUGH_THE_REPOSITORY]

#: Functions whose Creative and CreativeReview queries filter by creative_id, and so must
#: take tenant_id and filter by it too.
_TENANT_FILTERED = [
    ("src/core/database/queries.py", "get_creative_with_latest_review"),
    ("src/core/database/queries.py", "get_creative_reviews"),
]


class TestCreativeQueryTenantIsolation:
    """Every Creative/CreativeReview query must include tenant_id filter.

    After the composite PK migration (bfbf084c), creative_id is no longer
    globally unique. Queries without tenant_id can return rows from other
    tenants — a cross-tenant data leak.
    """

    @pytest.mark.parametrize(("path", "function", "model"), _NO_INLINE_QUERY)
    def test_no_inline_query_on_a_model_the_repository_loads(self, path: str, function: str, model: str):
        selects = extract_select_calls(ROOT / path, function, models=[model])
        assert selects == [], (
            f"Inline {model} query at {path}:{[s.lineno for s in selects]} — {model} loads must go "
            f"through CreativeRepository.get_by_ids."
        )

    @pytest.mark.parametrize(("path", "function"), _LOADED_THROUGH_THE_REPOSITORY)
    def test_creatives_load_through_the_repository(self, path: str, function: str):
        assert _function_calls_repo_get_by_ids(path, function), (
            f"{function} must load creatives via CreativeRepository(...).get_by_ids()."
        )

    @pytest.mark.parametrize(("path", "function"), _TENANT_FILTERED)
    def test_every_creative_query_scopes_by_tenant(self, path: str, function: str):
        for s in _extract_select_calls_in_function(path, function):
            assert s.has_tenant_filter, (
                f"Query at {path}:{s.lineno} on {s.model} is missing tenant_id filter. "
                f"This is a cross-tenant data leak ."
            )
