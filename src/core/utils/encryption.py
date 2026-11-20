"""Encryption utilities for sensitive data."""

import logging

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_encryption_key() -> bytes:
    """Get encryption key from environment variable.

    Returns:
        Encryption key as bytes.

    Raises:
        ValueError: If ENCRYPTION_KEY environment variable is not set.
    """
    key = get_settings().auth.encryption_key
    if not key:
        raise ValueError(
            "ENCRYPTION_KEY environment variable not set. "
            "Generate a key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return key.encode()


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt API key for storage.

    Args:
        plaintext: API key in plaintext.

    Returns:
        Encrypted API key as base64-encoded string.

    Raises:
        ValueError: If ENCRYPTION_KEY is not set or plaintext is empty.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt empty string")

    key = _get_encryption_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(plaintext.encode())
    return encrypted.decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt API key for use.

    Args:
        ciphertext: Encrypted API key as base64-encoded string.

    Returns:
        Decrypted API key in plaintext.

    Raises:
        ValueError: If ENCRYPTION_KEY is not set, ciphertext is empty, or decryption fails.
    """
    if not ciphertext:
        raise ValueError("Cannot decrypt empty string")

    try:
        key = _get_encryption_key()
        fernet = Fernet(key)
        decrypted = fernet.decrypt(ciphertext.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.error("Failed to decrypt API key - invalid token or wrong encryption key")
        raise ValueError("Invalid encrypted data or wrong encryption key")
    except Exception as e:
        logger.error(f"Unexpected error during decryption: {e}")
        raise ValueError(f"Decryption failed: {e}")


def is_encrypted(value: str | None) -> bool:
    """Check if a value appears to be encrypted.

    This is a heuristic check based on Fernet token format.
    Fernet tokens are base64-encoded and start with 'gAAAAA'.

    Args:
        value: String to check, or None.

    Returns:
        True if value appears to be encrypted, False otherwise.
    """
    if not value:
        return False

    # Fernet tokens are base64 and have a specific prefix
    # Try to decrypt - if it works, it's encrypted
    try:
        decrypt_api_key(value)
        return True
    except Exception:
        return False


def generate_encryption_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        New encryption key as string.
    """
    return Fernet.generate_key().decode()
