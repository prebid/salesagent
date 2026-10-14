# Admin UI OAuth Testing Guide

This document explains how to run and understand the OAuth tests for the Admin UI.

## Overview

The `test_admin_ui_oauth.py` file contains comprehensive tests for the Google OAuth authentication system used in the Admin UI. It tests:

- Google OAuth login flows
- Authentication decorators
- Session management
- Authorization levels (super admin vs tenant admin)
- Multi-tenant access scenarios

## Running the Tests

### Prerequisites

Make sure you have pytest and pytest-mock installed:

```bash
pip install pytest pytest-mock
```

### Run All OAuth Tests

```bash
pytest test_admin_ui_oauth.py -v
```

### Run Specific Test Classes

```bash
# Test only OAuth login flows
pytest test_admin_ui_oauth.py::TestOAuthLogin -v

# Test only OAuth callbacks
pytest test_admin_ui_oauth.py::TestOAuthCallback -v

# Test only authorization decorators
pytest test_admin_ui_oauth.py::TestAuthorizationDecorators -v

# Test only helper functions
pytest test_admin_ui_oauth.py::TestHelperFunctions -v

# Test only session management
pytest test_admin_ui_oauth.py::TestSessionManagement -v
```

### Run with Coverage

```bash
pytest test_admin_ui_oauth.py --cov=admin_ui --cov-report=html
```

## Test Structure

### Fixtures

The tests use several pytest fixtures for setup:

- `client`: Creates a Flask test client with testing configuration
- `mock_db`: Mocks the database connection
- `mock_google_oauth`: Mocks the Google OAuth object

### Test Classes

1. **TestOAuthLogin**: Tests the initial login pages and OAuth redirect flows
   - Login page rendering
   - Tenant-specific login pages
   - OAuth redirect initiation

2. **TestOAuthCallback**: Tests handling of OAuth callbacks from Google
   - Super admin authentication
   - Tenant admin authentication (single and multiple tenants)
   - Unauthorized user handling
   - Database user authentication

3. **TestAuthorizationDecorators**: Tests the `@require_auth` decorator
   - Unauthenticated user redirection
   - Authenticated user access
   - Admin-only route protection

4. **TestHelperFunctions**: Tests authorization helper functions
   - `is_super_admin()` with emails and domains

5. **TestSessionManagement**: Tests session-related functionality
   - Logout functionality
   - Multi-tenant selection

## Mocking Strategy

The tests use extensive mocking to avoid external dependencies:

### OAuth Mocking
```python
# Mock Google OAuth responses
mock_google_oauth.authorize_access_token.return_value = {
    'userinfo': {
        'email': 'user@example.com',
        'name': 'Test User'
    }
}
```

### Database Mocking
```python
# Mock database queries
cursor = MagicMock()
cursor.fetchone.return_value = ('Test Tenant',)
mock_db.execute.return_value = cursor
```

### Authorization Mocking
```python
# Mock authorization checks
with patch('admin_ui.is_super_admin', return_value=True):
    # Test super admin flow
```

## Test Scenarios

### 1. Super Admin Login Flow
- User visits `/login`
- Clicks "Sign in with Google"
- Google returns user info
- System recognizes email as super admin
- User is redirected to dashboard with full access

### 2. Tenant Admin Login Flow (Single Tenant)
- User visits `/tenant/tenant-id/login`
- Authenticates with Google
- System finds user has access to only this tenant
- User is redirected to tenant management page

### 3. Multi-Tenant Access Flow
- User authenticates with Google
- System finds user has access to multiple tenants
- User is shown tenant selection page
- User selects a tenant and gains access

### 4. Database User Authentication
- Tenant admin stored in `users` table
- User authenticates via tenant-specific login
- System validates against database
- User gains role-based access

### 5. Unauthorized Access Handling
- User attempts to authenticate
- Email not in authorized lists
- User shown error message
- No session created

## Adding New Tests

To add new OAuth-related tests:

1. Choose the appropriate test class based on what you're testing
2. Use the existing fixtures for setup
3. Mock external dependencies (OAuth, database)
4. Test both success and failure paths
5. Verify session state changes

Example template:
```python
def test_new_oauth_scenario(self, client, mock_google_oauth, mock_db):
    """Test description."""
    # Setup mocks
    mock_google_oauth.authorize_access_token.return_value = {
        'userinfo': {'email': 'test@example.com'}
    }

    # Execute test
    response = client.get('/auth/google/callback')

    # Verify results
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess['authenticated'] is True
```

## Debugging Failed Tests

If tests fail:

1. Check mock setup - ensure mocks return expected values
2. Verify session state - use `client.session_transaction()`
3. Check response data - `assert b'expected text' in response.data`
4. Use pytest's `-s` flag to see print statements
5. Use `--pdb` flag to drop into debugger on failure

## Integration with CI/CD

These tests should be run in your CI/CD pipeline:

```yaml
# Example GitHub Actions
- name: Run OAuth Tests
  run: |
    pip install -r requirements.txt
    pytest test_admin_ui_oauth.py -v --cov=admin_ui
```

## Known Limitations

1. Tests use mocking extensively - integration tests with real OAuth would require additional setup
2. Rate limiting and network errors are not tested
3. OAuth token refresh flows are not covered
4. Some edge cases around session expiry may need additional tests
