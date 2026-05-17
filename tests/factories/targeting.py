"""Pydantic factories for Targeting and the AdCP list-reference types.

Mirrors the pattern in tests/factories/format.py. These are factory-boy
factories for Pydantic models (not ORM), used in unit and integration tests
that construct Targeting objects directly.

Usage::

    from tests.factories import (
        TargetingFactory,
        PropertyListReferenceFactory,
        CollectionListReferenceFactory,
    )

    # Targeting with both list types — storyboard inventory_list_targeting shape
    targeting = TargetingFactory.build(
        property_list=PropertyListReferenceFactory.build(list_id="acme_outdoor_allowlist_v1"),
        collection_list=CollectionListReferenceFactory.build(list_id="acme_outdoor_collections_v1"),
    )
"""

from __future__ import annotations

import factory
from adcp.types import PropertyListReference

from src.core.schemas import CollectionListReference, Targeting

GOVERNANCE_URL = "https://gov.example"


class PropertyListReferenceFactory(factory.Factory):
    """Factory for the library-defined PropertyListReference Pydantic model."""

    class Meta:
        model = PropertyListReference

    agent_url = GOVERNANCE_URL
    list_id = factory.Sequence(lambda n: f"property_list_{n}")


class CollectionListReferenceFactory(factory.Factory):
    """Factory for CollectionListReference.

    Re-exported from ``src.core.schemas`` (the underlying type is sourced from
    ``adcp.types.generated_poc.core.collection_list_ref`` because adcp 4.3
    didn't surface it on the public ``adcp.types`` namespace, though the type
    is generated and used by TargetingOverlay). Shape mirrors
    PropertyListReference exactly.
    """

    class Meta:
        model = CollectionListReference

    agent_url = GOVERNANCE_URL
    list_id = factory.Sequence(lambda n: f"collection_list_{n}")


class TargetingFactory(factory.Factory):
    """Factory for the salesagent Targeting Pydantic model.

    Defaults are intentionally empty so callers opt into specific dimensions
    via keyword overrides — Targeting is a many-fielded type and a single
    "default shape" doesn't make sense.
    """

    class Meta:
        model = Targeting
