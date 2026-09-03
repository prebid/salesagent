#!/usr/bin/env python3
"""
Integration tests for GAM country and ad unit reporting functionality

Tests the GAM reporting service with mock data to verify country breakdown,
ad unit analysis, and combined reporting features.
"""

import unittest.mock

import pytest

from src.adapters.gam_reporting_service import GAMReportingService


def mock_requests_get(url, **kwargs):
    """Stand in for the egress seam's send(), returning gzipped CSV data.

    The report download goes through src.core.security.outbound_http.send now, so
    this returns an OutboundResult-shaped double: the body is read off
    ``result.content`` (GH #1802 closed OutboundResult over
    content/text/headers directly -- no more ``.response`` reach-through),
    and the seam raises on a non-2xx rather than handing back something to
    call raise_for_status() on.
    """
    import csv
    import gzip
    import io

    # Generate mock CSV data with country and ad unit info
    rows = []
    countries = ["United States", "Canada", "United Kingdom", "Germany", "France"]
    ad_units = ["Homepage_Top", "Article_Sidebar", "Video_Pre-Roll", "Mobile_Banner"]

    for country in countries:
        for ad_unit in ad_units:
            rows.append(
                {
                    "Dimension.DATE": "2025-01-13",
                    "Dimension.ADVERTISER_ID": "12345",
                    "Dimension.ADVERTISER_NAME": "Test Advertiser",
                    "Dimension.ORDER_ID": "67890",
                    "Dimension.ORDER_NAME": "Test Campaign",
                    "Dimension.LINE_ITEM_ID": "11111",
                    "Dimension.LINE_ITEM_NAME": "Test Line Item",
                    "Dimension.COUNTRY_NAME": country,
                    "Dimension.AD_UNIT_ID": f"unit_{ad_unit}",
                    "Dimension.AD_UNIT_NAME": ad_unit,
                    "Column.AD_SERVER_IMPRESSIONS": str(10000 + hash(country + ad_unit) % 5000),
                    "Column.AD_SERVER_CLICKS": str(100 + hash(country + ad_unit) % 50),
                    "Column.AD_SERVER_CPM_AND_CPC_REVENUE": str(5000000 + hash(country + ad_unit) % 2000000),
                }
            )

    # Create gzipped CSV data
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

    # Gzip the CSV data
    gz_buffer = io.BytesIO()
    with gzip.open(gz_buffer, "wt", newline="") as gz_file:
        gz_file.write(csv_buffer.getvalue())

    # OutboundResult-shaped double: production reads result.content directly
    mock_result = unittest.mock.Mock()
    mock_result.content = gz_buffer.getvalue()
    return mock_result


def create_mock_gam_client():
    """Create a mock GAM client for testing"""

    class MockGAMClient:
        def GetService(self, service_name):
            if service_name == "ReportService":
                return MockReportService()
            elif service_name == "NetworkService":
                return MockNetworkService()
            return None

    class MockNetworkService:
        def getCurrentNetwork(self):
            class Network:
                timeZone = "America/New_York"

            return Network()

    class MockReportService:
        def runReportJob(self, job):
            return {"id": "test_report_123"}

        def getReportJobStatus(self, job_id):
            return "COMPLETED"

        def getReportDownloadURL(self, job_id, format_type):
            return "https://storage.googleapis.com/gam-reports/mock-report.csv.gz"

    return MockGAMClient()


@pytest.mark.requires_db
def test_country_breakdown():
    """Test the country breakdown functionality"""
    # Create mock client and service
    mock_client = create_mock_gam_client()

    # Patch the seam entry point the report download now uses
    with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
        service = GAMReportingService(mock_client, "America/New_York")

        # Test get_country_breakdown
        result = service.get_country_breakdown(date_range="this_month", advertiser_id="12345")

    # Assertions for test validation
    assert result["total_countries"] > 0, "Should have country data"
    assert result["date_range"] == "this_month"
    assert result["timezone"] == "America/New_York"
    assert "countries" in result
    assert len(result["countries"]) > 0

    # Verify country data structure
    first_country = result["countries"][0]
    required_fields = ["country", "impressions", "spend", "avg_cpm", "ctr"]
    for field in required_fields:
        assert field in first_country, f"Country data missing field: {field}"

    # Verify data types
    assert isinstance(first_country["impressions"], int)
    assert isinstance(first_country["spend"], int | float)
    assert isinstance(first_country["avg_cpm"], int | float)
    assert isinstance(first_country["ctr"], int | float)


@pytest.mark.requires_db
def test_ad_unit_breakdown():
    """Test the ad unit breakdown functionality"""
    # Create mock client and service
    mock_client = create_mock_gam_client()

    # Patch the seam entry point the report download now uses
    with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
        service = GAMReportingService(mock_client, "America/New_York")

        # Test get_ad_unit_breakdown
        result = service.get_ad_unit_breakdown(
            date_range="this_month",
            advertiser_id="12345",
            country="United States",  # Filter by US
        )

    # Assertions for test validation
    assert result["total_ad_units"] > 0, "Should have ad unit data"
    assert result["filtered_by_country"] == "United States"
    assert "ad_units" in result
    assert len(result["ad_units"]) > 0

    # Verify ad unit data structure
    first_ad_unit = result["ad_units"][0]
    required_fields = ["ad_unit_name", "ad_unit_id", "impressions", "spend", "avg_cpm", "countries"]
    for field in required_fields:
        assert field in first_ad_unit, f"Ad unit data missing field: {field}"

    # Verify data types
    assert isinstance(first_ad_unit["impressions"], int)
    assert isinstance(first_ad_unit["spend"], int | float)
    assert isinstance(first_ad_unit["avg_cpm"], int | float)
    assert isinstance(first_ad_unit["countries"], dict)

    # Verify country breakdown within ad unit
    if first_ad_unit["countries"]:
        country_name, country_data = next(iter(first_ad_unit["countries"].items()))
        assert "cpm" in country_data, "Country data should have CPM"


@pytest.mark.requires_db
def test_combined_reporting():
    """Test getting both country and ad unit data with the include flags"""
    # Create mock client and service
    mock_client = create_mock_gam_client()

    # Patch the seam entry point the report download now uses
    with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
        service = GAMReportingService(mock_client, "America/New_York")

        # Get reporting data with both dimensions
        result = service.get_reporting_data(
            date_range="today", advertiser_id="12345", include_country=True, include_ad_unit=True
        )

    # Assertions for test validation
    assert hasattr(result, "dimensions"), "Result should have dimensions attribute"
    assert hasattr(result, "data"), "Result should have data attribute"
    assert hasattr(result, "metrics"), "Result should have metrics attribute"
    assert hasattr(result, "data_valid_until"), "Result should have data_valid_until attribute"

    # Verify dimensions include both country and ad unit
    assert "COUNTRY_NAME" in result.dimensions or "country" in str(result.dimensions).lower()
    assert "AD_UNIT_NAME" in result.dimensions or "ad_unit" in str(result.dimensions).lower()

    # Verify data structure
    assert len(result.data) > 0, "Should have data rows"
    first_row = result.data[0]
    assert "impressions" in first_row, "Data row should have impressions"
    assert "cpm" in first_row, "Data row should have CPM"

    # Verify both country and ad unit data present
    has_country = any("country" in row for row in result.data)
    has_ad_unit = any("ad_unit_name" in row for row in result.data)
    assert has_country or has_ad_unit, "Should have either country or ad unit data"

    # Verify metrics
    assert isinstance(result.metrics, dict), "Metrics should be a dictionary"
    assert len(result.metrics) > 0, "Should have summary metrics"


@pytest.mark.requires_db
def test_gam_reporting_integration():
    """Integration test that runs all GAM reporting functionality"""
    # Create mock client and service for integration test
    mock_client = create_mock_gam_client()

    # Patch the seam entry point the report download now uses
    with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
        service = GAMReportingService(mock_client, "America/New_York")

        # Test country breakdown
        country_result = service.get_country_breakdown(date_range="this_month", advertiser_id="12345")

        # Test ad unit breakdown
        ad_unit_result = service.get_ad_unit_breakdown(
            date_range="this_month", advertiser_id="12345", country="United States"
        )

        # Test combined reporting
        combined_result = service.get_reporting_data(
            date_range="today", advertiser_id="12345", include_country=True, include_ad_unit=True
        )

    # Additional integration assertions
    assert country_result["total_countries"] > 0, "Integration: Should have country data"
    assert ad_unit_result["total_ad_units"] > 0, "Integration: Should have ad unit data"
    assert hasattr(combined_result, "data"), "Integration: Should have combined data"


class TestGAMCountryAdUnitReporting:
    """Test class for GAM country and ad unit reporting functionality."""

    def test_mock_client_creation(self):
        """Test that mock GAM client can be created successfully"""
        mock_client = create_mock_gam_client()
        assert mock_client is not None

        # Test that required services can be obtained
        report_service = mock_client.GetService("ReportService")
        network_service = mock_client.GetService("NetworkService")

        assert report_service is not None
        assert network_service is not None
        assert hasattr(report_service, "getReportDownloadURL")

    def test_gam_service_initialization(self):
        """Test that GAM reporting service can be initialized"""
        mock_client = create_mock_gam_client()
        service = GAMReportingService(mock_client, "America/New_York")
        assert service is not None

    @pytest.mark.requires_db
    def test_country_breakdown_detailed(self):
        """Detailed test of country breakdown functionality"""
        # Create mock client and service
        mock_client = create_mock_gam_client()

        # Patch the seam entry point the report download now uses
        with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
            service = GAMReportingService(mock_client, "America/New_York")

            # Get country breakdown
            result = service.get_country_breakdown(date_range="this_month", advertiser_id="12345")

        # Test specific countries are present (from our mock data)
        country_names = [c["country"] for c in result["countries"]]
        expected_countries = ["United States", "Canada", "United Kingdom", "Germany", "France"]

        for expected in expected_countries:
            assert expected in country_names, f"Expected country {expected} not found in results"

    @pytest.mark.requires_db
    def test_ad_unit_breakdown_detailed(self):
        """Detailed test of ad unit breakdown functionality"""
        # Create mock client and service
        mock_client = create_mock_gam_client()

        # Patch the seam entry point the report download now uses
        with unittest.mock.patch("src.adapters.gam_reporting_service.send", side_effect=mock_requests_get):
            service = GAMReportingService(mock_client, "America/New_York")

            # Get ad unit breakdown
            result = service.get_ad_unit_breakdown(
                date_range="this_month", advertiser_id="12345", country="United States"
            )

        # Test specific ad units are present (from our mock data)
        ad_unit_names = [au["ad_unit_name"] for au in result["ad_units"]]
        expected_ad_units = ["Homepage_Top", "Article_Sidebar", "Video_Pre-Roll", "Mobile_Banner"]

        for expected in expected_ad_units:
            assert expected in ad_unit_names, f"Expected ad unit {expected} not found in results"


if __name__ == "__main__":
    # When run directly, provide usage information
    print("This is now a pytest integration test.")
    print("Run with: pytest tests/integration/test_gam_country_adunit.py -v")
    print()
    print("API Usage Examples:")
    print("# Get country breakdown:")
    print("curl 'http://localhost:8001/api/tenant/{tenant_id}/gam/reporting/countries?date_range=this_month'")
    print()
    print("# Get ad unit breakdown:")
    print(
        "curl 'http://localhost:8001/api/tenant/{tenant_id}/gam/reporting/ad-units?date_range=this_month&country=United%20States'"
    )
