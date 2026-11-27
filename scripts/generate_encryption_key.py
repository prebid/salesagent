#!/usr/bin/env python3
"""Generate encryption key for Gemini API key encryption.

This script generates a Fernet encryption key that should be stored
securely in the ENCRYPTION_KEY environment variable.

Usage:
    python scripts/generate_encryption_key.py

The generated key should be:
1. Added to .env as ENCRYPTION_KEY=<generated_key>; docs/security.md#secrets-configuration
   says which file each Docker Compose file reads
2. Backed up securely (lost key = lost API keys!)
3. Never committed to version control
4. Rotated periodically for security

Key Rotation:
-----------
To rotate encryption keys:
1. Generate new key: python scripts/generate_encryption_key.py
2. Set OLD_ENCRYPTION_KEY=<old_key> in environment
3. Set ENCRYPTION_KEY=<new_key> in environment
4. Run key rotation script: python scripts/rotate_encryption_key.py
"""

from cryptography.fernet import Fernet


def main():
    """Generate and display a new encryption key."""
    key = Fernet.generate_key().decode()

    print("=" * 80)
    print("GENERATED ENCRYPTION KEY")
    print("=" * 80)
    print()
    print(f"ENCRYPTION_KEY={key}")
    print()
    print("=" * 80)
    print("IMPORTANT: Save this key securely!")
    print("=" * 80)
    print()
    print("1. Add to .env (docs/security.md#secrets-configuration says which file each Docker Compose file reads):")
    print(f"   ENCRYPTION_KEY={key}")
    print()
    print("2. Backup securely:")
    print("   - Store in password manager (1Password, LastPass, etc.)")
    print("   - Store in secure vault (HashiCorp Vault, AWS Secrets Manager, etc.)")
    print("   - DO NOT commit to version control!")
    print()
    print("3. Run database migration to encrypt existing keys:")
    print("   uv run python migrate.py")
    print()
    print("WARNING: If you lose this key, you cannot decrypt existing API keys!")
    print("=" * 80)


if __name__ == "__main__":
    main()
