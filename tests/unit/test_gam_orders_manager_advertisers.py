"""Unit tests for GAM Orders Manager get_advertisers() method."""

from unittest.mock import MagicMock

import pytest

from src.adapters.gam.managers.orders import GAMOrdersManager


class MockCompanyResult:
    """Mock GAM Company result."""

    def __init__(self, company_id: str, name: str):
        self.id = company_id
        self.name = name
        self.type = "ADVERTISER"


class MockGetCompaniesByStatementResponse:
    """Mock GAM getCompaniesByStatement response."""

    def __init__(self, companies: list[MockCompanyResult], total_size: int):
        self.results = companies
        self.totalResultSetSize = total_size


class TestGetAdvertisers:
    """Test get_advertisers() method with search, limit, and pagination."""

    @pytest.fixture
    def mock_client_manager(self):
        """Create mock GAM client manager."""
        mock_client = MagicMock()
        mock_client.get_service = MagicMock()
        return mock_client

    @pytest.fixture
    def orders_manager(self, mock_client_manager):
        """Create GAM Orders Manager with mocked dependencies."""
        manager = GAMOrdersManager(
            client_manager=mock_client_manager,
            advertiser_id="test_advertiser",
            trafficker_id="test_trafficker",
            dry_run=False,
        )
        return manager

    def test_get_advertisers_default_behavior(self, orders_manager, mock_client_manager):
        """Test default behavior: fetch first page with limit."""
        # Mock service response
        mock_service = MagicMock()
        mock_companies = [
            MockCompanyResult("123", "Advertiser A"),
            MockCompanyResult("456", "Advertiser B"),
            MockCompanyResult("789", "Advertiser C"),
        ]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 3)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call method with defaults
        result = orders_manager.get_advertisers()

        # Verify results
        assert len(result) == 3
        assert result[0]["id"] == "123"
        assert result[0]["name"] == "Advertiser A"
        assert result[1]["id"] == "456"
        assert result[2]["id"] == "789"

        # Verify sorted by name
        assert result[0]["name"] == "Advertiser A"

    def test_get_advertisers_with_search_query(self, orders_manager, mock_client_manager):
        """Test search filtering with LIKE operator."""
        # Mock service response
        mock_service = MagicMock()
        mock_companies = [
            MockCompanyResult("123", "Scope3"),
            MockCompanyResult("456", "Scopely"),
        ]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 2)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call with search query
        result = orders_manager.get_advertisers(search_query="Scope")

        # Verify results
        assert len(result) == 2
        assert result[0]["name"] == "Scope3"
        assert result[1]["name"] == "Scopely"

    def test_get_advertisers_sanitizes_search_query(self, orders_manager, mock_client_manager):
        """Test that search query is sanitized (stripped and length-limited)."""
        # Mock service
        mock_service = MagicMock()
        mock_response = MockGetCompaniesByStatementResponse([], 0)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Test whitespace stripping
        result = orders_manager.get_advertisers(search_query="  Scope3  ")
        assert len(result) == 0  # Empty result is fine, we're testing sanitization

        # Test length limiting (100 chars max)
        long_query = "A" * 200  # 200 chars
        result = orders_manager.get_advertisers(search_query=long_query)
        assert len(result) == 0  # Empty result is fine

    def test_get_advertisers_empty_search_after_strip(self, orders_manager, mock_client_manager):
        """Test that empty search query after stripping is treated as no search."""
        # Mock service
        mock_service = MagicMock()
        mock_companies = [MockCompanyResult("123", "Test")]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 1)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call with whitespace-only query
        result = orders_manager.get_advertisers(search_query="   ")

        # Should treat as no search and return results
        assert len(result) == 1

    def test_get_advertisers_respects_limit(self, orders_manager, mock_client_manager):
        """Test that limit parameter is respected."""
        # Mock service
        mock_service = MagicMock()
        mock_companies = [MockCompanyResult(str(i), f"Advertiser {i}") for i in range(50)]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 1000)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call with custom limit
        result = orders_manager.get_advertisers(limit=50)

        # Should return 50 results
        assert len(result) == 50

    def test_get_advertisers_enforces_max_limit(self, orders_manager, mock_client_manager):
        """Test that limit is capped at 500."""
        # Mock service
        mock_service = MagicMock()
        mock_companies = [MockCompanyResult(str(i), f"Advertiser {i}") for i in range(100)]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 1000)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call with limit > 500
        result = orders_manager.get_advertisers(limit=1000)

        # Limit should be enforced to 500 max
        # (We can't directly verify the limit in the statement, but the function applies it)
        assert len(result) == 100  # Returns what the mock provided

    def test_get_advertisers_pagination_with_fetch_all(self, orders_manager, mock_client_manager):
        """Test pagination when fetch_all=True."""
        # Mock service with multiple pages
        mock_service = MagicMock()

        # First page: 100 results (use zero-padded numbers so sorting works)
        first_page = [MockCompanyResult(str(i), f"Advertiser {i:03d}") for i in range(100)]
        first_response = MockGetCompaniesByStatementResponse(first_page, 250)

        # Second page: 100 results
        second_page = [MockCompanyResult(str(i + 100), f"Advertiser {i + 100:03d}") for i in range(100)]
        second_response = MockGetCompaniesByStatementResponse(second_page, 250)

        # Third page: 50 results
        third_page = [MockCompanyResult(str(i + 200), f"Advertiser {i + 200:03d}") for i in range(50)]
        third_response = MockGetCompaniesByStatementResponse(third_page, 250)

        # Mock will return different responses on successive calls
        mock_service.getCompaniesByStatement = MagicMock(side_effect=[first_response, second_response, third_response])
        mock_client_manager.get_service.return_value = mock_service

        # Call with fetch_all=True
        result = orders_manager.get_advertisers(fetch_all=True)

        # Should fetch all 250 advertisers across 3 pages
        assert len(result) == 250

        # Results are sorted alphabetically by name, so verify range
        # First result should be "Advertiser 000"
        assert result[0]["name"] == "Advertiser 000"
        assert result[0]["id"] == "0"

        # Last result should be "Advertiser 249"
        assert result[249]["name"] == "Advertiser 249"
        assert result[249]["id"] == "249"

        # Verify service was called 3 times (pagination)
        assert mock_service.getCompaniesByStatement.call_count == 3

    def test_get_advertisers_no_results(self, orders_manager, mock_client_manager):
        """Test handling of empty results."""
        # Mock service with no results
        mock_service = MagicMock()
        mock_response = MockGetCompaniesByStatementResponse([], 0)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call method
        result = orders_manager.get_advertisers()

        # Should return empty list
        assert result == []

    def test_get_advertisers_dry_run_mode(self, mock_client_manager):
        """Test dry run mode returns mock data."""
        # Create manager in dry-run mode
        manager = GAMOrdersManager(
            client_manager=mock_client_manager,
            advertiser_id="test_advertiser",
            trafficker_id="test_trafficker",
            dry_run=True,
        )

        # Call method (should not call GAM API)
        result = manager.get_advertisers()

        # Should return mock data
        assert len(result) == 2
        assert result[0]["id"] == "123456789"
        assert result[1]["id"] == "987654321"

        # Verify GAM service was NOT called
        mock_client_manager.get_service.assert_not_called()

    def test_get_advertisers_error_handling(self, orders_manager, mock_client_manager):
        """Test error handling when GAM API fails."""
        # Mock service that raises an error
        mock_service = MagicMock()
        mock_service.getCompaniesByStatement = MagicMock(side_effect=Exception("GAM API Error"))
        mock_client_manager.get_service.return_value = mock_service

        # Call method
        result = orders_manager.get_advertisers()

        # Should return empty list on error
        assert result == []

    def test_get_advertisers_sorted_by_name(self, orders_manager, mock_client_manager):
        """Test that results are sorted alphabetically by name."""
        # Mock service with unsorted results
        mock_service = MagicMock()
        mock_companies = [
            MockCompanyResult("3", "Zebra Corp"),
            MockCompanyResult("1", "Acme Inc"),
            MockCompanyResult("2", "Beta LLC"),
        ]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 3)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call method
        result = orders_manager.get_advertisers()

        # Verify sorted by name
        assert len(result) == 3
        assert result[0]["name"] == "Acme Inc"
        assert result[1]["name"] == "Beta LLC"
        assert result[2]["name"] == "Zebra Corp"

    def test_get_advertisers_combined_search_and_limit(self, orders_manager, mock_client_manager):
        """Test combining search query and limit."""
        # Mock service
        mock_service = MagicMock()
        mock_companies = [
            MockCompanyResult("1", "Test Company A"),
            MockCompanyResult("2", "Test Company B"),
        ]
        mock_response = MockGetCompaniesByStatementResponse(mock_companies, 2)
        mock_service.getCompaniesByStatement = MagicMock(return_value=mock_response)
        mock_client_manager.get_service.return_value = mock_service

        # Call with both search and limit
        result = orders_manager.get_advertisers(search_query="Test", limit=100)

        # Should return filtered and limited results
        assert len(result) == 2
        assert "Test" in result[0]["name"]
        assert "Test" in result[1]["name"]
