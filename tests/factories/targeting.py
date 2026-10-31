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

from src.core.schemas import CollectionListReference, FrequencyCap, Targeting

GOVERNANCE_URL = "https://gov.example"


class FrequencyCapFactory(factory.Factory):
    """Factory for a frequency cap the pinned schema accepts.

    ``core/frequency-cap.json`` states two constraints outside its properties, and
    the rebuilt SDK compiles both onto the exported class:

    * ``anyOf`` — at least one of ``suppress``, ``suppress_minutes``, ``max_impressions``
    * ``dependencies`` — ``max_impressions`` requires ``per`` AND ``window``, and each of
      those requires ``max_impressions``

    The defaults below satisfy both, which is the whole reason this factory exists:
    thirteen tests hand-wrote ``{"max_impressions": N, "suppress_minutes": M}``, a
    document the pin has always forbidden and the previous SDK's exported class
    happened to accept. One home for the legal shape means the next test that needs
    a cap cannot reintroduce the illegal one (salesagent-8qela.120).

    Override ``max_impressions`` alone; ``per`` and ``window`` travel with it. For a
    cooldown-only cap, build ``FrequencyCap(suppress_minutes=...)`` directly — it
    needs none of these three and the factory would only add noise.
    """

    class Meta:
        model = FrequencyCap

    max_impressions = 5
    per = "devices"
    window = {"interval": 1, "unit": "days"}


class PropertyListReferenceFactory(factory.Factory):
    """Factory for the library-defined PropertyListReference Pydantic model."""

    class Meta:
        model = PropertyListReference

    agent_url = GOVERNANCE_URL
    list_id = factory.Sequence(lambda n: f"property_list_{n}")


class CollectionListReferenceFactory(factory.Factory):
    """Factory for CollectionListReference.

    Re-exported from ``src.core.schemas`` (the underlying type is sourced from
    ``adcp.types.domains.core.collection_list_ref`` because the adcp SDK
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
