# UI Tests

This directory contains UI tests for the AdCP Admin interface.

## Test Authentication Mode

The `test_auth_mode.py` file tests the authentication bypass mode for automated testing.

### Running the Tests

1. Enable test mode and optionally customize credentials:
   ```bash
   export ADCP_AUTH_TEST_MODE=true

   # Optional: Customize test credentials
   export TEST_SUPER_ADMIN_PASSWORD=my_secure_test_pass
   export TEST_TENANT_ADMIN_PASSWORD=another_secure_pass
   export TEST_TENANT_USER_PASSWORD=user_secure_pass
   ```

2. Start services with test mode enabled:
   ```bash
   # Copy override file if not already done
   cp docker-compose.override.example.yml docker-compose.override.yml

   # Edit override file to enable test mode
   # Then start services
   docker-compose up
   ```

3. Run the tests:
   ```bash
   uv run pytest tests/ui/test_auth_mode.py -v
   ```

### Test Users (Defaults)

| Role | Email | Password | Environment Variables |
|------|-------|----------|---------------------|
| Super Admin | `test_super_admin@example.com` | `test123` | `TEST_SUPER_ADMIN_EMAIL` / `TEST_SUPER_ADMIN_PASSWORD` |
| Tenant Admin | `test_tenant_admin@example.com` | `test123` | `TEST_TENANT_ADMIN_EMAIL` / `TEST_TENANT_ADMIN_PASSWORD` |
| Tenant User | `test_tenant_user@example.com` | `test123` | `TEST_TENANT_USER_EMAIL` / `TEST_TENANT_USER_PASSWORD` |

**Note**: Use environment variables to set more secure passwords in your test environments.

**WARNING**: Never enable test mode in production!
