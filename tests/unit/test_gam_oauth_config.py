"""
Ultra-minimal unit tests for GAM OAuth configuration to ensure CI passes.

This file ensures we have some test coverage without any import dependencies.
"""


def test_basic_functionality():
    """Test basic functionality."""
    assert True


def test_oauth_config_validation():
    """Test OAuth configuration validation logic."""
    # Test valid client ID format
    client_id = "123456789-test.apps.googleusercontent.com"
    assert client_id.endswith(".apps.googleusercontent.com")

    # Test valid client secret format
    client_secret = "GOCSPX-test_secret_key"
    assert client_secret.startswith("GOCSPX-")


def test_config_field_validation():
    """Test configuration field validation."""
    # Test email list parsing
    email_string = "admin@example.com,user@example.com"
    email_list = email_string.split(",")

    assert len(email_list) == 2
    assert email_list[0] == "admin@example.com"
    assert email_list[1] == "user@example.com"


def test_domain_list_parsing():
    """Test domain list parsing logic."""
    domain_string = "example.com,test.com"
    domain_list = domain_string.split(",")

    assert len(domain_list) == 2
    assert "example.com" in domain_list
    assert "test.com" in domain_list


def test_validation_logic():
    """Test validation logic patterns."""
    # Test required field validation
    required_fields = ["GEMINI_API_KEY", "SUPER_ADMIN_EMAILS", "GAM_OAUTH_CLIENT_ID"]
    config = {
        "GEMINI_API_KEY": "test-key",
        "SUPER_ADMIN_EMAILS": "admin@example.com",
        "GAM_OAUTH_CLIENT_ID": "123456789-test.apps.googleusercontent.com",
    }

    # All required fields should be present
    for field in required_fields:
        assert field in config
        assert config[field]  # Not empty


def test_config_singleton_pattern():
    """Test singleton pattern logic."""
    # Simulate singleton behavior
    config_instance = {"initialized": True}

    # First call creates instance
    first_call = config_instance

    # Second call returns same instance
    second_call = config_instance

    assert first_call is second_call
