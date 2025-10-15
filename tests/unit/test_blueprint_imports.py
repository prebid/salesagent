"""Comprehensive import validation tests for all admin blueprints.

These tests validate that all required imports are present in blueprint modules,
preventing NameErrors that would only surface when specific code paths execute.

This approach catches import issues at test time rather than runtime, improving
reliability and catching bugs before they reach production.
"""

import pytest


class TestBlueprintSQLAlchemyImports:
    """Test SQLAlchemy imports across all blueprints."""

    def test_api_blueprint_imports(self):
        """Validate api.py has required SQLAlchemy imports."""
        from src.admin.blueprints import api

        assert hasattr(api, "func"), "Missing required import: func from sqlalchemy"
        assert hasattr(api, "text"), "Missing required import: text from sqlalchemy"

    def test_core_blueprint_imports(self):
        """Validate core.py has required SQLAlchemy imports."""
        from src.admin.blueprints import core

        assert hasattr(core, "text"), "Missing required import: text from sqlalchemy"

    def test_creatives_blueprint_imports(self):
        """Validate creatives.py has required SQLAlchemy imports."""
        from src.admin.blueprints import creatives

        # creatives.py no longer uses or_ after CreativeFormat table was dropped
        assert hasattr(creatives, "select"), "Missing required import: select from sqlalchemy"

    def test_inventory_blueprint_imports(self):
        """Validate inventory.py has required SQLAlchemy imports."""
        from src.admin.blueprints import inventory

        # These imports were missing and caused production bug
        assert hasattr(inventory, "or_"), "Missing required import: or_ from sqlalchemy"
        assert hasattr(inventory, "String"), "Missing required import: String from sqlalchemy"
        assert hasattr(inventory, "func"), "Missing required import: func from sqlalchemy"

    def test_public_blueprint_imports(self):
        """Validate public.py has required SQLAlchemy imports."""
        from src.admin.blueprints import public

        assert hasattr(public, "or_"), "Missing required import: or_ from sqlalchemy"


class TestBlueprintBasicImports:
    """Test that all blueprints can be imported without errors."""

    @pytest.mark.parametrize(
        "blueprint_name",
        [
            "activity_stream",
            "adapters",
            "api",
            "auth",
            "authorized_properties",
            "core",
            "creatives",
            "gam",
            "inventory",
            "mcp_test",
            "operations",
            "policy",
            "principals",
            "products",
            "public",
            "schemas",
            "settings",
            "tenants",
            "users",
            "workflows",
        ],
    )
    def test_blueprint_imports_successfully(self, blueprint_name):
        """Test that each blueprint can be imported without errors.

        This catches:
        - Missing dependencies
        - Syntax errors
        - Import errors from dependencies
        - Circular import issues
        """
        module = __import__(f"src.admin.blueprints.{blueprint_name}", fromlist=[blueprint_name])
        assert module is not None, f"Failed to import blueprint: {blueprint_name}"


class TestBlueprintFlaskIntegration:
    """Test that all blueprints properly define Flask blueprint objects."""

    @pytest.mark.parametrize(
        "blueprint_name,expected_bp_name",
        [
            ("activity_stream", "activity_stream_bp"),
            ("adapters", "adapters_bp"),
            ("api", "api_bp"),
            ("auth", "auth_bp"),
            ("authorized_properties", "authorized_properties_bp"),
            ("core", "core_bp"),
            ("creatives", "creatives_bp"),
            ("gam", "gam_bp"),
            ("inventory", "inventory_bp"),
            ("mcp_test", "mcp_test_bp"),
            ("operations", "operations_bp"),
            ("policy", "policy_bp"),
            ("principals", "principals_bp"),
            ("products", "products_bp"),
            ("public", "public_bp"),
            ("schemas", "schemas_bp"),
            ("settings", "settings_bp"),
            ("tenants", "tenants_bp"),
            ("users", "users_bp"),
            ("workflows", "workflows_bp"),
        ],
    )
    def test_blueprint_object_exists(self, blueprint_name, expected_bp_name):
        """Test that each blueprint module exports a Blueprint object.

        This ensures blueprints can be properly registered with Flask app.
        """
        module = __import__(f"src.admin.blueprints.{blueprint_name}", fromlist=[expected_bp_name])
        assert hasattr(module, expected_bp_name), f"Blueprint {blueprint_name} missing {expected_bp_name}"

        blueprint_obj = getattr(module, expected_bp_name)
        from flask import Blueprint

        assert isinstance(blueprint_obj, Blueprint), f"{expected_bp_name} is not a Flask Blueprint instance"


class TestCriticalAPIEndpoints:
    """Test that critical API endpoint functions exist and are callable."""

    def test_inventory_list_endpoint_exists(self):
        """Test inventory-list endpoint that had the import bug."""
        from src.admin.blueprints.inventory import get_inventory_list

        assert callable(get_inventory_list), "get_inventory_list should be callable"

    def test_api_health_endpoint_exists(self):
        """Test health check endpoint exists."""
        from src.admin.blueprints.api import api_health

        assert callable(api_health), "api_health should be callable"

    def test_auth_login_endpoint_exists(self):
        """Test login endpoint exists."""
        from src.admin.blueprints.auth import login

        assert callable(login), "login should be callable"


class TestImportRegressionPrevention:
    """Document and prevent regression of known import issues."""

    def test_inventory_search_imports_fixed(self):
        """Regression test: Inventory search requires or_ and String.

        Original bug: Missing imports caused NameError when search was used.
        Symptoms:
        - 404 on /api/tenant/{id}/inventory-list with search parameter
        - "Failed to fetch inventory" in UI
        - Unable to configure GAM products with inventory targeting

        This test ensures the fix stays in place.
        """
        # Verify these are the actual SQLAlchemy objects
        from sqlalchemy import String as SQLAlchemyString
        from sqlalchemy import func as SQLAlchemyFunc
        from sqlalchemy import or_ as SQLAlchemyOr

        from src.admin.blueprints.inventory import String, func, or_

        assert or_ is SQLAlchemyOr, "or_ import is not the correct SQLAlchemy function"
        assert String is SQLAlchemyString, "String import is not the correct SQLAlchemy type"
        assert func is SQLAlchemyFunc, "func import is not the correct SQLAlchemy module"

    def test_other_blueprints_with_or_operator(self):
        """Ensure other blueprints using or_ have it properly imported."""
        # creatives.py no longer uses or_ after CreativeFormat table was dropped

        # public.py uses or_
        from sqlalchemy import or_ as SQLAlchemyOr
        from src.admin.blueprints.public import or_ as public_or

        assert public_or is SQLAlchemyOr
