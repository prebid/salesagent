"""Unit test to verify SQLAlchemy imports in inventory blueprint.

This test specifically validates that the imports required for the inventory-list
search functionality are present, which would prevent the NameError that occurred
in production.
"""


def test_inventory_blueprint_has_required_imports():
    """Test that inventory.py imports or_ and String from SQLAlchemy.

    This test validates the fix for the bug where missing imports caused:
    - 404 error when loading inventory
    - "Failed to fetch inventory" in UI
    - NameError: name 'or_' is not defined (in production)

    The bug would have been caught immediately if this test existed, demonstrating
    the value of import validation tests for critical API endpoints.
    """
    # Import the module - this will fail if the imports are missing and code tries to use them
    from src.admin.routers import inventory

    # Verify the required SQLAlchemy functions are importable in the module's context
    assert hasattr(inventory, "or_"), "Missing required import: or_ from sqlalchemy"
    assert hasattr(inventory, "String"), "Missing required import: String from sqlalchemy"
    assert hasattr(inventory, "func"), "Missing required import: func from sqlalchemy"


def test_inventory_list_function_exists():
    """Test that the get_inventory_list function exists and is callable."""
    from src.admin.routers.inventory import get_inventory_list

    assert callable(get_inventory_list), "get_inventory_list should be a callable function"


def test_inventory_search_documentation():
    """Documentation: How missing imports caused production issues.

    Without the or_ and String imports, calling the search functionality
    would raise: NameError: name 'or_' is not defined

    This is exactly what happened in production, causing:
    - 404 errors on /api/tenant/{id}/inventory-list
    - "Failed to fetch inventory" error in UI
    - Unable to configure GAM products

    This test exists to document the importance of import validation.
    """
    # Test passes because imports are now correct
    from src.admin.routers.inventory import String, func, or_

    assert or_ is not None
    assert String is not None
    assert func is not None
