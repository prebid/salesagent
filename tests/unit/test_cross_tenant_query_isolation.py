"""Reproduction tests for cross-tenant query leaks.

After the composite PK migration (bfbf084c), IDs like principal_id, creative_id,
media_buy_id, and package_id are no longer globally unique. Every query on a
tenant-scoped model MUST include tenant_id in the WHERE clause.

Note: MediaPackage has NO tenant_id column — its PK is (media_buy_id, package_id).
Tenant isolation comes through the media_buy_id FK to MediaBuy (globally unique PK).
MediaPackage queries filtered by media_buy_id are inherently tenant-safe.

These tests use AST scanning: each function below reaches a tenant-scoped model through
the repository that pins tenant_id in its constructor, and must not query it directly again.
"""

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import extract_select_calls

ROOT = Path(__file__).resolve().parents[2]


# TestAuthUtilsTenantIsolation is DELETED. It scanned auth.py's get_principal_object for a
# tenant_id filter on its select(Principal); the function was deleted in 47d57e5d6 and
# auth.py now holds one push-notification header helper and touches no Principal at all.
# The test could not be retargeted, because the lookup it graded no longer has a raw
# spelling to scan: the resolver loads the principal through PrincipalRepository, whose
# tenant_id is constructor-bound and applied by every select in it
# (repositories/principal.py). Two mechanisms hold what the scan held:
#   - structurally, ruff-boundary.toml's TID251 ban on src.core.database.models.Principal
#     (43a205a72) makes a raw select on the model unwritable outside the repositories
#     package, so there is no un-scoped lookup to find;
#   - behaviourally, on the wire, by BR-SECURITY-002-tenant-isolation.feature --
#     @T-SECURITY-002-credential-does-not-cross-tenants grades both directions of a
#     credential minted for one tenant being refused (AUTH_INVALID) by the other, and
#     @T-SECURITY-002-own-tenant-only grades that a buyer sees its own tenant's products
#     AND no other tenant's, which is the leak this scan was a proxy for.

# TestAdapterTenantIsolation is DELETED. Its one test scanned
# GAMOrdersManager.create_line_items for a tenant_id filter on its select(DBCreative).
# 43a205a72 moved that query into CreativeRepository.admin_get_by_ids, which applies the
# SAME two predicates (Creative.tenant_id == self._tenant_id, creative_id.in_(ids)) with
# tenant_id constructor-bound, so the filter the scan graded survives verbatim one layer
# down -- only the spelling inside create_line_items is gone, and a function-scoped AST
# scan has nothing left to read. ("admin_" there means publisher-scoped WITHIN the tenant,
# not cross-tenant; the call site passes [] when tenant_id is absent, matching the old raw
# query's NULL comparison, which matched nothing.)
#
# The original NOTE, still true: Broadstreet MediaPackage queries are not covered here
# because MediaPackage has no tenant_id column -- its isolation comes through the
# media_buy_id FK to MediaBuy (globally unique PK).
#
# _get_media_buy_delivery_impl's MediaPackage query is NOT tested here for the same reason:
# tenant isolation via the media_buy_id FK is sufficient.

#: (path, function, model): each function's access to *model* was migrated to the
#: tenant-scoped repository (MediaBuyRepository, CreativeRepository and
#: get_prior_ai_review, ProductRepository), so a raw query on it there bypasses tenant
#: isolation. ``_update_media_buy_impl`` reaches MediaBuy through MediaBuyUoW, and
#: ``approve_media_buy`` once held two raw select(MediaBuy) calls.
_MIGRATED_TO_A_REPOSITORY = [
    ("src/core/tools/media_buy_update.py", "_update_media_buy_impl", "MediaBuy"),
    ("src/admin/blueprints/operations.py", "approve_media_buy", "MediaBuy"),
    ("src/admin/blueprints/creatives.py", "_call_webhook_for_creative_status", "Creative"),
    ("src/admin/blueprints/creatives.py", "approve_creative", "Creative"),
    ("src/admin/blueprints/creatives.py", "approve_creative", "CreativeReview"),
    ("src/admin/blueprints/creatives.py", "reject_creative", "CreativeReview"),
    ("src/admin/blueprints/creatives.py", "review_creatives", "MediaBuy"),
    ("src/admin/blueprints/creatives.py", "review_creatives", "Product"),
]

#: (path, function, names): the function must name one of *names*, the tenant-scoped
#: repository or unit of work it reads the model through.
_READS_THROUGH = [
    ("src/admin/blueprints/operations.py", "approve_media_buy", ("MediaBuyRepository",)),
    ("src/admin/blueprints/creatives.py", "_call_webhook_for_creative_status", ("AdminCreativeUoW",)),
    ("src/admin/blueprints/creatives.py", "review_creatives", ("MediaBuyRepository", "AdminCreativeUoW")),
]


@pytest.mark.parametrize(("path", "function", "model"), _MIGRATED_TO_A_REPOSITORY)
def test_no_raw_query_on_a_model_its_repository_scopes(path: str, function: str, model: str) -> None:
    selects = extract_select_calls(ROOT / path, function, models=[model])
    assert selects == [], (
        f"{function}() at {path}:{[s.lineno for s in selects]} queries {model} directly. Its access "
        f"goes through the tenant-scoped repository; a raw query bypasses tenant isolation."
    )


@pytest.mark.parametrize(("path", "function", "names"), _READS_THROUGH)
def test_reads_through_a_tenant_scoped_repository(path: str, function: str, names: tuple[str, ...]) -> None:
    source = (ROOT / path).read_text(encoding="utf-8")
    node = next(
        (
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
        ),
        None,
    )
    assert node is not None, f"{function} function not found in {path}"
    text = ast.get_source_segment(source, node) or ""
    assert any(name in text for name in names), f"{function}() must use {' or '.join(names)} for tenant-scoped access"
