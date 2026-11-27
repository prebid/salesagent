# Encryption System for Sensitive Data

This document describes the encryption system used to protect sensitive data in the database.

## Overview

The system uses **Fernet symmetric encryption** (from the `cryptography` library) to encrypt sensitive API keys stored in the database. Currently, this includes:
- Tenant Gemini API keys (`tenants.gemini_api_key`)
- GAM service account JSON (`adapter_config.gam_service_account_json`)
- GAM OAuth refresh tokens (`adapter_config.gam_refresh_token`, encrypted from revision `9fdf18a56063`)

## Architecture

### Encryption Flow

```
Plaintext API Key → Fernet.encrypt() → Base64 Encoded Ciphertext → Database
Database → Base64 Encoded Ciphertext → Fernet.decrypt() → Plaintext API Key
```

### Key Components

1. **Encryption Utility** (`src/core/utils/encryption.py`)
   - `encrypt_api_key(plaintext: str) -> str`: Encrypts a plaintext API key
   - `decrypt_api_key(ciphertext: str) -> str`: Decrypts an encrypted API key
   - `is_encrypted(value: str) -> bool`: Checks if a value is encrypted
   - `generate_encryption_key() -> str`: Generates a new Fernet key

2. **Tenant Model Property** (`src/core/database/models.py`)
   - `Tenant.gemini_api_key`: Transparent property that encrypts on set, decrypts on get
   - Application code uses `tenant.gemini_api_key` normally
   - Database stores encrypted value in `tenants._gemini_api_key`

3. **Migration** (`alembic/versions/6c2d562e3ee4_encrypt_gemini_api_keys.py`)
   - Encrypts all existing plaintext API keys
   - Idempotent: detects already-encrypted keys and skips them
   - Reversible: downgrade decrypts keys back to plaintext

## Setup

### 1. Generate Encryption Key

```bash
# Generate a new encryption key
uv run python scripts/generate_encryption_key.py

# Output:
# ENCRYPTION_KEY=<44-character-base64-string>
```

### 2. Configure Environment

Add the generated key to `.env` ([Secrets configuration](security.md#secrets-configuration) says which file each Docker Compose file reads):

```bash
# .env
ENCRYPTION_KEY=RQhloVU0vooMBdE1d-TvFT5P3JC5dOwt7FPyWiyJbjQ=
```

**IMPORTANT**: Never commit this key to version control!

### 3. Backup Encryption Key

Store the encryption key securely:
- Password manager (1Password, LastPass, Bitwarden)
- Secrets vault (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager)
- Encrypted backup file (offline storage)

**WARNING**: If you lose the encryption key, you cannot decrypt existing API keys!

### 4. Run Migration

Encrypt existing API keys in the database:

```bash
# Set encryption key
export ENCRYPTION_KEY=<your-key>

# Run migrations
uv run python migrate.py
```

The migration will:
- Find all tenants with Gemini API keys
- Encrypt plaintext keys
- Skip already-encrypted keys
- Report summary of encrypted keys

## Usage

### Application Code

The encryption is transparent to application code:

```python
from src.core.database.models import Tenant
from src.core.database.database_session import get_db_session

# Set API key (automatically encrypted)
with get_db_session() as session:
    tenant = session.query(Tenant).filter_by(tenant_id="test").first()
    tenant.gemini_api_key = "plaintext-api-key-12345"
    session.commit()

# Get API key (automatically decrypted)
with get_db_session() as session:
    tenant = session.query(Tenant).filter_by(tenant_id="test").first()
    api_key = tenant.gemini_api_key  # Returns plaintext
    print(f"API Key: {api_key}")
```

### Direct Encryption/Decryption

For manual encryption/decryption (rare):

```python
from src.core.utils.encryption import encrypt_api_key, decrypt_api_key

# Encrypt
plaintext = "my-api-key"
encrypted = encrypt_api_key(plaintext)

# Decrypt
decrypted = decrypt_api_key(encrypted)
assert decrypted == plaintext
```

## Migration Details

### Upgrade (Encrypt Keys)

```bash
# Set encryption key
export ENCRYPTION_KEY=<your-key>

# Run migration
uv run python migrate.py
```

The migration:
1. Reads all tenants with `gemini_api_key` set
2. Checks if each key is already encrypted (idempotent)
3. Encrypts plaintext keys using Fernet
4. Updates database with encrypted values
5. Reports summary (e.g., "5 keys encrypted, 2 already encrypted")

### Downgrade (Decrypt Keys)

**WARNING**: This stores API keys in plaintext! Only use for rollback.

```bash
# Set same encryption key used to encrypt
export ENCRYPTION_KEY=<your-key>

# Downgrade migration
uv run alembic downgrade -1
```

The downgrade:
1. Reads all tenants with `gemini_api_key` set
2. Checks if each key is encrypted
3. Decrypts encrypted keys using Fernet
4. Updates database with plaintext values
5. Reports summary (e.g., "5 keys decrypted, 2 already plaintext")

## Security Considerations

### Encryption Key Storage

- **Environment Variable**: Store `ENCRYPTION_KEY` in `.env`; see [Secrets configuration](security.md#secrets-configuration)
- **Secrets Manager**: Use cloud secrets manager in production (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault)
- **Never Commit**: `.gitignore` already excludes `.env`
- **Backup**: Store backup in secure offline location

### Key Rotation

To rotate encryption keys (future implementation):

1. Generate new key: `python scripts/generate_encryption_key.py`
2. Set both keys:
   ```bash
   export OLD_ENCRYPTION_KEY=<old-key>
   export ENCRYPTION_KEY=<new-key>
   ```
3. Run rotation script: `python scripts/rotate_encryption_key.py` (to be implemented)
4. Update `.env` with new key
5. Remove old key from environment

### Access Control

- **Database Access**: Limit access to production database
- **Environment Variables**: Restrict access to production environment
- **Logs**: Never log plaintext API keys or encryption keys
- **Backups**: Encrypt database backups at rest

### Threat Model

**What This Protects Against:**
- ✅ Database dumps falling into wrong hands
- ✅ SQL injection accessing raw database values
- ✅ Insider threats (DBAs cannot read keys without encryption key)
- ✅ Compromised backups

**What This Does NOT Protect Against:**
- ❌ Compromised application server (has encryption key)
- ❌ Memory dumps of running application
- ❌ Compromised environment variables
- ❌ Compromised secrets manager

## Testing

Run encryption tests:

```bash
# Run all encryption tests
uv run pytest tests/unit/test_encryption.py -v

# Run specific test class
uv run pytest tests/unit/test_encryption.py::TestEncryptDecrypt -v

# Run with coverage
uv run pytest tests/unit/test_encryption.py --cov=src.core.utils.encryption
```

Test coverage:
- Encryption/decryption roundtrip
- Empty string and None handling
- Invalid data handling
- Wrong encryption key handling
- Tenant model property integration
- Migration idempotency

## Monitoring

### Logs to Monitor

- **Encryption failures**: `Failed to decrypt Gemini API key for tenant {tenant_id}`
- **Migration summary**: `Migration complete: X keys encrypted, Y already encrypted`
- **Key not set warnings**: `ENCRYPTION_KEY not set - skipping encryption`

### Metrics to Track

- Number of encrypted keys in database
- Decryption error rate
- Migration execution time

### Alerts to Configure

- 🚨 **Critical**: Encryption key not set in production
- ⚠️ **Warning**: Multiple decryption failures (wrong key?)
- ℹ️ **Info**: Migration completed successfully

## Troubleshooting

### "ENCRYPTION_KEY environment variable not set"

**Cause**: Missing `ENCRYPTION_KEY` in environment.

**Solution**:
1. Generate key: `python scripts/generate_encryption_key.py`
2. Add to `.env`: `ENCRYPTION_KEY=<key>`
3. Restart application

### "Invalid encrypted data or wrong encryption key"

**Cause**: Trying to decrypt with wrong encryption key.

**Solutions**:
1. Check `.env` has correct key
2. Verify key hasn't been changed since encryption
3. Check key rotation hasn't left some keys encrypted with old key
4. If keys are corrupted, you may need to re-enter them manually

### "Failed to decrypt Gemini API key for tenant X"

**Cause**: Database contains invalid encrypted data.

**Solutions**:
1. Check encryption key is correct
2. Manually re-enter API key for that tenant in Admin UI
3. Check database for data corruption

### Migration runs but doesn't encrypt any keys

**Cause**: Keys are already encrypted or no keys exist.

**Solutions**:
1. Check migration output: "Already encrypted (skipped): X"
2. Verify tenants have `gemini_api_key` set
3. Check database directly: `SELECT tenant_id, gemini_api_key FROM tenants`

## Future Enhancements

1. **Key Rotation**: Automated key rotation script
2. **Additional Fields**: Encrypt other sensitive fields (OAuth tokens, webhook secrets)
3. **Audit Logging**: Log all encryption/decryption operations
4. **Key Versioning**: Support multiple encryption keys with versioning
5. **Hardware Security Module (HSM)**: Integrate with HSM for key storage

## References

- [Cryptography Library Documentation](https://cryptography.io/en/latest/)
- [Fernet Specification](https://github.com/fernet/spec/)
- [OWASP Key Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html)
- [Database Encryption Best Practices](https://docs.microsoft.com/en-us/sql/relational-databases/security/encryption/encryption-best-practices)
