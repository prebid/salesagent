"""Test that media_buy_create passes tenant ORM model, not manual dicts.

Bug: execute_approved_media_buy() manually constructs a partial tenant dict with only
5 fields instead of passing the Tenant ORM model directly. Downstream code should
receive the typed model, not a lossy dict.
"""

import ast
import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestMediaBuyTenantContext:
    """Verify execute_approved_media_buy passes tenant ORM model to downstream code."""

    def test_no_manual_tenant_dict_construction(self):
        """execute_approved_media_buy should not manually construct tenant dicts.

        Manual dict construction (tenant_dict = {"tenant_id": ..., "name": ...}) misses
        fields and violates the architecture rule: logic layer works with ORM models.
        """
        source = (_PROJECT_ROOT / "src" / "core" / "tools" / "media_buy_create.py").read_text()

        tree = ast.parse(source)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "execute_approved_media_buy":
                func_node = node
                break

        assert func_node is not None, "execute_approved_media_buy function not found"

        func_source = ast.get_source_segment(source, func_node)
        assert func_source is not None

        # The bug pattern: manually building a dict with tenant fields
        assert "tenant_dict = {" not in func_source, (
            "execute_approved_media_buy manually constructs tenant_dict instead of "
            "passing tenant_obj (ORM model) directly"
        )

    def test_passes_tenant_obj_to_adapter_calls(self):
        """Downstream adapter calls should receive tenant_obj, not tenant_dict."""
        source = (_PROJECT_ROOT / "src" / "core" / "tools" / "media_buy_create.py").read_text()

        tree = ast.parse(source)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "execute_approved_media_buy":
                func_node = node
                break

        assert func_node is not None
        func_source = ast.get_source_segment(source, func_node)
        assert func_source is not None

        # No references to tenant_dict anywhere in the function
        assert "tenant_dict" not in func_source, (
            "execute_approved_media_buy still references tenant_dict — should use tenant_obj (ORM model) everywhere"
        )

    def test_get_adapter_handles_orm_model(self):
        """get_adapter should accept Tenant ORM model via attribute access."""
        source = (_PROJECT_ROOT / "src" / "core" / "helpers" / "adapter_helpers.py").read_text()

        # get_adapter should have ORM model branch (not just dict access)
        assert "tenant.tenant_id" in source, "get_adapter does not support Tenant ORM model attribute access"
        assert "tenant.ad_server" in source, (
            "get_adapter does not support Tenant ORM model attribute access for ad_server"
        )
