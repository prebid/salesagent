"""Stand-in for the Broadstreet API client, for tests that drive the Broadstreet adapter.

The adapter has no dry-run mode: a campaign, an advertisement and a placement exist only
once Broadstreet has been called. Each manager used to answer from data it invented when
``dry_run`` was set — the advertisement manager minted ``bs_ad_<creative_id>`` ids, and
the inventory manager returned a fixed zone list ("Top Banner" 728x90, "Sidebar" 300x250,
…) that tests then asserted on. Those branches are gone from ``src/``, so the vendor is
what a test stands in for, and inventory is data the TEST states.

Everything here returns the shape the real client returns: ``get_zones`` hands back the
vendor's raw zone dicts (which is why ``zone_payload`` exists — the manager reads both
``id``/``Id`` casings), and each create returns a dict carrying the new object's ``id``.
"""

from __future__ import annotations

from itertools import count
from typing import Any
from unittest.mock import Mock

from src.adapters.broadstreet.client import BroadstreetClient


def zone_payload(
    zone_id: str,
    name: str,
    *,
    width: int | None = None,
    height: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One zone as ``get_zones`` returns it — the vendor's wire shape, not ``ZoneInfo``."""
    payload: dict[str, Any] = {"id": zone_id, "name": name}
    if width is not None:
        payload["width"] = width
    if height is not None:
        payload["height"] = height
    payload.update(extra)
    return payload


def stub_broadstreet_client(
    *,
    zones: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    campaign_id: str = "camp_1",
) -> Mock:
    """A ``BroadstreetClient`` stand-in serving the zones a test states.

    ``create_advertisement`` and ``create_placement`` hand back a distinct id per call,
    the way the vendor does, so a test can tell one created object from another.
    """
    stub = Mock(spec=BroadstreetClient)
    stub.get_zones.return_value = list(zones)
    stub.create_campaign.return_value = {"id": campaign_id}

    ad_ids = count(1)
    stub.create_advertisement.side_effect = lambda **_: {"id": f"bs_{next(ad_ids)}"}

    placement_ids = count(1)
    stub.create_placement.side_effect = lambda **_: {"id": f"place_{next(placement_ids)}"}

    return stub
