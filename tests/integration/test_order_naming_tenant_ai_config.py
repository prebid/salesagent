"""Order naming runs on the seller's own AI configuration, wherever the seller stored it.

``get_adapter`` hands the adapter the tenant's resolved AI configuration
(``TenantContext.ai_config``), the way it hands over the naming templates, and the adapter
passes it to ``build_order_name_context``. The adapters used to read the tenant row
themselves and hand over ``gemini_api_key`` alone, so a seller who configured AI in the
Admin UI (``tenants.ai_config``) got no ``{auto_name}``: the naming fell back to the brand
name.

Three sites build an order name, and each is graded here through its own code path, every
one of them reached through ``get_adapter``: the mock adapter, the GAM adapter's automatic
mode (the name sent to GAM's ``createOrders``), and the GAM adapter's manual-approval mode
(the name written to the workflow step a human trafficker reads). The model is asked only
when the template names ``{auto_name}``; the default template does not, and asks nothing.

The platform AI credential is removed, so the seller's own configuration is the only one
that can make the AI run. The vendor systems are the only things stubbed: the model call,
which this test cannot make, and the GAM SOAP client, which it cannot reach.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.base import AdapterCreateRequest
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schemas import FormatId, MediaPackage
from src.core.tenant_context import TenantContext
from tests.factories import PrincipalFactory
from tests.helpers.gam_client import stub_gam_client_manager
from tests.helpers.settings_injection import PlatformAICredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_AI_NAME = "Seller AI Order Name"
_PRODUCT_ID = "prod_naming"
_START = datetime(2026, 12, 1, tzinfo=UTC)
_END = datetime(2026, 12, 31, tzinfo=UTC)

#: The two places a seller can store its AI configuration.
_SELLER_AI = pytest.mark.parametrize(
    ("ai_config", "gemini_api_key"),
    [
        ({"provider": "anthropic", "model": "claude-x", "api_key": "tenant-owned-naming-key"}, None),
        (None, "tenant-owned-legacy-key"),
    ],
    ids=["ai_config", "legacy_gemini_api_key"],
)


def _seller_tenant(session, ai_config, gemini_api_key, order_name_template="{auto_name}"):
    """A tenant naming orders ``order_name_template``, with its AI configuration in one place."""
    from tests.factories import TenantFactory

    tenant = TenantFactory(order_name_template=order_name_template, ai_config=ai_config)
    tenant.gemini_api_key = gemini_api_key
    session.commit()
    return tenant


def _adapter_for(tenant, principal):
    """The adapter production builds for this caller: ``get_adapter`` on the loaded identity."""
    identity = PrincipalFactory.make_identity(
        tenant=TenantContext.from_orm_model(tenant),
        principal_id=principal.principal_id,
        platform_mappings=principal.platform_mappings,
    )
    return get_adapter(identity)


def _package() -> MediaPackage:
    """One package in a reference format, so the GAM line-item path resolves its size.

    The format resolves from the checked-in reference catalog the test settings serve
    in place of a creative agent (``ReferenceFormatsRegistry``), so no agent is dialled
    and nothing is patched to make the format known.
    """
    return MediaPackage(
        package_id="pkg_naming",
        name="Naming Package",
        delivery_type="non_guaranteed",
        cpm=10.0,
        impressions=1000,
        format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")],
        product_id=_PRODUCT_ID,
    )


def _request() -> AdapterCreateRequest:
    return AdapterCreateRequest(brand={"domain": "naming.example.com"}, total_budget=Decimal("1000.00"))


@contextmanager
def _sellers_ai_only() -> Iterator[AsyncMock]:
    """No platform credential, and the vendor model call answering ``_AI_NAME``."""
    with (
        PlatformAICredential(),
        patch(
            "src.services.ai.agents.naming_agent.generate_name_async", new=AsyncMock(return_value=_AI_NAME)
        ) as model_call,
    ):
        yield model_call


def _create_mock_buy(tenant):
    principal = PrincipalFactory(tenant=tenant)
    adapter = _adapter_for(tenant, principal)
    with _sellers_ai_only() as model_call:
        result = adapter._create_media_buy_immediate(_request(), [_package()], _START, _END)
    return adapter._media_buys[result.media_buy_id]["name"], model_call


@_SELLER_AI
def test_mock_adapter_names_the_order_with_the_sellers_own_ai(bound_factory_session, ai_config, gemini_api_key):
    name, model_call = _create_mock_buy(_seller_tenant(bound_factory_session, ai_config, gemini_api_key))

    assert model_call.await_count == 1, "order naming never asked the seller's AI"
    assert name == _AI_NAME


@_SELLER_AI
def test_a_template_without_auto_name_never_asks_the_model(bound_factory_session, ai_config, gemini_api_key):
    """The default template has no ``{auto_name}``: the seller's AI is configured and stays unasked.

    Naming runs on the create_media_buy write path and the model call blocks it, so a name
    the template then discards cost the seller a model call and the buyer the wait.
    """
    tenant = _seller_tenant(
        bound_factory_session,
        ai_config,
        gemini_api_key,
        order_name_template="{campaign_name|brand_name} - {media_buy_id} - {date_range}",
    )

    name, model_call = _create_mock_buy(tenant)

    assert model_call.await_count == 0, "the model was asked for a name the template does not use"
    assert name.startswith("naming.example.com - "), name


def _create_gam_buy(session, ai_config, gemini_api_key, *, manual_approval: bool):
    """Drive ``GoogleAdManager.create_media_buy`` for a seller tenant, against a stub GAM client.

    The product is a real row carrying inventory targeting, because ``create_media_buy``
    reads it before naming the order and refuses a product without ad units. Its pricing
    option is a real row too, projected into ``package_pricing_info`` by the same helper the
    create path uses, because the line items GAM gets after the order need a rate. The
    adapter is the one ``get_adapter`` builds from the tenant's ``AdapterConfig`` row, which
    carries the GAM order-name template.

    Returns the adapter's result, the model-call mock, and the GAM client stand-in, whose
    ``OrderService.createOrders`` records what the adapter sent to GAM.
    """
    from src.core.helpers.pricing_helpers import pricing_info_for
    from tests.factories import AdapterConfigFactory, PricingOptionFactory, ProductFactory

    tenant = _seller_tenant(session, ai_config, gemini_api_key)
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token="test_token",
        gam_trafficker_id="456",
        gam_manual_approval_required=manual_approval,
        gam_order_name_template="{auto_name}",
    )
    principal = PrincipalFactory(tenant=tenant, platform_mappings={"google_ad_manager": {"advertiser_id": "789"}})
    product = ProductFactory(
        tenant=tenant,
        product_id=_PRODUCT_ID,
        delivery_type="non_guaranteed",
        implementation_config={"targeted_ad_unit_ids": ["123456"]},
    )
    pricing_option = PricingOptionFactory(
        product=product, pricing_model="cpm", rate=10.0, currency="USD", is_fixed=True
    )
    session.commit()
    package = _package()

    gam_client = stub_gam_client_manager()
    with patch("src.adapters.google_ad_manager.GAMClientManager", return_value=gam_client):
        adapter = _adapter_for(tenant, principal)

    with _sellers_ai_only() as model_call:
        result = adapter.create_media_buy(
            _request(),
            [package],
            _START,
            _END,
            package_pricing_info={package.package_id: pricing_info_for(pricing_option)},
        )

    return tenant, result, model_call, gam_client


def _orders_sent_to_gam(gam_client: MagicMock) -> list[dict]:
    order_service = gam_client.get_service("OrderService")
    return [order for call in order_service.createOrders.call_args_list for order in call.args[0]]


@_SELLER_AI
def test_gam_adapter_names_the_gam_order_with_the_sellers_own_ai(bound_factory_session, ai_config, gemini_api_key):
    _tenant, _result, model_call, gam_client = _create_gam_buy(
        bound_factory_session, ai_config, gemini_api_key, manual_approval=False
    )

    assert model_call.await_count == 1, "order naming never asked the seller's AI"
    orders = _orders_sent_to_gam(gam_client)
    assert len(orders) == 1, f"expected one order created in GAM, got {orders!r}"
    # The adapter appends a uniqueness suffix ``[mb_gam_<id>]`` to the templated name.
    assert orders[0]["name"].startswith(f"{_AI_NAME} [mb_gam_"), orders[0]["name"]


@_SELLER_AI
def test_gam_manual_approval_names_the_workflow_order_with_the_sellers_own_ai(
    bound_factory_session, ai_config, gemini_api_key
):
    from src.core.database.repositories.uow import WorkflowUoW

    tenant, result, model_call, gam_client = _create_gam_buy(
        bound_factory_session, ai_config, gemini_api_key, manual_approval=True
    )

    assert model_call.await_count == 1, "order naming never asked the seller's AI"
    assert _orders_sent_to_gam(gam_client) == [], "manual approval must not create the order in GAM"
    with WorkflowUoW(tenant.tenant_id) as uow:
        assert uow.workflows is not None
        mapping = uow.workflows.get_latest_mapping_for_object("media_buy", result.media_buy_id)
        assert mapping is not None, f"no workflow step recorded for {result.media_buy_id}"
        step = uow.workflows.get_by_step_id(mapping.step_id)
        assert step is not None
        assert step.transaction_details["campaign_name"] == _AI_NAME
        assert step.request_data["campaign_name"] == _AI_NAME
