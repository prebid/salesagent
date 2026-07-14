"""Factory_boy factory for DeliverySimulationConfig model (#1418).

Lets tests seed the server-side delivery payload the Mock adapter reads from
the DB. The harness builds the same ``AdapterGetMediaBuyDeliveryResponse`` it
always has, then writes its ``model_dump(mode="json")`` here instead of
registering it on an in-process MagicMock.
"""

from __future__ import annotations

from typing import Any

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import DeliverySimulationConfig
from tests.factories.core import TenantFactory


def _default_payload(o: Any) -> dict[str, Any]:
    """A minimal, valid AdapterGetMediaBuyDeliveryResponse wire dump."""
    return {
        "media_buy_id": o.media_buy_id,
        "reporting_period": {
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-31T00:00:00+00:00",
        },
        "totals": {"impressions": 0, "spend": 0.0},
        "by_package": [],
        "currency": "USD",
    }


class DeliverySimulationConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = DeliverySimulationConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)

    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    media_buy_id = Sequence(lambda n: f"mb_sim_{n:04d}")
    response_payload = LazyAttribute(_default_payload)
