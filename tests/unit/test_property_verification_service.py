"""Unit tests for property verification service.

Tests the database wrapper logic around the adcp library's adagents functionality.
The actual adagents.json fetching, parsing, and validation is tested in the adcp library.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from adcp import AdagentsNotFoundError, AdagentsTimeoutError, AdagentsValidationError

from src.services.property_verification_service import PropertyVerificationService


class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_mock_db_session_with_property(property_data):
        """Create mock database session with property (SQLAlchemy 2.0 compatible)."""
        mock_session = Mock()
        mock_db_session_patcher = patch("src.services.property_verification_service.get_db_session")
        mock_db_session = mock_db_session_patcher.start()
        mock_db_session.return_value.__enter__.return_value = mock_session

        mock_property = Mock() if property_data else None
        if mock_property:
            for key, value in property_data.items():
                setattr(mock_property, key, value)

        # Mock SQLAlchemy 2.0 pattern: session.scalars(stmt).first()
        mock_scalars = Mock()
        mock_scalars.first.return_value = mock_property
        mock_session.scalars.return_value = mock_scalars

        return mock_db_session_patcher, mock_session, mock_property


class TestPropertyVerificationService:
    """Test PropertyVerificationService functionality.

    These tests focus on the database wrapper logic. The adcp library's
    adagents.json fetching, parsing, and validation are tested separately.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PropertyVerificationService()

    @pytest.mark.asyncio
    async def test_verify_property_success(self):
        """Test successful property verification."""
        # Mock database
        property_data = {
            "property_id": "prop1",
            "name": "Test Property",
            "publisher_domain": "example.com",
            "property_type": "website",
            "identifiers": [{"type": "domain", "value": "example.com"}],
        }
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(property_data)

        # Mock adcp library functions
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

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_verification_service.verify_agent_authorization") as mock_verify:
                mock_fetch.return_value = mock_adagents_data
                mock_verify.return_value = True

                # Test verification
                is_verified, error = await self.service._verify_property_async(
                    "tenant1", "prop1", "https://sales-agent.example.com"
                )

                # Verify results
                assert is_verified is True
                assert error is None

                # Verify adcp library called correctly
                mock_fetch.assert_called_once_with("example.com")
                mock_verify.assert_called_once_with(
                    adagents_data=mock_adagents_data,
                    agent_url="https://sales-agent.example.com",
                    property_type="website",
                    property_identifiers=[{"type": "domain", "value": "example.com"}],
                )

                # Verify database updated
                assert mock_property.verification_status == "verified"
                assert mock_property.verification_error is None

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_not_authorized(self):
        """Test property verification when agent is not authorized."""
        property_data = {
            "property_id": "prop1",
            "name": "Test Property",
            "publisher_domain": "example.com",
            "property_type": "website",
            "identifiers": [{"type": "domain", "value": "example.com"}],
        }
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(property_data)

        mock_adagents_data = {"authorized_agents": []}

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_verification_service.verify_agent_authorization") as mock_verify:
                mock_fetch.return_value = mock_adagents_data
                mock_verify.return_value = False

                is_verified, error = await self.service._verify_property_async(
                    "tenant1", "prop1", "https://sales-agent.example.com"
                )

                assert is_verified is False
                assert "not authorized" in error

                # Verify database updated with failure
                assert mock_property.verification_status == "failed"
                assert mock_property.verification_error is not None

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_adagents_not_found(self):
        """Test handling of missing adagents.json file (404)."""
        property_data = {
            "property_id": "prop1",
            "name": "Test Property",
            "publisher_domain": "example.com",
            "property_type": "website",
            "identifiers": [],
        }
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsNotFoundError("404 Not Found")

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            assert "not found (404)" in error

            # Verify database updated with failure
            assert mock_property.verification_status == "failed"

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_timeout(self):
        """Test handling of timeout when fetching adagents.json."""
        property_data = {
            "property_id": "prop1",
            "name": "Test Property",
            "publisher_domain": "example.com",
            "property_type": "website",
            "identifiers": [],
        }
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsTimeoutError("https://example.com/.well-known/adagents.json", 5.0)

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            assert "Timeout" in error

            # Verify database updated with failure
            assert mock_property.verification_status == "failed"

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_invalid_json(self):
        """Test handling of invalid adagents.json format."""
        property_data = {
            "property_id": "prop1",
            "name": "Test Property",
            "publisher_domain": "example.com",
            "property_type": "website",
            "identifiers": [],
        }
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsValidationError("Missing authorized_agents field")

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            assert "Invalid adagents.json" in error

            # Verify database updated with failure
            assert mock_property.verification_status == "failed"

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_not_found_in_db(self):
        """Test handling of property not found in database."""
        mock_db_patcher, mock_session, mock_property = MockSetup.create_mock_db_session_with_property(None)

        is_verified, error = await self.service._verify_property_async(
            "tenant1", "nonexistent", "https://sales-agent.example.com"
        )

        assert is_verified is False
        assert "Property not found" in error

        mock_db_patcher.stop()

    def test_verify_property_sync_wrapper(self):
        """Test that sync wrapper calls async implementation."""
        with patch.object(self.service, "_verify_property_async", new_callable=AsyncMock) as mock_async:
            mock_async.return_value = (True, None)

            result = self.service.verify_property("tenant1", "prop1", "https://agent.example.com")

            assert result == (True, None)
            mock_async.assert_called_once_with("tenant1", "prop1", "https://agent.example.com")

    def test_verify_all_properties(self):
        """Test bulk verification of all pending properties."""
        # Mock database with multiple properties
        property1 = Mock(property_id="prop1", name="Property 1")
        property2 = Mock(property_id="prop2", name="Property 2")

        mock_db_patcher = patch("src.services.property_verification_service.get_db_session")
        mock_db_session = mock_db_patcher.start()
        mock_session = Mock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock SQLAlchemy 2.0 pattern for all()
        mock_scalars = Mock()
        mock_scalars.all.return_value = [property1, property2]
        mock_session.scalars.return_value = mock_scalars

        # Mock verify_property to return success for first, failure for second
        with patch.object(self.service, "verify_property") as mock_verify:
            mock_verify.side_effect = [(True, None), (False, "Not authorized")]

            results = self.service.verify_all_properties("tenant1", "https://agent.example.com")

            assert results["total_checked"] == 2
            assert results["verified"] == 1
            assert results["failed"] == 1
            assert len(results["errors"]) == 1

        mock_db_patcher.stop()
