"""Unit tests for property verification service.

Tests the database wrapper logic around the adcp library's adagents functionality.
The actual adagents.json fetching, parsing, and validation is tested in the adcp library.
"""

from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from adcp import AdagentsNotFoundError, AdagentsTimeoutError, AdagentsValidationError

from src.services.property_verification_service import PropertyVerificationService


class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_mock_uow_with_property(property_data):
        """Patch the service's unit of work; its repository returns *property_data* as a row."""
        mock_uow_patcher = patch("src.services.property_verification_service.AuthorizedPropertyUoW")
        mock_uow_cls = mock_uow_patcher.start()
        mock_repo = mock_uow_cls.return_value.__enter__.return_value.authorized_properties

        mock_property = Mock() if property_data else None
        if mock_property:
            for key, value in property_data.items():
                setattr(mock_property, key, value)

        mock_repo.get_by_id.return_value = mock_property

        return mock_uow_patcher, mock_repo, mock_property


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
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(property_data)

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
                mock_repo.record_verification.assert_called_once_with(mock_property, "verified", None)

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
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(property_data)

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
                mock_repo.record_verification.assert_called_once_with(mock_property, "failed", error)

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
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsNotFoundError("404 Not Found")

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            # salesagent-grgc: a fixed, non-disclosing message -- never the library's
            # own exception text (which could carry a resolved IP / SSRF detail).
            assert error == "adagents.json not found for this domain"

            # Verify database updated with failure
            mock_repo.record_verification.assert_called_once_with(mock_property, "failed", error)

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
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsTimeoutError("https://example.com/.well-known/adagents.json", 5.0)

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            # salesagent-grgc: a fixed, non-disclosing message -- see above.
            assert error == "Timed out fetching adagents.json"

            # Verify database updated with failure
            mock_repo.record_verification.assert_called_once_with(mock_property, "failed", error)

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
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(property_data)

        with patch("src.services.property_verification_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsValidationError("Missing authorized_agents field")

            is_verified, error = await self.service._verify_property_async(
                "tenant1", "prop1", "https://sales-agent.example.com"
            )

            assert is_verified is False
            # salesagent-grgc: a fixed, non-disclosing message -- see above. This is
            # specifically the branch that could carry a resolved IP / SSRF range
            # classification via AdagentsValidationError, so it must never echo str(e).
            assert error == "adagents.json could not be validated"

            # Verify database updated with failure
            mock_repo.record_verification.assert_called_once_with(mock_property, "failed", error)

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_verify_property_not_found_in_db(self):
        """Test handling of property not found in database."""
        mock_db_patcher, mock_repo, mock_property = MockSetup.create_mock_uow_with_property(None)

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
        mock_db_patcher, mock_repo, _ = MockSetup.create_mock_uow_with_property(None)
        mock_repo.list_pending.return_value = [("prop1", "Property 1"), ("prop2", "Property 2")]

        # Mock verify_property to return success for first, failure for second
        with patch.object(self.service, "verify_property") as mock_verify:
            mock_verify.side_effect = [(True, None), (False, "Not authorized")]

            results = self.service.verify_all_properties("tenant1", "https://agent.example.com")

            assert results == {
                "total_checked": 2,
                "verified": 1,
                "failed": 1,
                "errors": ["Property 2: Not authorized"],
            }
            mock_verify.assert_has_calls(
                [
                    call("tenant1", "prop1", "https://agent.example.com"),
                    call("tenant1", "prop2", "https://agent.example.com"),
                ]
            )

        mock_db_patcher.stop()
