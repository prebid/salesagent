"""Live MCP, A2A and REST delivery, including the real bearer challenge."""

import pytest

from tests.factories import PricingOptionFactory, ProductFactory
from tests.harness.account_list import AccountListEnv
from tests.harness.client import AdCPTestClient
from tests.harness.product import ProductEnv
from tests.harness.transport import E2EConfig, Transport
from tests.helpers.transport_parity import _assert_error_equivalent, _assert_success_equivalent


@pytest.fixture(scope="module")
def e2e_live_config():
    config = E2EConfig.from_environment()
    if config is None:
        pytest.skip("No E2E stack configured; run with make test-stack-up or run_all_tests.sh")
    return config


@pytest.mark.parametrize("transport", [Transport.E2E_REST, Transport.E2E_MCP, Transport.E2E_A2A])
def test_success_and_error_equivalence(e2e_live_config, transport):
    with ProductEnv(e2e_config=e2e_live_config) as env:
        tenant, _principal = env.setup_default_data()
        product = ProductFactory(tenant=tenant)
        PricingOptionFactory(product=product)
        via = env.call_via(transport, tool_name="get_products", brief="video ads")
        client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, transport)
        _assert_success_equivalent(via, client_result)

    with AccountListEnv(e2e_config=e2e_live_config) as env:
        via = env.call_via(transport, tool_name="list_accounts", credential=env.credential(token=None))
        client_result = AdCPTestClient(env).call("list_accounts", {}, transport, credential=env.credential(token=None))
        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable", check_auth=True)
