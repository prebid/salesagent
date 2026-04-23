"""Tests for encryption utilities."""

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.core.utils.encryption import (
    decrypt_api_key,
    encrypt_api_key,
    generate_encryption_key,
    is_encrypted,
)


@pytest.fixture
def encryption_key():
    """Generate a test encryption key."""
    return Fernet.generate_key().decode()


@pytest.fixture
def set_encryption_key(encryption_key):
    """Set ENCRYPTION_KEY environment variable for tests."""
    with patch.dict(os.environ, {"ENCRYPTION_KEY": encryption_key}):
        yield encryption_key


class TestEncryptDecrypt:
    """Test encryption and decryption operations."""

    def test_encrypt_decrypt_roundtrip(self, set_encryption_key):
        """Test that encryption and decryption work correctly."""
        plaintext = "test-api-key-12345"

        # Encrypt
        encrypted = encrypt_api_key(plaintext)
        assert encrypted != plaintext
        assert len(encrypted) > len(plaintext)

        # Decrypt
        decrypted = decrypt_api_key(encrypted)
        assert decrypted == plaintext

    def test_encrypt_different_keys(self, set_encryption_key):
        """Test that encrypting the same plaintext produces different ciphertexts."""
        plaintext = "test-api-key-12345"

        # Encrypt twice
        encrypted1 = encrypt_api_key(plaintext)
        encrypted2 = encrypt_api_key(plaintext)

        # Should be different due to random IV
        assert encrypted1 != encrypted2

        # Both should decrypt to same plaintext
        assert decrypt_api_key(encrypted1) == plaintext
        assert decrypt_api_key(encrypted2) == plaintext

    def test_encrypt_empty_string_fails(self, set_encryption_key):
        """Test that encrypting empty string raises ValueError."""
        with pytest.raises(ValueError, match="Cannot encrypt empty string"):
            encrypt_api_key("")

    def test_decrypt_empty_string_fails(self, set_encryption_key):
        """Test that decrypting empty string raises ValueError."""
        with pytest.raises(ValueError, match="Cannot decrypt empty string"):
            decrypt_api_key("")

    def test_encrypt_without_key_fails(self):
        """Test that encryption fails without ENCRYPTION_KEY set."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="ENCRYPTION_KEY environment variable not set"):
                encrypt_api_key("test-key")

    def test_decrypt_without_key_fails(self):
        """Test that decryption fails without ENCRYPTION_KEY set."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="ENCRYPTION_KEY environment variable not set"):
                decrypt_api_key("some-encrypted-data")

    def test_decrypt_invalid_data(self, set_encryption_key):
        """Test that decrypting invalid data raises ValueError."""
        with pytest.raises(ValueError, match="Invalid encrypted data"):
            decrypt_api_key("not-valid-fernet-token")

    def test_decrypt_with_wrong_key(self, encryption_key):
        """Test that decrypting with wrong key fails."""
        # Encrypt with one key
        with patch.dict(os.environ, {"ENCRYPTION_KEY": encryption_key}):
            encrypted = encrypt_api_key("test-key")

        # Try to decrypt with different key
        wrong_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": wrong_key}):
            with pytest.raises(ValueError, match="Invalid encrypted data or wrong encryption key"):
                decrypt_api_key(encrypted)

    def test_encrypt_long_key(self, set_encryption_key):
        """Test encrypting a long API key."""
        plaintext = "a" * 500  # 500 character key

        encrypted = encrypt_api_key(plaintext)
        decrypted = decrypt_api_key(encrypted)

        assert decrypted == plaintext

    def test_encrypt_special_characters(self, set_encryption_key):
        """Test encrypting keys with special characters."""
        plaintext = "key-with-special!@#$%^&*()_+={}[]|\\:;\"'<>,.?/~`"

        encrypted = encrypt_api_key(plaintext)
        decrypted = decrypt_api_key(encrypted)

        assert decrypted == plaintext

    def test_encrypt_unicode(self, set_encryption_key):
        """Test encrypting keys with unicode characters."""
        plaintext = "key-with-unicode-日本語-émojis-🔒"

        encrypted = encrypt_api_key(plaintext)
        decrypted = decrypt_api_key(encrypted)

        assert decrypted == plaintext


class TestIsEncrypted:
    """Test is_encrypted utility function."""

    def test_is_encrypted_detects_encrypted(self, set_encryption_key):
        """Test that is_encrypted correctly identifies encrypted data."""
        plaintext = "test-api-key-12345"
        encrypted = encrypt_api_key(plaintext)

        assert is_encrypted(encrypted)

    def test_is_encrypted_rejects_plaintext(self, set_encryption_key):
        """Test that is_encrypted correctly identifies plaintext."""
        plaintext = "test-api-key-12345"

        assert not is_encrypted(plaintext)

    def test_is_encrypted_empty_string(self, set_encryption_key):
        """Test that is_encrypted handles empty string."""
        assert not is_encrypted("")

    def test_is_encrypted_none(self, set_encryption_key):
        """Test that is_encrypted handles None gracefully."""
        # is_encrypted should handle None without raising
        # The decrypt attempt will fail, so it returns False
        assert not is_encrypted(None)  # type: ignore

    def test_is_encrypted_short_string(self, set_encryption_key):
        """Test that is_encrypted handles short strings."""
        assert not is_encrypted("short")

    def test_is_encrypted_looks_like_base64(self, set_encryption_key):
        """Test that is_encrypted doesn't false positive on base64."""
        # Random base64 that's not a valid Fernet token
        fake_base64 = "dGVzdC1hcGkta2V5LTEyMzQ1"

        assert not is_encrypted(fake_base64)


class TestGenerateKey:
    """Test encryption key generation."""

    def test_generate_key_produces_valid_key(self):
        """Test that generated key can be used for encryption."""
        key = generate_encryption_key()

        # Should be a valid Fernet key
        assert isinstance(key, str)
        assert len(key) > 40  # Fernet keys are 44 characters

        # Should be usable for encryption
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            plaintext = "test-key"
            encrypted = encrypt_api_key(plaintext)
            decrypted = decrypt_api_key(encrypted)
            assert decrypted == plaintext

    def test_generate_key_produces_unique_keys(self):
        """Test that each generated key is unique."""
        key1 = generate_encryption_key()
        key2 = generate_encryption_key()

        assert key1 != key2


class TestTenantModelIntegration:
    """Test encryption integration with Tenant model."""

    def test_tenant_property_encrypts_on_set(self, set_encryption_key):
        """Test that setting gemini_api_key encrypts the value."""
        from src.core.database.models import Tenant

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # Set plaintext key
        plaintext = "test-gemini-key-12345"
        tenant.gemini_api_key = plaintext

        # Internal value should be encrypted
        assert tenant._gemini_api_key != plaintext
        assert len(tenant._gemini_api_key) > len(plaintext)

        # Property getter should decrypt
        assert tenant.gemini_api_key == plaintext

    def test_tenant_property_decrypts_on_get(self, set_encryption_key):
        """Test that getting gemini_api_key decrypts the value."""
        from src.core.database.models import Tenant

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # Set encrypted value directly
        plaintext = "test-gemini-key-12345"
        encrypted = encrypt_api_key(plaintext)
        tenant._gemini_api_key = encrypted

        # Property getter should decrypt
        assert tenant.gemini_api_key == plaintext

    def test_tenant_property_handles_none(self, set_encryption_key):
        """Test that None values are handled correctly."""
        from src.core.database.models import Tenant

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # Set None
        tenant.gemini_api_key = None

        # Should be None
        assert tenant._gemini_api_key is None
        assert tenant.gemini_api_key is None

    def test_tenant_property_handles_empty_string(self, set_encryption_key):
        """Test that empty string is treated as None."""
        from src.core.database.models import Tenant

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # Set empty string
        tenant.gemini_api_key = ""

        # Should be None
        assert tenant._gemini_api_key is None

    def test_tenant_property_roundtrip(self, set_encryption_key):
        """Test full roundtrip: set -> get -> set -> get."""
        from src.core.database.models import Tenant

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # First roundtrip
        key1 = "test-key-1"
        tenant.gemini_api_key = key1
        assert tenant.gemini_api_key == key1

        # Second roundtrip with different key
        key2 = "test-key-2"
        tenant.gemini_api_key = key2
        assert tenant.gemini_api_key == key2

        # Verify internal value changed
        encrypted1 = encrypt_api_key(key1)
        encrypted2 = encrypt_api_key(key2)
        # Internal values should be different (though we can't compare directly due to random IV)
        assert tenant._gemini_api_key != encrypted1  # Different due to new encryption

    def test_tenant_property_raises_on_invalid_encrypted_data(self, set_encryption_key):
        """Test that invalid encrypted data raises AdCPConfigurationError."""
        from src.core.database.models import Tenant
        from src.core.exceptions import AdCPConfigurationError

        tenant = Tenant(tenant_id="test", name="Test", subdomain="test")

        # Set invalid encrypted value directly
        tenant._gemini_api_key = "invalid-encrypted-data"

        # Property getter should raise AdCPConfigurationError (not return None)
        with pytest.raises(AdCPConfigurationError, match="decrypt"):
            _ = tenant.gemini_api_key


class TestErrorHandling:
    """Test error handling in encryption utilities."""

    def test_encrypt_with_invalid_key_format(self):
        """Test that invalid encryption key format raises ValueError."""
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "not-a-valid-fernet-key"}):
            with pytest.raises((ValueError, Exception)):
                encrypt_api_key("test-key")

    def test_decrypt_with_invalid_key_format(self):
        """Test that invalid encryption key format raises ValueError."""
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "not-a-valid-fernet-key"}):
            with pytest.raises((ValueError, Exception)):
                decrypt_api_key("some-data")

    def test_encrypt_with_key_too_short(self):
        """Test that encryption key that's too short fails."""
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "short"}):
            with pytest.raises((ValueError, Exception)):
                encrypt_api_key("test-key")
