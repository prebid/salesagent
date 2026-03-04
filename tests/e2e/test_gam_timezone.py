"""
E2E test for GAM network timezone discovery.

Verifies that:
1. The test network exposes a valid IANA timezone
2. GAMReportingService auto-detects it correctly
3. The detected timezone is usable with pytz

Test network: 23341594478 (XFP sandbox property)
Note: Test networks cannot serve ads, so delivery/reporting data is always empty.
      This test only validates the timezone discovery mechanism, not report content.
"""

import pytest
import pytz


@pytest.mark.requires_gam
class TestGAMNetworkTimezone:
    """Verify GAM network timezone is discoverable and valid."""

    def test_network_has_timezone(self, gam_client_manager):
        """Network exposes a timeZone field."""
        client = gam_client_manager.get_client()
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        assert network["timeZone"] is not None, "Network timeZone field is missing"
        assert isinstance(network["timeZone"], str)
        assert len(network["timeZone"]) > 0

    def test_network_timezone_is_valid_iana(self, gam_client_manager):
        """Network timezone is a valid IANA timezone that pytz can use."""
        client = gam_client_manager.get_client()
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        tz_name = network["timeZone"]

        # Must be in pytz's database — this is what GAMReportingService uses
        assert tz_name in pytz.all_timezones, f"Network timezone '{tz_name}' is not a valid IANA timezone"

        # Must be constructible (some timezone names are aliases that exist
        # in all_timezones but fail on construction)
        tz = pytz.timezone(tz_name)
        assert tz is not None

    def test_reporting_service_autodetects_timezone(self, gam_client_manager):
        """GAMReportingService auto-detects network timezone on init."""
        from src.adapters.gam_reporting_service import GAMReportingService

        client = gam_client_manager.get_client()
        service = GAMReportingService(client)

        # Should have auto-detected from NetworkService, not fallen back
        assert service.network_timezone != "America/New_York" or _network_is_eastern(gam_client_manager), (
            "Expected auto-detected timezone, got fallback 'America/New_York'. "
            "This may indicate NetworkService.getCurrentNetwork() failed silently."
        )

        # Must be a valid pytz timezone
        assert service.network_timezone in pytz.all_timezones


def _network_is_eastern(gam_client_manager) -> bool:
    """Check if the network actually IS in Eastern time (not a fallback)."""
    client = gam_client_manager.get_client()
    network_service = client.GetService("NetworkService")
    network = network_service.getCurrentNetwork()
    return network["timeZone"] == "America/New_York"
