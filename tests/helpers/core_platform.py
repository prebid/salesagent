"""Helpers for tests that exercise the greenfield core/ platform glue."""

from unittest.mock import MagicMock

from adcp.types import GetProductsResponse

from tests.helpers.adcp_factories import create_test_product


def make_get_products_response(
    product_id: str,
    *,
    name: str = "Test Product",
    description: str = "A product for testing",
) -> GetProductsResponse:
    """Build an AdCP product response suitable for wire-shape projection tests."""
    return GetProductsResponse(
        products=[
            create_test_product(
                product_id=product_id,
                name=name,
                description=description,
                delivery_type="non_guaranteed",
            )
        ],
        errors=None,
        context=None,
    )


def make_active_tenant_session() -> MagicMock:
    """Build a mocked DB session whose tenant lookup returns an active tenant."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalars.return_value.first.return_value = MagicMock(is_active=True)
    return session
