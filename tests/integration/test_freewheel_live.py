"""Live FreeWheel API smoke test.

Exercises the whole FreeWheel client stack — bearer auth, content-type
negotiation, inventory reads, commercial reads, creative reads, and a
full write-cleanup cycle (Campaign + IO + Placement) — against the real
publisher API. Skipped by default; runs only when both env vars are set:

    FREEWHEEL_TEST_API_KEY
    FREEWHEEL_TEST_ADVERTISER_ID

The creative trafficking write cycle additionally needs:

    FREEWHEEL_TEST_AD_UNIT_NODE_ID

Run with::

    uv run pytest tests/integration/test_freewheel_live.py -m live -v

This test creates and deletes real entities on the publisher's test
network. Names are clearly tagged with ``scope3-live-smoke-`` and the
current UTC timestamp so any orphans from a failed cleanup are easy to
find and reap by hand.
"""

from __future__ import annotations

import logging
import os
import uuid

import pytest

from src.adapters.freewheel import FreeWheelClient, FreeWheelError

logger = logging.getLogger(__name__)

API_TOKEN_ENV = "FREEWHEEL_TEST_API_KEY"
ADVERTISER_ID_ENV = "FREEWHEEL_TEST_ADVERTISER_ID"
AD_UNIT_NODE_ID_ENV = "FREEWHEEL_TEST_AD_UNIT_NODE_ID"

pytestmark = pytest.mark.live


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set — live FreeWheel test requires real credentials")
    return value


@pytest.fixture(scope="module")
def client() -> FreeWheelClient:
    """One client per module; the bearer is constant across all tests here."""
    return FreeWheelClient(api_token=_require_env(API_TOKEN_ENV))


@pytest.fixture(scope="module")
def advertiser_id() -> int:
    return int(_require_env(ADVERTISER_ID_ENV))


@pytest.fixture(scope="module")
def ad_unit_node_id() -> int:
    return int(_require_env(AD_UNIT_NODE_ID_ENV))


@pytest.fixture
def probe_label() -> str:
    """A short unique label so concurrent runs and orphan reaping stay sane."""
    return f"scope3-live-smoke-{uuid.uuid4().hex[:8]}"


class TestAuthAndConnectivity:
    def test_token_info_returns_user_and_expiry(self, client: FreeWheelClient):
        info = client.token_info()
        assert "user_id" in info
        assert isinstance(info["expires_in"], int)
        assert info["expires_in"] > 0
        logger.info("Token info: user_id=%s expires_in=%s", info.get("user_id"), info.get("expires_in"))


class TestInventoryReads:
    def test_list_sites_returns_entities(self, client: FreeWheelClient):
        page = client.inventory.list_sites(per_page=5)
        assert page.total_count >= 1
        assert all(site.id > 0 for site in page.items)

    def test_list_videos_returns_entities(self, client: FreeWheelClient):
        page = client.inventory.list_videos(per_page=5)
        assert page.total_count >= 1
        assert all(video.id > 0 for video in page.items)


class TestCommercialReads:
    def test_list_advertisers_includes_test_advertiser(self, client: FreeWheelClient, advertiser_id: int):
        advertiser = client.commercial.get_advertiser(advertiser_id)
        assert advertiser.id == advertiser_id
        assert advertiser.status is not None


class TestCreativeReads:
    def test_list_creatives_returns_entities(self, client: FreeWheelClient):
        page = client.creatives.list_creatives(per_page=5)
        assert page.total_count >= 1
        assert all(c.id > 0 for c in page.items)


class TestWriteRoundTrip:
    """Create-then-delete cycle for the full Campaign → IO → Placement stack.

    Verifies that adapter.create_media_buy's underlying call sequence
    actually works end-to-end against the real API. Entities are clearly
    tagged in case cleanup fails.
    """

    def test_full_create_and_delete_cycle(self, client: FreeWheelClient, advertiser_id: int, probe_label: str):
        campaign_id: int | None = None
        io_id: int | None = None
        placement_id: int | None = None

        try:
            campaign = client.commercial.create_campaign(name=probe_label, advertiser_id=advertiser_id)
            campaign_id = campaign.id
            assert campaign.id > 0
            assert campaign.advertiser_id == advertiser_id
            logger.info("Created campaign %s", campaign.id)

            io = client.commercial.create_insertion_order(name=probe_label, campaign_id=campaign.id)
            io_id = io.id
            assert io.campaign_id == campaign.id
            assert io.currency == "EUR"  # observed default for this publisher
            logger.info("Created insertion order %s", io.id)

            placement = client.commercial.create_placement(name=probe_label, insertion_order_id=io.id)
            placement_id = placement.id
            assert placement.insertion_order_id == io.id
            assert placement.status == "IN_ACTIVE"
            logger.info("Created placement %s", placement.id)

            # Round-trip GET to confirm everything reads back correctly.
            fetched_io = client.commercial.get_insertion_order(io.id)
            assert fetched_io.id == io.id

        finally:
            # Best-effort reverse-order cleanup. Each delete is logged with
            # its outcome so orphans from a failed run are visible.
            for entity_kind, entity_id, deleter in [
                ("placement", placement_id, client.commercial.delete_placement),
                ("insertion_order", io_id, client.commercial.delete_insertion_order),
                ("campaign", campaign_id, client.commercial.delete_campaign),
            ]:
                if entity_id is None:
                    continue
                try:
                    deleter(entity_id)
                    logger.info("Deleted %s %s", entity_kind, entity_id)
                except FreeWheelError as exc:
                    logger.warning("Failed to delete %s %s: %s", entity_kind, entity_id, exc)


class TestCreativeTraffickingRoundTrip:
    """Create a VAST creative_resource, bind it to an ad_unit_node, then clean up."""

    SAMPLE_VAST_URL = "https://samplelib.com/vast/sample-vast-2.0-inline-linear.xml"

    def test_create_bind_unbind_delete_cycle(
        self,
        client: FreeWheelClient,
        advertiser_id: int,
        ad_unit_node_id: int,
        probe_label: str,
    ):
        creative_id: int | None = None
        instance_id: int | None = None

        try:
            creative = client.creatives.create_creative(
                name=probe_label,
                advertiser_ids=[advertiser_id],
                external_id=probe_label,
                renditions=[
                    {
                        "uri": self.SAMPLE_VAST_URL,
                        "content_type": "application/xml",
                        "vast_rendition": True,
                        "https_compatibility": "compatible",
                    }
                ],
                duration=15,
            )
            creative_id = creative.id
            assert creative.id > 0
            logger.info("Created creative_resource %s", creative.id)

            binding = client.creatives.create_creative_instance(
                ad_unit_node_id=ad_unit_node_id,
                creative_id=creative.id,
                tracking_name=probe_label,
            )
            instance_id = int(binding["id"])
            assert int(binding["creative_id"]) == creative.id
            assert int(binding["ad_id"]) == ad_unit_node_id
            logger.info("Created creative_instance %s", instance_id)

        finally:
            if instance_id is not None:
                try:
                    client.creatives.delete_creative_instance(instance_id)
                    logger.info("Deleted creative_instance %s", instance_id)
                except FreeWheelError as exc:
                    logger.warning("Failed to delete creative_instance %s: %s", instance_id, exc)
            if creative_id is not None:
                try:
                    client.creatives.delete_creative(creative_id)
                    logger.info("Deleted creative_resource %s", creative_id)
                except FreeWheelError as exc:
                    logger.warning("Failed to delete creative_resource %s: %s", creative_id, exc)
