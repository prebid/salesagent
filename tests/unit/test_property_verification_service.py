"""Unit tests for property verification service."""

from unittest.mock import Mock, patch

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

    @staticmethod
    def create_mock_http_response(response_data):
        """Create mock HTTP response."""
        mock_response = Mock()
        mock_response.json.return_value = response_data
        return mock_response


class TestPropertyVerificationService:
    """Test PropertyVerificationService functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PropertyVerificationService()

    def test_domain_matches_exact(self):
        """Test exact domain matching."""
        assert self.service._domain_matches("example.com", "example.com")
        assert not self.service._domain_matches("example.com", "other.com")

    def test_domain_matches_common_subdomains(self):
        """Test common subdomain matching rules."""
        # Base domain should match common subdomains
        assert self.service._domain_matches("www.example.com", "example.com")
        assert self.service._domain_matches("m.example.com", "example.com")
        assert self.service._domain_matches("mobile.example.com", "example.com")
        assert self.service._domain_matches("amp.example.com", "example.com")

        # But not other subdomains
        assert not self.service._domain_matches("blog.example.com", "example.com")
        assert not self.service._domain_matches("api.example.com", "example.com")

    def test_domain_matches_wildcard(self):
        """Test wildcard domain matching."""
        # Wildcard should match any subdomain
        assert self.service._domain_matches("www.example.com", "*.example.com")
        assert self.service._domain_matches("blog.example.com", "*.example.com")
        assert self.service._domain_matches("api.example.com", "*.example.com")

        # Should also match base domain
        assert self.service._domain_matches("example.com", "*.example.com")

        # Should not match different domains
        assert not self.service._domain_matches("other.com", "*.example.com")

    def test_domain_matches_specific_subdomain(self):
        """Test specific subdomain matching."""
        # Specific subdomain should only match exactly
        assert self.service._domain_matches("blog.example.com", "blog.example.com")
        assert not self.service._domain_matches("www.example.com", "blog.example.com")
        assert not self.service._domain_matches("example.com", "blog.example.com")

    def test_normalize_domain(self):
        """Test domain normalization."""
        assert self.service._normalize_domain("Example.Com") == "example.com"
        assert self.service._normalize_domain("  example.com  ") == "example.com"
        assert self.service._normalize_domain("https://example.com") == "example.com"
        assert self.service._normalize_domain("http://www.example.com/path") == "www.example.com"

    def test_urls_match(self):
        """Test URL matching logic."""
        assert self.service._urls_match("https://example.com/api", "http://example.com/api")
        assert self.service._urls_match("https://example.com/api/", "https://example.com/api")
        assert not self.service._urls_match("https://example.com/api", "https://other.com/api")

    def test_identifier_values_match_domain(self):
        """Test identifier value matching for domains."""
        assert self.service._identifier_values_match("example.com", "example.com", "domain")
        assert self.service._identifier_values_match("www.example.com", "example.com", "domain")
        assert not self.service._identifier_values_match("other.com", "example.com", "domain")

    def test_identifier_values_match_non_domain(self):
        """Test identifier value matching for non-domain types."""
        assert self.service._identifier_values_match("com.example.app", "com.example.app", "bundle_id")
        assert not self.service._identifier_values_match("com.example.app", "com.other.app", "bundle_id")

    def test_identifiers_match(self):
        """Test identifier matching between property and agent."""
        property_identifiers = [
            {"type": "domain", "value": "example.com"},
            {"type": "bundle_id", "value": "com.example.app"},
        ]

        agent_identifiers = [{"type": "domain", "value": "example.com"}]

        assert self.service._identifiers_match(property_identifiers, agent_identifiers)

        # No matching identifiers
        agent_identifiers_no_match = [{"type": "domain", "value": "other.com"}]

        assert not self.service._identifiers_match(property_identifiers, agent_identifiers_no_match)

    def test_property_matches(self):
        """Test property matching against agent properties."""
        # Mock property object
        property_obj = Mock()
        property_obj.property_type = "website"
        property_obj.identifiers = [{"type": "domain", "value": "example.com"}]

        # Agent property that matches
        agent_properties = [{"property_type": "website", "identifiers": [{"type": "domain", "value": "example.com"}]}]

        assert self.service._property_matches(property_obj, agent_properties)

        # Agent property with different type
        agent_properties_wrong_type = [
            {"property_type": "mobile_app", "identifiers": [{"type": "domain", "value": "example.com"}]}
        ]

        assert not self.service._property_matches(property_obj, agent_properties_wrong_type)

    def test_check_agent_authorization(self):
        """Test agent authorization checking."""
        # Mock property object
        property_obj = Mock()
        property_obj.property_type = "website"
        property_obj.identifiers = [{"type": "domain", "value": "example.com"}]

        # Agents list with matching agent
        agents = [
            {
                "url": "https://sales-agent.example.com",
                "properties": [
                    {"property_type": "website", "identifiers": [{"type": "domain", "value": "example.com"}]}
                ],
            }
        ]

        assert self.service._check_agent_authorization(agents, "https://sales-agent.example.com", property_obj)

        # No matching agent URL
        assert not self.service._check_agent_authorization(agents, "https://other-agent.example.com", property_obj)

    def test_verify_property_success(self):
        """Test successful property verification."""
        # Use centralized mock setup
        property_data = {
            "property_type": "website",
            "identifiers": [{"type": "domain", "value": "example.com"}],
            "publisher_domain": "example.com",
        }

        response_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.scope3.com",
                    "properties": [
                        {"property_type": "website", "identifiers": [{"type": "domain", "value": "example.com"}]}
                    ],
                }
            ]
        }

        # Setup mocks using centralized helper
        db_patcher, _, _ = MockSetup.create_mock_db_session_with_property(property_data)

        # Mock the service's session.get method
        with patch.object(self.service.session, "get") as mock_get:
            mock_get.return_value = MockSetup.create_mock_http_response(response_data)

            try:
                # Test verification
                is_verified, error = self.service.verify_property("tenant1", "prop1", "https://sales-agent.scope3.com")

                assert is_verified
                assert error is None

                # Verify HTTP request was made
                mock_get.assert_called_once_with("https://example.com/.well-known/adagents.json", timeout=10)
            finally:
                db_patcher.stop()

    def test_verify_property_not_authorized(self):
        """Test property verification when agent is not authorized."""
        # Use centralized mock setup
        property_data = {
            "property_type": "website",
            "identifiers": [{"type": "domain", "value": "example.com"}],
            "publisher_domain": "example.com",
        }

        response_data = {
            "authorized_agents": [
                {
                    "url": "https://other-agent.com",
                    "properties": [
                        {"property_type": "website", "identifiers": [{"type": "domain", "value": "example.com"}]}
                    ],
                }
            ]
        }

        # Setup mocks using centralized helper
        db_patcher, _, _ = MockSetup.create_mock_db_session_with_property(property_data)

        with patch.object(self.service.session, "get") as mock_get:
            mock_get.return_value = MockSetup.create_mock_http_response(response_data)

            try:
                # Test verification
                is_verified, error = self.service.verify_property("tenant1", "prop1", "https://sales-agent.scope3.com")

                assert not is_verified
                assert "not found in authorized agents list" in error
            finally:
                db_patcher.stop()

    def test_verify_property_http_error(self):
        """Test property verification when HTTP request fails."""
        from requests.exceptions import RequestException

        # Use centralized mock setup
        property_data = {"publisher_domain": "example.com"}

        # Setup mocks using centralized helper
        db_patcher, _, _ = MockSetup.create_mock_db_session_with_property(property_data)

        with patch.object(self.service.session, "get") as mock_get:
            mock_get.side_effect = RequestException("Connection failed")

            try:
                # Test verification
                is_verified, error = self.service.verify_property("tenant1", "prop1", "https://sales-agent.scope3.com")

                assert not is_verified
                assert "Failed to fetch adagents.json" in error
            finally:
                db_patcher.stop()

    def test_verify_property_not_found(self):
        """Test property verification when property doesn't exist."""
        # Use centralized mock setup with None property
        with patch("src.services.property_verification_service.get_db_session") as mock_db_session:
            mock_session = Mock()
            mock_db_session.return_value.__enter__.return_value = mock_session

            # Mock SQLAlchemy 2.0 pattern: session.scalars(stmt).first() returns None
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_session.scalars.return_value = mock_scalars

            # Test verification
            is_verified, error = self.service.verify_property("tenant1", "prop1", "https://sales-agent.scope3.com")

        assert not is_verified
        assert error == "Property not found"
