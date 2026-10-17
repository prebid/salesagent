"""Real product generation with a signals agent served over TLS and MCP."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from tests.factories import PricingOptionFactory, ProductFactory, SignalsAgentFactory, TenantFactory
from tests.harness.egress import EgressHatchMixin
from tests.harness.product import ProductEnv
from tests.harness.publisher_authorization import PUBLISHER_ORIGIN_HOST
from tests.helpers.egress_hatches import egress_hatch_env
from tests.helpers.local_mcp_origin import run_mcp_origin
from tests.helpers.tls_material import load_gen_test_tls


class DynamicProductEnv(EgressHatchMixin, ProductEnv):
    """No product service, registry, converter or repository is substituted."""

    EXTERNAL_PATCHES = {"ranking_factory": ProductEnv.EXTERNAL_PATCHES["ranking_factory"]}
    ASYNC_PATCHES = set()

    def _configure_mocks(self) -> None:
        self.set_ranking_disabled()

    def _enter_pre(self) -> None:
        super()._enter_pre()
        tls = load_gen_test_tls()
        tls.ensure_test_tls()
        trust = patch.dict(os.environ, {"SSL_CERT_FILE": str(tls.COMBINED_CERT), **egress_hatch_env(private=True)})
        trust.start()
        self._guard("signals_tls_trust", trust.stop)
        self.set_egress_hatches(private=True)
        self._signals = []
        origin_context = run_mcp_origin(
            tools={"get_signals": self._get_signals},
            certfile=tls.SERVER_CERT,
            keyfile=tls.SERVER_KEY,
            listen_host="0.0.0.0" if self.is_e2e else "127.0.0.1",
            public_host=PUBLISHER_ORIGIN_HOST if self.is_e2e else "127.0.0.1",
        )
        self.origin = origin_context.__enter__()
        self._guard("signals_origin", lambda: origin_context.__exit__(None, None, None))

    def _get_signals(self, signal_spec: str, discovery_mode: str) -> dict:
        return {"signals": self._signals}

    def seed_catalog(
        self, *, legacy: bool = False, restricted: bool = False, priced: bool = False, invalid_signal: bool = False
    ) -> None:
        from src.services.dynamic_products import generate_variant_id

        tenant, _ = self.setup_default_data(ad_server="mock")
        static = ProductFactory(tenant=tenant, product_id="static", name="Static inventory")
        PricingOptionFactory(product=static, pricing_option_id="static-rate", rate=Decimal("4.25"))
        template = ProductFactory(
            tenant=tenant,
            product_id="template",
            name="Audience inventory",
            is_dynamic=True,
            signals_agent_ids=[self.origin.base_url],
            allowed_principal_ids=["another-buyer"] if restricted else None,
        )
        PricingOptionFactory(product=template, pricing_option_id="negotiated-cpm", rate=Decimal("7.25"))
        PricingOptionFactory(
            product=template, pricing_option_id="click-rate", pricing_model="cpc", rate=Decimal("1.35")
        )
        SignalsAgentFactory(tenant=tenant, agent_url=self.origin.base_url)
        key = {"type": "key_value", "key": "audience", "value": "premium"}
        self.variant_id = generate_variant_id("template", key)
        self._signals = [
            {
                "name": "Premium audience",
                "description": "Premium readers",
                "signal_agent_segment_id": "premium",
                "signal_type": "marketplace",
                "deployments": [
                    {"type": "agent", "agent_url": tenant.agent_url, "is_live": True, "activation_key": key}
                ],
            }
        ]
        from adcp.types import GetSignalsResponse

        GetSignalsResponse.model_validate({"signals": self._signals})
        if invalid_signal:
            invalid = deepcopy(self._signals[0])
            invalid["name"] = "x" * 300
            invalid["deployments"][0]["activation_key"]["value"] = "invalid"
            self._signals.insert(0, invalid)
        if legacy or priced:
            saved = ProductFactory(
                tenant=tenant,
                name="Saved audience inventory",
                product_id=self.variant_id,
                is_dynamic_variant=True,
                parent_product_id="template",
                activation_key=key,
            )
            if priced:
                PricingOptionFactory(product=saved, pricing_option_id="buyer-contract", rate=Decimal("12.50"))
        other = TenantFactory()
        ProductFactory(
            tenant=other, product_id="other-template", is_dynamic=True, signals_agent_ids=[self.origin.base_url]
        )

    def variant_pricing_ids(self) -> list[str]:
        from src.core.database.repositories.product import ProductRepository

        self._session.expire_all()
        variant = ProductRepository(self._session, self._tenant_id).get_by_id_with_pricing(self.variant_id)
        return sorted(option.pricing_option_id for option in variant.pricing_options)

    def restrict_template(self) -> None:
        from src.core.database.repositories.product import ProductRepository

        repository = ProductRepository(self.get_session(), self._tenant_id)
        repository.update_fields("template", allowed_principal_ids=["another-buyer"])
        self.get_session()
