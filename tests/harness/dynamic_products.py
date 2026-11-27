"""Real product generation with a signals agent served over TLS and MCP."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlsplit

from tests.factories import PricingOptionFactory, ProductFactory, SignalFactory, SignalsAgentFactory, TenantFactory
from tests.harness.egress import EgressHatchMixin
from tests.harness.product import ProductEnv
from tests.helpers.local_http_origin import guarded_origin
from tests.helpers.local_mcp_origin import run_mcp_origin
from tests.helpers.tls_material import start_tls_mcp_origin, trust_test_ca


class DynamicProductEnv(EgressHatchMixin, ProductEnv):
    """No product service, registry, converter or repository is substituted."""

    EXTERNAL_PATCHES = {"ranking_factory": ProductEnv.EXTERNAL_PATCHES["ranking_factory"]}
    ASYNC_PATCHES = set()

    def _configure_mocks(self) -> None:
        self.set_ranking_disabled()

    def _enter_pre(self) -> None:
        super()._enter_pre()
        self.set_egress_hatches(private=True)
        self._signals = []
        self._signals_fail = False
        tools = {"get_signals": self._get_signals}
        if self.is_e2e:
            config = self.e2e_config
            assert config is not None and config.signals_origin, "The live stack declares no signals TLS front"
            front = urlsplit(config.signals_origin)
            self.origin = guarded_origin(
                self._guard,
                "signals_origin",
                run_mcp_origin(
                    tools=tools,
                    listen_host="0.0.0.0",
                    port=config.signals_port,
                    path=front.path,
                    public_origin=f"{front.scheme}://{front.netloc}",
                ),
            )
        else:
            self.origin = start_tls_mcp_origin(self._guard, "signals_origin", tools=tools)
        trust_test_ca(self._guard)

    def _get_signals(self, signal_spec: str, discovery_mode: str) -> dict:
        if self._signals_fail:
            raise RuntimeError("Signals service unavailable")
        return {"signals": self._signals}

    def seed_catalog(
        self,
        *,
        legacy: bool = False,
        restricted: bool = False,
        priced: bool = False,
        invalid_signal: bool = False,
        signals_fail: bool = False,
    ) -> None:
        from src.services.dynamic_products import generate_variant_id

        self._signals_fail = signals_fail

        tenant, _ = self.setup_default_data(ad_server="mock")
        static = ProductFactory(tenant=tenant, product_id="static", name="Static inventory")
        PricingOptionFactory(product=static, pricing_option_id="static-rate", rate=Decimal("4.25"))
        template = ProductFactory(
            tenant=tenant,
            product_id="template",
            name="Audience inventory",
            dynamic_template=True,
            template_pricing__pricing_option_id="negotiated-cpm",
            template_pricing__rate=Decimal("7.25"),
            signals_agent_ids=[self.origin.base_url],
            allowed_principal_ids=["another-buyer"] if restricted else None,
        )
        PricingOptionFactory(
            product=template, pricing_option_id="click-rate", pricing_model="cpc", rate=Decimal("1.35")
        )
        SignalsAgentFactory(tenant=tenant, agent_url=self.origin.base_url)
        key = {"type": "key_value", "key": "audience", "value": "premium"}
        self.variant_id = generate_variant_id("template", key)
        signal_fields = {
            "signal_agent_segment_id": "premium",
            "name": "Premium audience",
            "description": "Premium readers",
            "agent_url": tenant.agent_url,
            "key": "audience",
            "value": "premium",
        }
        self._signals = [SignalFactory.payload(**signal_fields)]
        if invalid_signal:
            self._signals.insert(
                0,
                SignalFactory.payload(
                    **{
                        **signal_fields,
                        "name": "x" * 300,
                        "value": "invalid",
                    }
                ),
            )
        if legacy or priced:
            saved = ProductFactory(
                tenant=tenant,
                name="Saved audience inventory",
                product_id=self.variant_id,
                is_dynamic_variant=True,
                parent_product_id="template",
                activation_key=key,
                allowed_principal_ids=["another-buyer"] if restricted else None,
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

    def set_template_access(self, allowed: list[str] | None) -> None:
        from src.core.database.repositories.product import ProductRepository

        repository = ProductRepository(self.get_session(), self._tenant_id)
        repository.update_fields("template", allowed_principal_ids=allowed)
        self.get_session()
