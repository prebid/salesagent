"""Live MCP, A2A and REST delivery, including the real bearer challenge."""

import pytest

from tests.factories import PricingOptionFactory, ProductFactory
from tests.harness.account_list import AccountListEnv
from tests.harness.client import AdCPTestClient
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport
from tests.helpers.transport_parity import _assert_error_equivalent, _assert_success_equivalent


@pytest.mark.parametrize("transport", [Transport.E2E_REST, Transport.E2E_MCP, Transport.E2E_A2A])
def test_success_and_error_equivalence(live_e2e_config, transport):
    with ProductEnv(e2e_config=live_e2e_config) as env:
        tenant, _principal = env.setup_default_data()
        product = ProductFactory(tenant=tenant)
        PricingOptionFactory(product=product)
        via = env.call_via(transport, brief="video ads")
        client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, transport)
        for result in (via, client_result):
            assert result.is_success, result
            assert product.product_id in {row["product_id"] for row in result.wire_response["products"]}
        _assert_success_equivalent(via, client_result)

    with AccountListEnv(e2e_config=live_e2e_config) as env:
        via = env.call_via(transport, credential=env.credential(token=None))
        client_result = AdCPTestClient(env).call("list_accounts", {}, transport, credential=env.credential(token=None))
        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable", check_auth=True)
