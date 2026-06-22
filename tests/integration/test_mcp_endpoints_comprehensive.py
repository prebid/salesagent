"""Schema validation tests retained from the legacy MCP endpoint suite.

Server-backed MCP endpoint coverage lives in ``tests/e2e/`` (``live_server`` /
``docker_services_e2e``). The former ``@pytest.mark.requires_server`` integration
tests targeted a manual localhost:8080/8100 server and never executed in CI or tox.
See #1233 D11 and ``docs/development/ci-pipeline.md``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.core.schemas import CreateMediaBuyRequest
from tests.helpers.adcp_factories import create_test_package_request


@pytest.mark.requires_db
def test_schema_adcp_format() -> None:
    """AdCP schema validates create-media-buy requests per spec."""
    request = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        po_number="PO-V24-67890",
        packages=[
            create_test_package_request(product_id="prod_1", budget=6000.0, pricing_option_id="default"),
            create_test_package_request(product_id="prod_2", budget=4000.0, pricing_option_id="default"),
        ],
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(days=30),
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
    )

    assert len(request.packages) == 2
    assert request.get_total_budget() == 10000.0
    product_ids = request.get_product_ids()
    assert product_ids == ["prod_1", "prod_2"]
