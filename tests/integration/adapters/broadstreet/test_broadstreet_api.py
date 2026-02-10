"""Integration tests for Broadstreet API client.

Run with:
    BROADSTREET_API_KEY=<your_key> pytest tests/integration/adapters/broadstreet/ -v

Optionally set BROADSTREET_NETWORK_ID if you have multiple networks.

These tests require a valid Broadstreet API key and will make real API calls.
The API key should come from your tenant's Broadstreet account settings.
"""

import os
from datetime import datetime, timedelta

import pytest

from src.adapters.broadstreet.client import BroadstreetClient

# Skip all tests if no API key is configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("BROADSTREET_API_KEY"),
    reason="BROADSTREET_API_KEY environment variable not set",
)


@pytest.fixture
def api_key():
    """Get API key from environment."""
    return os.environ.get("BROADSTREET_API_KEY")


@pytest.fixture
def network_id():
    """Get network ID from environment or discover it."""
    return os.environ.get("BROADSTREET_NETWORK_ID")


@pytest.fixture
def client(api_key, network_id):
    """Create a Broadstreet client."""
    # If no network ID provided, discover it
    if not network_id:
        import requests

        resp = requests.get(f"https://api.broadstreetads.com/api/0/networks?access_token={api_key}")
        networks = resp.json().get("networks", [])
        if networks:
            network_id = str(networks[0]["id"])
        else:
            pytest.skip("No networks found for this API key")

    return BroadstreetClient(
        access_token=api_key,
        network_id=network_id,
    )


class TestBroadstreetConnection:
    """Tests for basic API connectivity."""

    def test_get_networks(self, client):
        """Test fetching networks."""
        networks = client.get_networks()
        assert isinstance(networks, list)
        assert len(networks) > 0
        assert "id" in networks[0]
        assert "name" in networks[0]

    def test_get_network(self, client):
        """Test fetching specific network."""
        network = client.get_network()
        assert "id" in network or "name" in network

    def test_get_zones(self, client):
        """Test fetching zones."""
        zones = client.get_zones()
        assert isinstance(zones, list)
        # May be empty, that's OK

    def test_get_advertisers(self, client):
        """Test fetching advertisers."""
        advertisers = client.get_advertisers()
        assert isinstance(advertisers, list)


class TestBroadstreetCampaignLifecycle:
    """Tests for campaign create/delete lifecycle."""

    @pytest.fixture
    def advertiser_id(self, client):
        """Get or create an advertiser for testing."""
        advertisers = client.get_advertisers()
        if advertisers:
            return str(advertisers[0]["id"])

        # Create a test advertiser
        advertiser = client.create_advertiser("AdCP Test Advertiser")
        return str(advertiser["id"])

    def test_campaign_lifecycle(self, client, advertiser_id):
        """Test creating and deleting a campaign."""
        # Create campaign
        start_date = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        campaign = client.create_campaign(
            advertiser_id=advertiser_id,
            name=f"AdCP Integration Test {datetime.now().isoformat()}",
            start_date=start_date,
            end_date=end_date,
        )

        assert "id" in campaign
        campaign_id = str(campaign["id"])

        try:
            # Verify campaign was created
            assert campaign.get("name", "").startswith("AdCP Integration Test")

            # Delete campaign
            client.delete_campaign(advertiser_id, campaign_id)
        except Exception:
            # Cleanup on failure
            try:
                client.delete_campaign(advertiser_id, campaign_id)
            except Exception:
                pass
            raise


class TestBroadstreetAdvertisementLifecycle:
    """Tests for advertisement create/delete lifecycle."""

    @pytest.fixture
    def advertiser_id(self, client):
        """Get or create an advertiser for testing."""
        advertisers = client.get_advertisers()
        if advertisers:
            return str(advertisers[0]["id"])

        advertiser = client.create_advertiser("AdCP Test Advertiser")
        return str(advertiser["id"])

    def test_html_advertisement(self, client, advertiser_id):
        """Test creating an HTML advertisement."""
        ad = client.create_advertisement(
            advertiser_id=advertiser_id,
            name=f"AdCP HTML Test {datetime.now().isoformat()}",
            ad_type="html",
            params={"html": "<div>Test Ad</div>"},
        )

        assert "id" in ad
        ad_id = str(ad["id"])

        try:
            # Verify ad was created
            fetched = client.get_advertisement(advertiser_id, ad_id)
            assert fetched.get("name", "").startswith("AdCP HTML Test")

            # Delete ad
            client.delete_advertisement(advertiser_id, ad_id)
        except Exception:
            # Cleanup on failure
            try:
                client.delete_advertisement(advertiser_id, ad_id)
            except Exception:
                pass
            raise


class TestBroadstreetInventoryManager:
    """Tests for inventory manager with real API."""

    def test_fetch_zones(self, client):
        """Test fetching zones through inventory manager."""
        from src.adapters.broadstreet.managers.inventory import BroadstreetInventoryManager

        manager = BroadstreetInventoryManager(
            client=client,
            network_id=client.network_id,
            dry_run=False,
        )

        zones = manager.fetch_zones()
        # May be empty, but should be a list
        assert isinstance(zones, list)

        # If zones exist, verify structure
        if zones:
            zone = zones[0]
            assert hasattr(zone, "zone_id")
            assert hasattr(zone, "name")

    def test_build_inventory_response(self, client):
        """Test building inventory response."""
        from src.adapters.broadstreet.managers.inventory import BroadstreetInventoryManager

        manager = BroadstreetInventoryManager(
            client=client,
            network_id=client.network_id,
            dry_run=False,
        )

        response = manager.build_inventory_response()

        assert "zones" in response
        assert "creative_specs" in response
        assert "properties" in response
        assert response["properties"]["network_id"] == client.network_id
