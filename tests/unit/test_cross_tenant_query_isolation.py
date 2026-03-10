"""Reproduction tests for cross-tenant query leaks (salesagent-0kba, 353c, v7lw, gcjx).

After the composite PK migration (bfbf084c), IDs like principal_id, creative_id,
media_buy_id, and package_id are no longer globally unique. Every query on a
tenant-scoped model MUST include tenant_id in the WHERE clause.

Note: MediaPackage has NO tenant_id column — its PK is (media_buy_id, package_id).
Tenant isolation comes through the media_buy_id FK to MediaBuy (globally unique PK).
MediaPackage queries filtered by media_buy_id are inherently tenant-safe.

These tests use AST scanning to verify that select() calls include tenant_id.
Each test class maps to one beads bug.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Models that require tenant_id in every query
TENANT_SCOPED_MODELS = {
    "Principal",
    "ModelPrincipal",
    "Creative",
    "CreativeModel",
    "DBCreative",
    "CreativeReview",
    "MediaBuy",
    "MediaBuyModel",
    "Product",
    "CreativeAssignment",
    "PricingOption",
}

# MediaPackage / MediaPackageModel / DBMediaPackage are NOT tenant-scoped.
# They have no tenant_id column; PK is (media_buy_id, package_id).
# Tenant isolation is inherited via media_buy_id FK → MediaBuy (globally unique PK).


def _extract_select_calls(
    file_path: str,
    func_name: str,
    class_name: str | None = None,
    tenant_scoped_only: bool = True,
) -> list[dict]:
    """Extract select() call info from a function or method using AST.

    Args:
        file_path: Relative path from project root
        func_name: Function or method name to scan
        class_name: If set, look for method inside this class
        tenant_scoped_only: If True, only return calls on TENANT_SCOPED_MODELS

    Returns:
        List of dicts with: model, has_tenant_filter, lineno
    """
    source_path = ROOT / file_path
    tree = ast.parse(source_path.read_text())
    source_text = source_path.read_text()
    lines = source_text.splitlines()
    results = []

    # Find target function/method nodes
    target_nodes = []
    for node in ast.walk(tree):
        if class_name:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == func_name:
                            target_nodes.append(child)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                target_nodes.append(node)

    for func_node in target_nodes:
        for child in ast.walk(func_node):
            if not isinstance(child, ast.Call):
                continue

            func = child.func
            if not (isinstance(func, ast.Name) and func.id == "select"):
                continue
            if not child.args:
                continue

            model_arg = child.args[0]
            model_name = None
            if isinstance(model_arg, ast.Name):
                model_name = model_arg.id
            elif isinstance(model_arg, ast.Attribute):
                model_name = model_arg.attr

            if not model_name:
                continue

            if tenant_scoped_only and model_name not in TENANT_SCOPED_MODELS:
                continue

            select_line = child.lineno
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


class TestAuthUtilsTenantIsolation:
    """salesagent-0kba: auth.py Principal lookup missing tenant_id."""

    def test_get_principal_object_scopes_by_tenant(self):
        """get_principal_object must filter Principal by tenant_id.

        Principal has composite PK (tenant_id, principal_id). Querying by
        principal_id alone can return a principal from a different tenant.
        """
        selects = _extract_select_calls(
            "src/core/auth.py",
            "get_principal_object",
        )

        principal_selects = [s for s in selects if s["model"] == "Principal" or s["model"] == "ModelPrincipal"]
        assert principal_selects, "Expected at least one Principal select() call"

        for s in principal_selects:
            assert s["has_tenant_filter"], (
                f"Principal query at auth.py:{s['lineno']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-0kba)."
            )


class TestMediaBuyUpdateTenantIsolation:
    """salesagent-353c: media_buy_update.py MediaBuy queries missing tenant_id.

    After UoW migration (salesagent-4jq2), _update_media_buy_impl uses
    MediaBuyUoW which provides a tenant-scoped MediaBuyRepository.
    All MediaBuy queries go through the repository — no raw select(MediaBuy) calls remain.
    """

    def test_update_impl_uses_repository_not_raw_select(self):
        """_update_media_buy_impl must use MediaBuyUoW, not raw select(MediaBuy).

        After UoW migration, MediaBuy access goes through tenant-scoped
        MediaBuyRepository. Verify no raw select(MediaBuy) calls remain
        in the function (they would bypass tenant isolation).
        """
        selects = _extract_select_calls(
            "src/core/tools/media_buy_update.py",
            "_update_media_buy_impl",
        )

        mediabuy_selects = [s for s in selects if s["model"] in ("MediaBuy", "MediaBuyModel")]
        assert not mediabuy_selects, (
            f"Found {len(mediabuy_selects)} raw select(MediaBuy) call(s) in _update_media_buy_impl. "
            f"After UoW migration, all MediaBuy queries should go through MediaBuyRepository. "
            f"Raw select() calls bypass the tenant-scoped repository."
        )


class TestApproveMediaBuyTenantIsolation:
    """salesagent-snvr: approve_media_buy() raw select(MediaBuy) should use repository."""

    def test_approve_media_buy_uses_repository(self):
        """approve_media_buy() must use MediaBuyRepository, not raw select(MediaBuy).

        Two raw select(MediaBuy) calls remain at lines ~328 and ~431.
        Both should be migrated to MediaBuyRepository for pattern consistency.
        """
        source_path = ROOT / "src/admin/blueprints/operations.py"
        tree = ast.parse(source_path.read_text())

        # Find the approve_media_buy function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "approve_media_buy":
                func_node = node
                break

        assert func_node is not None, "approve_media_buy function not found"

        # Verify MediaBuyRepository is used
        source_text = ast.get_source_segment(source_path.read_text(), func_node)
        assert "MediaBuyRepository" in source_text, (
            "approve_media_buy() must use MediaBuyRepository for tenant-scoped MediaBuy access (salesagent-snvr)"
        )

        # Verify no raw select(MediaBuy) calls bypass the repository
        selects = _extract_select_calls(
            "src/admin/blueprints/operations.py",
            "approve_media_buy",
        )
        mediabuy_selects = [s for s in selects if s["model"] in ("MediaBuy", "MediaBuyModel")]
        assert not mediabuy_selects, (
            f"approve_media_buy() should use MediaBuyRepository, not raw select(MediaBuy). "
            f"Found {len(mediabuy_selects)} raw query(ies). (salesagent-snvr)"
        )


class TestAdapterTenantIsolation:
    """salesagent-v7lw: adapter queries missing tenant_id (GAM Creative)."""

    # NOTE: Broadstreet MediaPackage queries are NOT tested here because
    # MediaPackage has no tenant_id column. Its tenant isolation comes through
    # the media_buy_id FK to MediaBuy (globally unique PK).

    def test_gam_create_line_items_scopes_by_tenant(self):
        """GAMOrdersManager.create_line_items must filter Creative by tenant_id."""
        selects = _extract_select_calls(
            "src/adapters/gam/managers/orders.py",
            "create_line_items",
            class_name="GAMOrdersManager",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "DBCreative", "CreativeModel")]
        assert creative_selects, "Expected at least one Creative select() call"

        for s in creative_selects:
            assert s["has_tenant_filter"], (
                f"Creative query at gam/managers/orders.py:{s['lineno']} is missing tenant_id filter. "
                f"This is a cross-tenant data leak (salesagent-v7lw)."
            )


class TestAdminDeliveryTenantIsolation:
    """salesagent-gcjx: admin blueprints + delivery queries missing tenant_id."""

    def test_webhook_creative_query_scopes_by_tenant(self):
        """_call_webhook_for_creative_status Creative access must be tenant-scoped.

        Creative access was migrated to CreativeRepository (salesagent-p6i),
        which enforces tenant_id on every query by construction.
        Verify the repository is used rather than raw select() calls.
        """
        source_path = ROOT / "src/admin/blueprints/creatives.py"
        tree = ast.parse(source_path.read_text())

        func_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_call_webhook_for_creative_status"
            ):
                func_node = node
                break

        assert func_node is not None
        source_text = ast.get_source_segment(source_path.read_text(), func_node)
        assert "AdminCreativeUoW" in source_text, (
            "_call_webhook_for_creative_status must use AdminCreativeUoW for tenant-scoped access"
        )

        # Verify no raw select(Creative) calls bypass the repository
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "_call_webhook_for_creative_status",
        )
        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert not creative_selects, (
            f"_call_webhook_for_creative_status should use CreativeRepository, not raw select(Creative). "
            f"Found {len(creative_selects)} raw query(ies)."
        )

    def test_approve_creative_creative_query_scopes_by_tenant(self):
        """approve_creative() Creative access must be tenant-scoped.

        Creative access was migrated to CreativeRepository (salesagent-p6i),
        which enforces tenant_id on every query by construction.
        Verify no raw select(Creative) calls bypass the repository.
        """
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "approve_creative",
        )

        creative_selects = [s for s in selects if s["model"] in ("Creative", "CreativeModel")]
        assert not creative_selects, (
            f"approve_creative() should use CreativeRepository, not raw select(Creative). "
            f"Found {len(creative_selects)} raw query(ies)."
        )

    def test_review_creatives_mediabuy_queries_scope_by_tenant(self):
        """review_creatives() MediaBuy queries must be tenant-scoped.

        MediaBuy access was migrated to MediaBuyRepository (salesagent-72dh),
        which enforces tenant_id on every query by construction.
        Verify the repository is used rather than raw select() calls.
        """
        source_path = ROOT / "src/admin/blueprints/creatives.py"
        tree = ast.parse(source_path.read_text())

        # Find the review_creatives function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "review_creatives":
                func_node = node
                break

        assert func_node is not None, "review_creatives function not found"

        # Verify tenant-scoped media buy access (via MediaBuyRepository or AdminCreativeUoW)
        source_text = ast.get_source_segment(source_path.read_text(), func_node)
        assert "MediaBuyRepository" in source_text or "AdminCreativeUoW" in source_text, (
            "review_creatives() must use MediaBuyRepository or AdminCreativeUoW for tenant-scoped MediaBuy access"
        )

        # Verify no raw select(MediaBuy) calls bypass the repository
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "review_creatives",
        )
        mediabuy_selects = [s for s in selects if s["model"] in ("MediaBuy", "MediaBuyModel")]
        assert not mediabuy_selects, (
            f"review_creatives() should use MediaBuyRepository, not raw select(MediaBuy). "
            f"Found {len(mediabuy_selects)} raw query(ies)."
        )

    def test_review_creatives_product_query_scopes_by_tenant(self):
        """review_creatives() Product access must be tenant-scoped.

        Product access was migrated to ProductRepository (salesagent-p6i),
        which enforces tenant_id on every query by construction.
        Verify no raw select(Product) calls bypass the repository.
        """
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "review_creatives",
        )

        product_selects = [s for s in selects if s["model"] == "Product"]
        assert not product_selects, (
            f"review_creatives() should use ProductRepository, not raw select(Product). "
            f"Found {len(product_selects)} raw query(ies)."
        )

    def test_approve_creative_review_query_scopes_by_tenant(self):
        """approve_creative() CreativeReview access must be tenant-scoped.

        CreativeReview access was migrated to CreativeRepository.get_prior_ai_review()
        (salesagent-p6i), which enforces tenant_id by construction.
        Verify no raw select(CreativeReview) calls bypass the repository.
        """
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "approve_creative",
        )

        review_selects = [s for s in selects if s["model"] == "CreativeReview"]
        assert not review_selects, (
            f"approve_creative() should use CreativeRepository.get_prior_ai_review(), not raw select(CreativeReview). "
            f"Found {len(review_selects)} raw query(ies)."
        )

    def test_reject_creative_review_query_scopes_by_tenant(self):
        """reject_creative() CreativeReview access must be tenant-scoped.

        CreativeReview access was migrated to CreativeRepository.get_prior_ai_review()
        (salesagent-p6i), which enforces tenant_id by construction.
        Verify no raw select(CreativeReview) calls bypass the repository.
        """
        selects = _extract_select_calls(
            "src/admin/blueprints/creatives.py",
            "reject_creative",
        )

        review_selects = [s for s in selects if s["model"] == "CreativeReview"]
        assert not review_selects, (
            f"reject_creative() should use CreativeRepository.get_prior_ai_review(), not raw select(CreativeReview). "
            f"Found {len(review_selects)} raw query(ies)."
        )

    # NOTE: _get_media_buy_delivery_impl MediaPackage query is NOT tested here because
    # MediaPackage has no tenant_id column. Tenant isolation via media_buy_id FK is sufficient.

    def test_get_pricing_options_scopes_by_tenant(self):
        """ProductRepository.get_all_pricing_options PricingOption query must include tenant_id."""
        selects = _extract_select_calls(
            "src/core/database/repositories/product.py",
            "get_all_pricing_options",
        )

        pricing_selects = [s for s in selects if s["model"] == "PricingOption"]
        assert pricing_selects, "Expected at least one PricingOption select() call"

        for s in pricing_selects:
            assert s["has_tenant_filter"], (
                f"PricingOption query at product.py:{s['lineno']} is missing tenant_id filter. (salesagent-gcjx)"
            )
