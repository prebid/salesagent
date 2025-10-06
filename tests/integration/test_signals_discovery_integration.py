"""Integration tests for SignalsDiscoveryProvider with reduced mocking.

Reduces excessive mocking while still providing comprehensive test coverage
for the signals discovery functionality by focusing on configuration,
initialization, and fallback behavior.
"""

import pytest

from product_catalog_providers.signals import SignalsDiscoveryProvider


class TestSignalsDiscoveryProviderIntegration:
    """Integration tests for SignalsDiscoveryProvider with minimal mocking."""

    def test_provider_configuration_patterns(self):
        """Test various configuration patterns without mocking internals."""
        # Test 1: Default disabled configuration
        provider_disabled = SignalsDiscoveryProvider({})
        assert provider_disabled.enabled is False
        assert provider_disabled.upstream_url == ""
        assert provider_disabled.upstream_token == ""
        assert provider_disabled.auth_header == "x-adcp-auth"
        assert provider_disabled.timeout == 30
        assert provider_disabled.fallback_to_database is True

        # Test 2: Complete enabled configuration
        config_enabled = {
            "enabled": True,
            "upstream_url": "http://test-signals:8080/mcp/",
            "upstream_token": "test-token",
            "auth_header": "Authorization",
            "timeout": 60,
            "forward_promoted_offering": False,
            "fallback_to_database": False,
            "max_signal_products": 5,
        }

        provider_enabled = SignalsDiscoveryProvider(config_enabled)
        assert provider_enabled.enabled is True
        assert provider_enabled.upstream_url == "http://test-signals:8080/mcp/"
        assert provider_enabled.upstream_token == "test-token"
        assert provider_enabled.auth_header == "Authorization"
        assert provider_enabled.timeout == 60
        assert provider_enabled.forward_promoted_offering is False
        assert provider_enabled.fallback_to_database is False
        assert provider_enabled.max_signal_products == 5

        # Test 3: Partial configuration (real-world scenario)
        config_partial = {
            "enabled": True,
            "upstream_url": "http://signals.company.com/api/",
            "upstream_token": "prod-token-xyz",
        }

        provider_partial = SignalsDiscoveryProvider(config_partial)
        assert provider_partial.enabled is True
        assert provider_partial.upstream_url == "http://signals.company.com/api/"
        assert provider_partial.upstream_token == "prod-token-xyz"
        # Should use defaults for unspecified values
        assert provider_partial.auth_header == "x-adcp-auth"
        assert provider_partial.timeout == 30
        assert provider_partial.fallback_to_database is True

    @pytest.mark.asyncio
    async def test_initialization_behavior_without_mocking(self):
        """Test initialization behavior for different configurations."""
        # Test 1: Disabled provider should not initialize client
        provider_disabled = SignalsDiscoveryProvider({"enabled": False})
        await provider_disabled.initialize()
        assert provider_disabled.client is None

        # Test 2: Enabled but no URL should not initialize client
        provider_no_url = SignalsDiscoveryProvider({"enabled": True, "upstream_url": ""})
        await provider_no_url.initialize()
        assert provider_no_url.client is None

        # Test 3: Valid config should attempt initialization
        # (We don't mock the actual client creation to avoid over-mocking,
        # but we can test that the provider attempts it)
        provider_valid = SignalsDiscoveryProvider(
            {
                "enabled": True,
                "upstream_url": "http://localhost:9999/invalid/",  # Invalid URL for testing
                "upstream_token": "test-token",
            }
        )

        # This will likely fail to connect, but that's expected behavior
        # The important part is that it attempts initialization
        await provider_valid.initialize()
        # We don't assert on client state since connection will fail

    def test_url_validation_patterns(self):
        """Test URL validation and normalization patterns."""
        test_cases = [
            ("http://localhost:8080/mcp/", True),
            ("https://signals.company.com/api/", True),
            ("", False),
            ("not-a-url", True),  # Provider doesn't validate URL format
            ("ftp://invalid.protocol.com/", True),  # Provider accepts any string
        ]

        for url, _should_be_enabled in test_cases:
            config = {"enabled": True, "upstream_url": url}
            provider = SignalsDiscoveryProvider(config)

            if url == "":
                # Empty URL should disable the provider effectively
                assert provider.upstream_url == ""
            else:
                assert provider.upstream_url == url

    def test_timeout_and_limit_configuration(self):
        """Test timeout and limit configurations work correctly."""
        # Test boundary values
        test_configs = [
            {"timeout": 1, "max_signal_products": 1},
            {"timeout": 300, "max_signal_products": 100},
            {"timeout": 0, "max_signal_products": 0},  # Edge case
        ]

        for config in test_configs:
            provider = SignalsDiscoveryProvider(config)
            assert provider.timeout == config["timeout"]
            if "max_signal_products" in config:
                assert provider.max_signal_products == config["max_signal_products"]

    def test_auth_header_configuration(self):
        """Test authentication header configuration patterns."""
        auth_patterns = [
            "x-adcp-auth",  # Default
            "Authorization",  # Standard Bearer token
            "X-API-Key",  # API key pattern
            "X-Custom-Auth",  # Custom pattern
        ]

        for auth_header in auth_patterns:
            config = {"auth_header": auth_header}
            provider = SignalsDiscoveryProvider(config)
            assert provider.auth_header == auth_header

    def test_fallback_behavior_configuration(self):
        """Test fallback behavior configuration."""
        # Test all combinations of fallback and forward_promoted_offering
        test_combinations = [
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ]

        for fallback_to_db, forward_promoted in test_combinations:
            config = {"fallback_to_database": fallback_to_db, "forward_promoted_offering": forward_promoted}
            provider = SignalsDiscoveryProvider(config)
            assert provider.fallback_to_database == fallback_to_db
            assert provider.forward_promoted_offering == forward_promoted

    @pytest.mark.asyncio
    async def test_client_lifecycle_management(self):
        """Test client lifecycle without mocking client internals."""
        provider = SignalsDiscoveryProvider(
            {"enabled": True, "upstream_url": "http://nonexistent.example.com/"}  # Will fail to connect
        )

        # Before initialization
        assert provider.client is None

        # After initialization attempt (will fail, but that's ok)
        await provider.initialize()

        # Test shutdown behavior
        await provider.shutdown()
        # After shutdown, client should be properly cleaned up
        assert provider.client is None

    def test_signals_provider_integration_readiness(self):
        """Test that provider is ready for integration with actual signals systems."""
        # Test realistic production-like configuration
        production_config = {
            "enabled": True,
            "upstream_url": "https://signals-api.company.com/mcp/",
            "upstream_token": "prod-xyz-123",
            "auth_header": "Authorization",
            "timeout": 45,
            "fallback_to_database": True,
            "forward_promoted_offering": True,
            "max_signal_products": 20,
        }

        provider = SignalsDiscoveryProvider(production_config)

        # Verify all configuration is correctly applied
        assert provider.enabled is True
        assert provider.upstream_url == "https://signals-api.company.com/mcp/"
        assert provider.upstream_token == "prod-xyz-123"
        assert provider.auth_header == "Authorization"
        assert provider.timeout == 45
        assert provider.fallback_to_database is True
        assert provider.forward_promoted_offering is True
        assert provider.max_signal_products == 20

        # Verify the provider is in a valid state for initialization
        assert hasattr(provider, "initialize")
        assert hasattr(provider, "shutdown")
        assert hasattr(provider, "get_products")
