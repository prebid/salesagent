"""Regression (PR1399 R3): GAM auto-config must normalize a guaranteed product's
delivery_type to its enum *value*, not str(Enum).

src/core/tools/media_buy_create.py auto-generates a default GAM
implementation_config when a product is missing one. It derives the
``delivery_type`` string and passes it to
``GAMProductConfigService.generate_default_config``, which branches on
``delivery_type == "guaranteed"``. ``Product.delivery_type`` is a plain
``DeliveryType`` enum, so ``str(delivery_type)`` yields
``"DeliveryType.guaranteed"`` -- the equality fails and a GUARANTEED product
silently gets the NON_GUARANTEED (PRICE_PRIORITY) config. The fix routes the
value through ``enum_value()``.

This drives the real ``_create_media_buy_impl`` GAM auto-config path and calls
through to the real config generator; only the captured arguments/output are
asserted.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import CreateMediaBuyRequest
from src.services.gam_product_config_service import GAMProductConfigService
from tests.factories import PricingOptionFactory, ProductFactory
from tests.factories.core import PropertyTagFactory
from tests.harness.media_buy_create import MediaBuyCreateEnv

_TENANT_ID = "gam-autocfg-tenant"
_PRINCIPAL_ID = "gam-autocfg-principal"


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _make_request(product_id: str) -> CreateMediaBuyRequest:
    return CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        start_time=_future(1),
        end_time=_future(8),
        packages=[{"product_id": product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        idempotency_key=f"gam-autoconfig-{product_id}",
    )


@pytest.mark.requires_db
class TestGAMAutoConfigDeliveryTypeNormalization:
    def test_guaranteed_product_autoconfig_receives_normalized_delivery_type(self, integration_db):
        """A guaranteed product missing implementation_config must auto-generate a
        GUARANTEED (STANDARD line item) GAM config, not the non_guaranteed default."""
        real_generate = GAMProductConfigService.generate_default_config
        captured: dict = {}

        def _spy(delivery_type, formats=None):
            config = real_generate(delivery_type, formats=formats)
            captured["delivery_type"] = delivery_type
            captured["config"] = config
            return config

        with MediaBuyCreateEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID, human_review_required=False) as env:
            tenant, _principal = env.setup_default_data()
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_guar",
                delivery_type="guaranteed",
                implementation_config=None,  # force the auto-config branch
                property_tags=["all_inventory"],
            )
            PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)

            # Enter the GAM auto-config branch (guarded by adapter class name).
            env.mock["adapter"].return_value.__class__ = GoogleAdManager

            with patch.object(GAMProductConfigService, "generate_default_config", side_effect=_spy):
                env.call_impl(req=_make_request("prod_guar"))

        assert captured, "GAM auto-config branch did not run -- generate_default_config was never called"
        # The bug passes str(DeliveryType.guaranteed) == 'DeliveryType.guaranteed'.
        assert captured["delivery_type"] == "guaranteed"
        # ... which makes generate_default_config fall through to non_guaranteed.
        assert captured["config"]["line_item_type"] == "STANDARD"
