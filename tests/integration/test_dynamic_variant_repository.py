"""Pricing row ownership and concurrent variant writes on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.uow import ProductUoW
from src.services.dynamic_products import generate_variant_id, generate_variants_from_signals
from tests.factories import PricingOptionFactory, ProductFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = pytest.mark.requires_db


@pytest.mark.parametrize("is_fixed", [True, False])
def test_inherited_pricing_owns_new_rows_and_json(integration_db, is_fixed):
    with BareIntegrationEnv() as env:
        tenant, _ = env.setup_default_data()
        template = ProductFactory(tenant=tenant, product_id="template", is_dynamic=True)
        source = PricingOptionFactory(
            product=template,
            pricing_option_id="publisher-option",
            currency="EUR",
            is_fixed=is_fixed,
            rate=Decimal("7.25") if is_fixed else None,
            price_guidance={"floor": 2.5},
            parameters={"publisher_data": {"label": "original"}},
            min_spend_per_package=Decimal("250.00"),
        )
        variant = ProductFactory(tenant=tenant, product_id="variant", parent_product_id="template")
        session = env.get_session()
        repository = ProductRepository(session, tenant.tenant_id)
        repository.inherit_missing_variant_pricing(variant, template)
        inherited = variant.pricing_options[0]
        assert inherited.id is None
        for field in (
            "pricing_option_id",
            "pricing_model",
            "currency",
            "is_fixed",
            "rate",
            "price_guidance",
            "parameters",
            "min_spend_per_package",
        ):
            assert getattr(inherited, field) == getattr(source, field)
        assert inherited.product_id == "variant"
        inherited.price_guidance["floor"] = 3.5
        inherited.parameters["publisher_data"]["label"] = "variant-only"
        assert source.price_guidance == {"floor": 2.5}
        assert source.parameters == {"publisher_data": {"label": "original"}}
        env.get_session().expire_all()
        stored = repository.get_by_id_with_pricing("variant").pricing_options[0]
        assert stored.id != source.id
        assert stored.price_guidance == {"floor": 3.5}
        assert stored.parameters == {"publisher_data": {"label": "variant-only"}}
        assert source.price_guidance == {"floor": 2.5}
        assert source.parameters == {"publisher_data": {"label": "original"}}


@pytest.mark.parametrize("legacy", [False, True], ids=["new", "unpriced-existing"])
def test_concurrent_variant_writes_keep_one_complete_product(integration_db, legacy):
    key = {"type": "segment_id", "segment_id": "premium"}
    signal = {"name": "Premium", "deployments": [{"is_live": True, "activation_key": key}]}
    variant_id = generate_variant_id("template", key)
    with BareIntegrationEnv() as env:
        tenant, _ = env.setup_default_data()
        tenant_id = tenant.tenant_id
        template = ProductFactory(tenant=tenant, product_id="template", is_dynamic=True)
        PricingOptionFactory(product=template, pricing_option_id="publisher-cpm", rate=Decimal("7.25"))
        PricingOptionFactory(product=template, pricing_option_id="publisher-cpc", pricing_model="cpc")
        if legacy:
            ProductFactory(tenant=tenant, product_id=variant_id, parent_product_id="template", is_dynamic_variant=True)
        env.get_session()
        ready = Barrier(4)

        def discover():
            ready.wait(timeout=10)
            with ProductUoW(tenant_id) as uow:
                assert uow.products is not None
                templates = uow.products.list_dynamic_templates(for_update=True)
                variants = generate_variants_from_signals(uow.products, templates[0], [signal], "premium")
                return [(variant.product_id, len(variant.pricing_options)) for variant in variants]

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: discover(), range(4)))
        assert outcomes == [[(variant_id, 2)]] * 4
        session = env.get_session()
        session.expire_all()
        repository = ProductRepository(session, tenant_id)
        stored = repository.get_by_id_with_pricing(variant_id)
        assert sorted(option.pricing_option_id for option in stored.pricing_options) == [
            "publisher-cpc",
            "publisher-cpm",
        ]
        assert sorted(product.product_id for product in repository.list_all()) == ["template", variant_id]
