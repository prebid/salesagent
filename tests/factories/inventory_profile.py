"""Factory_boy factory for InventoryProfile model."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import InventoryProfile, InventoryProfilePublisherSelection
from tests.factories.core import TenantFactory
from tests.factories.publisher import SellsPublishersMixin


class InventoryProfileFactory(SellsPublishersMixin, factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = InventoryProfile
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    profile_id = Sequence(lambda n: f"profile_{n:04d}")
    name = LazyAttribute(lambda o: f"Inventory Profile {o.profile_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")
    inventory_config = factory.LazyFunction(
        lambda: {"ad_units": ["12345"], "placements": [], "include_descendants": True}
    )
    format_ids = factory.LazyFunction(
        lambda: [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
    )
    # The publishers the profile sells, as selectors (``publisher_properties=[...]``); stored
    # as selection rows, each publisher created first (SellsPublishersMixin).
    publisher_selections = LazyAttribute(
        lambda o: InventoryProfilePublisherSelection.from_selectors(o.tenant_id, o.publisher_properties)
    )

    class Params:
        publisher_properties = factory.LazyFunction(
            lambda: [
                {"publisher_domain": "test.example.com", "property_tags": ["all_inventory"], "selection_type": "by_tag"}
            ]
        )
