"""Unit tests for property discovery service.

Tests the property discovery service that fetches and caches properties/tags
from publisher adagents.json files using the adcp library.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from adcp import AdagentsNotFoundError, AdagentsTimeoutError, AdagentsValidationError

from src.services.property_discovery_service import PropertyDiscoveryService


class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_mock_db_session():
        """Create mock database session (SQLAlchemy 2.0 compatible)."""
        mock_session = Mock()
        mock_db_session_patcher = patch("src.services.property_discovery_service.get_db_session")
        mock_db_session = mock_db_session_patcher.start()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock SQLAlchemy 2.0 pattern: session.scalars(stmt).all()
        # Must return empty list (iterable) not Mock object
        mock_scalars = Mock()
        mock_scalars.all.return_value = []
        mock_scalars.first.return_value = None
        mock_session.scalars.return_value = mock_scalars
        mock_session.execute.return_value.all.return_value = []

        return mock_db_session_patcher, mock_session


class TestPropertyDiscoveryService:
    """Test PropertyDiscoveryService functionality.

    These tests focus on the service logic. The adcp library's
    adagents.json fetching and parsing are tested in the adcp library.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PropertyDiscoveryService()

    @pytest.mark.asyncio
    async def test_sync_properties_success(self):
        """Test successful property and tag sync from adagents.json."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists (no existing properties/tags)
        # This mock needs to handle both .first() and .all() calls
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Mock adagents.json data
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "name": "Example Site",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                            "tags": ["premium", "news"],
                        }
                    ],
                }
            ]
        }

        # Mock adcp library functions
        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_props.return_value = [
                        {
                            "property_type": "website",
                            "name": "Example Site",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                            "tags": ["premium", "news"],
                        }
                    ]
                    mock_tags.return_value = ["premium", "news"]

                    # Test sync
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    # Verify results
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1
                    assert stats["tags_found"] == 2
                    assert stats["properties_created"] == 1
                    assert stats["tags_created"] == 2
                    assert len(stats["errors"]) == 0

                    # Verify adcp library called
                    mock_fetch.assert_called_once_with("example.com")
                    mock_props.assert_called_once_with(mock_adagents_data)
                    mock_tags.assert_called_once_with(mock_adagents_data)

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_adagents_not_found(self):
        """Test handling of missing adagents.json (404)."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock fetch to raise not found error
        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsNotFoundError("404 Not Found")

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert stats["properties_found"] == 0
            assert len(stats["errors"]) == 1
            assert "adagents.json not found (404)" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_timeout(self):
        """Test handling of timeout when fetching adagents.json."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsTimeoutError("https://example.com/.well-known/adagents.json", 5.0)

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert len(stats["errors"]) == 1
            assert "timeout" in stats["errors"][0].lower()

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_invalid_json(self):
        """Test handling of invalid adagents.json format."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsValidationError("Missing authorized_agents field")

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert len(stats["errors"]) == 1
            assert "Invalid adagents.json" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_no_domains(self):
        """Test handling when no publisher domains found."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock empty result from database query
        mock_session.execute.return_value.all.return_value = []

        stats = await self.service.sync_properties_from_adagents("tenant1", None)

        assert stats["domains_synced"] == 0
        assert len(stats["errors"]) == 1
        assert "No publisher domains found" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_multiple_domains(self):
        """Test syncing properties from multiple domains."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ],
                }
            ]
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_props.return_value = [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    # Sync from multiple domains
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com", "example.org"])

                    assert stats["domains_synced"] == 2
                    assert stats["properties_found"] == 2
                    assert mock_fetch.call_count == 2

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_partial_failure(self):
        """Test syncing when some domains fail but others succeed."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ],
                }
            ]
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    # First domain succeeds, second fails
                    mock_fetch.side_effect = [
                        mock_adagents_data,
                        AdagentsNotFoundError("404 Not Found"),
                    ]
                    mock_props.return_value = [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com", "example.org"])

                    # One success, one failure
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1
                    assert len(stats["errors"]) == 1
                    assert "example.org" in stats["errors"][0]

        mock_db_patcher.stop()

    def test_sync_properties_sync_wrapper(self):
        """Test that sync wrapper calls async implementation."""
        with patch.object(self.service, "sync_properties_from_adagents", new_callable=AsyncMock) as mock_async:
            mock_async.return_value = {
                "domains_synced": 1,
                "properties_found": 1,
                "tags_found": 0,
                "properties_created": 1,
                "properties_updated": 0,
                "tags_created": 0,
                "errors": [],
                "dry_run": False,
            }

            result = self.service.sync_properties_from_adagents_sync("tenant1", ["example.com"])

            assert result["domains_synced"] == 1
            mock_async.assert_called_once_with("tenant1", ["example.com"], False)
