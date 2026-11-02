"""A stored expires_at reaches the buyer, and a NULL reporting_capabilities row still converts.

``expires_at`` is a pinned field (core/product.json /properties/expires_at, AdCP 3.1.1). The
strip that hid it is gone; ``convert_product_model_to_schema`` is the one producer of a wire
``Product`` from a stored row, so the copy there is what puts the value on the wire
(salesagent-3cs7o.15, item 1). The same edge supplies the pin-required
``reporting_capabilities`` for a row that stores NULL, so the wire model inherits the field
as required and fabricates nothing (salesagent-3cs7o.14, item 1).
"""

from datetime import UTC, datetime

from src.core.product_conversion import convert_product_model_to_schema
from src.core.schemas import GetProductsResponse
from src.core.tools._wire import to_wire
from tests.factories.product import PricingOptionFactory, ProductFactory


def _row(**overrides):
    row = ProductFactory.build(**overrides)
    row.pricing_options = [PricingOptionFactory.build(product=row)]
    return row


def _served(row) -> dict:
    """The product as the buyer receives it: converted, then through the wire function."""
    (product,) = to_wire(GetProductsResponse(products=[convert_product_model_to_schema(row)]))["products"]
    return product


def test_a_stored_expires_at_is_served_as_an_iso_string() -> None:
    served = _served(_row(expires_at=datetime(2030, 1, 1, tzinfo=UTC)))
    assert served["expires_at"] == "2030-01-01T00:00:00Z"


def test_a_null_expires_at_is_omitted() -> None:
    served = _served(_row(expires_at=None))
    assert "expires_at" not in served


def test_a_null_reporting_capabilities_row_is_served_with_the_edge_default() -> None:
    served = _served(_row(reporting_capabilities=None))
    assert served["reporting_capabilities"]["available_reporting_frequencies"] == ["daily"]
