# Security and authentication

This document is the reference for the security architecture of the sales agent: admin authentication, tenant and principal isolation, OAuth and OIDC configuration, and secrets handling. It describes what the system does and what you must configure or test when you change authentication code. Outbound HTTP and SSRF protection appear here only in summary; [Outbound egress](security/outbound-egress.md) is the full reference.

## Contents

- [Admin authentication architecture](#admin-authentication-architecture) — environment-first super-admin check with database fallback
- [Tenant registration security](#tenant-registration-security) — subdomain assignment, branded URLs, ad server checks
- [Access control patterns](#access-control-patterns) — super admins, tenant users, principal isolation, audit trail
- [Outbound egress (SSRF)](#outbound-egress-ssrf) — the single gateway for outbound HTTP
- [Security testing requirements](#security-testing-requirements)
- [OAuth cross-domain authentication](#oauth-cross-domain-authentication) — why cross-domain login fails, and the workaround
- [Generic OIDC provider support](#generic-oidc-provider-support) — configuration for any OIDC provider, or Google OAuth
- [Secrets configuration](#secrets-configuration) — the `.env.secrets` file and handling practices

## Admin authentication architecture

The admin authentication system uses an environment-first approach with a database fallback for secure access control.

### Implementation

```python
def is_super_admin(email):
    """Environment-first authentication with database fallback."""
    # 1. Check environment variables first (deployment-time config)
    env_emails = os.environ.get("SUPER_ADMIN_EMAILS", "")
    if env_emails:
        emails_list = [e.strip().lower() for e in env_emails.split(",") if e.strip()]
        if email.lower() in emails_list:
            return True

    # 2. Fallback to database configuration (runtime config)
    try:
        with get_db_session() as db:
            stmt = select(TenantManagementConfig).filter_by(
                config_key="super_admin_emails"
            )
            config = db.scalars(stmt).first()
            if config and config.config_value:
                db_emails = [e.strip().lower() for e in config.config_value.split(",")]
                return email.lower() in db_emails
    except Exception as e:
        logger.error(f"Database auth check failed: {e}")

    return False
```

### Session optimization

The session layer avoids redundant checks in the following ways:

- **Session caching**: The session caches super admin status to avoid redundant database calls.
- **Trusted session state**: `require_tenant_access()` checks the session first, then validates if needed.
- **Automatic caching**: The system updates the session when it confirms admin status.

## Tenant registration security

### Subdomain assignment

To prevent subdomain squatting and brand impersonation, the system generates tenant subdomains automatically from random universally unique identifiers (UUIDs):

- Subdomains are 8-character hexadecimal strings, for example `a7f3d92b`.
- The system takes the first 8 characters of a UUID4 value.
- Users cannot choose custom subdomains during signup.
- This approach eliminates the risk of a user claiming a subdomain like `nytimes` or `cnn`.

### Branded URLs

Publishers who want branded URLs can use virtual hosts (custom domains). Configure a custom domain, for example `sales.publisher.com`, in the tenant settings. The Approximated proxy service verifies domain ownership. This approach provides branding without security risks.

### Ad server configuration check

Tenants without a configured ad server show a "Pending Configuration" page instead of active agent endpoints. This prevents incomplete registrations from appearing operational.

## Access control patterns

### Super admins

Super admins have full access to all tenants and can create, modify, and delete tenants and users. You configure super admins through environment variables or the database.

### Tenant users

Tenant users have access to specific tenants through the `User` model. They can manage their own tenant's configuration but cannot access other tenants' data.

### Principal isolation

Each advertiser (principal) has isolated access tokens. Tokens are scoped to a specific tenant and cannot access other tenants or principals.

Durable `get_task` / `complete_task` are principal-scoped at the workflow
repository seam (same-tenant sibling → `REFERENCE_NOT_FOUND`); `list_tasks`
is still tenant-scoped and may surface sibling-owned rows — tracked in #1808

### Audit trail

The system logs all admin actions to the `audit_logs` table, including the timestamp, user, action, and result. The audit trail supports compliance and security monitoring.

## Outbound egress (SSRF)

Every outbound HTTP request goes through a single module,
`src/core/security/outbound_http.py` (`send` / `asend`), which makes the
address, TLS, redirect, and retry decisions together. This design prevents
server-side request forgery (SSRF). The module's docstring records the few
permitted exceptions, with reasons. Call sites do not validate URLs. A
private-IP check or hostname blocklist at a call site is a defect, not defense
in depth: it reintroduces the resolve-then-connect TOCTOU (time-of-check to
time-of-use) vulnerability that the module closes by pinning the resolved IP
address.

The module refuses requests based on two checks that share one address
predicate: `check_registration` (validates a buyer-supplied URL before
storage, without DNS resolution) and `resolve_for_dial` (resolves the hostname
once, pins the resolved IP address, and refuses based on it). The SDK does not
classify six supplement ranges — carrier-grade NAT (CGNAT) and five
others. The module refuses these ranges under every configuration, including
the test-only `ADCP_OUTBOUND_ALLOW_PRIVATE` override, because no other layer
defends them.

Enforcement has three layers:

- The egress modules bind their imports privately, so a re-export raises `ImportError`.
- `ruff-egress.toml` bans the raw client modules.
- The check runs with `--ignore-noqa`, so a file cannot exempt itself. Exemptions are rows in `[lint.per-file-ignores]` that land in a reviewed diff.

For the full architecture, including how to add a new outbound call, see
[Outbound egress](security/outbound-egress.md).

## Security testing requirements

All authentication changes must include tests for the following:

- Session timeout behavior
- Revalidation logic
- Environment versus database precedence
- Audit logging completeness
- Session security headers
- Cross-site request forgery (CSRF) protection

**Test location**: `tests/integration/test_product_deletion.py` contains comprehensive authentication tests, including validation of the environment-first approach.

## OAuth cross-domain authentication

OAuth authentication works within the `sales-agent.example.com` domain and its subdomains. OAuth authentication from external domains, such as `test-agent.adcontextprotocol.org`, is limited by browser cookie security restrictions.

### How OAuth works

#### Same-domain OAuth

Same-domain OAuth is fully functional:

1. The user visits `https://tenant.sales-agent.example.com/admin/`.
2. The OAuth flow completes with session cookies.
3. The system redirects the user back to the tenant subdomain after authentication.

#### Cross-domain OAuth

Cross-domain OAuth is limited:

1. The user visits an external domain, for example `https://test-agent.adcontextprotocol.org/admin/`.
2. OAuth initiation works and stores the external domain in the session.
3. The OAuth callback cannot retrieve the session data because of cookie domain restrictions.
4. The system redirects the user to the login page instead of back to the external domain.

### Technical details

#### Session cookie configuration

```python
# Production session config (src/admin/app.py)
SESSION_COOKIE_DOMAIN = ".sales-agent.example.com"  # Multi-tenant mode: scoped to the internal domain
SESSION_COOKIE_SECURE = True                        # Required for SameSite=None over HTTPS
SESSION_COOKIE_SAMESITE = "None"                    # Required for EventSource cross-origin requests
SESSION_COOKIE_PATH = "/"                           # Covers /admin/* and /auth/* (OAuth callbacks)
```

#### OAuth flow architecture

```python
# OAuth Initiation (stores external domain in session)
session["oauth_external_domain"] = request.headers.get("Apx-Incoming-Host")

# OAuth Callback (retrieves from session - fails cross-domain)
external_domain = session.pop("oauth_external_domain", None)
```

### Browser security limitation

The limitation comes from fundamental browser security: browsers cannot share cookies across different domains. When a user comes from `test-agent.adcontextprotocol.org`, the browser cannot access session cookies scoped to `.sales-agent.example.com`.

### Workaround

Direct users to `https://tenant.sales-agent.example.com/admin/` for OAuth authentication rather than to external domain URLs.

### Test coverage

The tests cover the following behaviors:

- OAuth session handling within the same domain
- Approximated header detection and processing
- Session cookie configuration
- Redirect URI integrity (no modifications)
- CSRF protection preservation (Authlib state management)
- Documentation of the cross-domain limitation

**Key test file**: `tests/integration/test_oauth_session_handling.py`

## Generic OIDC provider support

The Admin UI supports authentication through any OpenID Connect (OIDC) compliant provider, not just Google. This lets organizations use their own identity provider.

### Supported providers

Any OIDC-compliant provider works, including the following:

- Google (default)
- Microsoft Azure AD / Entra ID
- Okta
- Auth0
- Keycloak
- OneLogin
- Ping Identity
- Custom OIDC providers

### Configuration options

#### Option A: Generic OIDC

Use this option with any OIDC-compliant provider:

```bash
# Required for generic OIDC
OAUTH_DISCOVERY_URL=https://your-provider.com/.well-known/openid-configuration
OAUTH_CLIENT_ID=your-client-id
OAUTH_CLIENT_SECRET=your-client-secret

# Optional: customize scopes (defaults to "openid email profile")
OAUTH_SCOPES=openid email profile custom_scope
```

#### Option B: Google OAuth

Use this option if you only use Google:

```bash
# Google-specific (backwards compatible)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

### Common discovery URLs

The following list shows discovery URLs for common providers:

- **Google**: `https://accounts.google.com/.well-known/openid-configuration`
- **Microsoft**: `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration`
- **Okta**: `https://{your-domain}.okta.com/.well-known/openid-configuration`
- **Auth0**: `https://{your-tenant}.auth0.com/.well-known/openid-configuration`
- **Keycloak**: `https://{server}/realms/{realm}/.well-known/openid-configuration`

### Set up the OAuth application

When you create the OAuth application in your identity provider, use the following settings:

- **Application type**: Web application
- **Redirect URI**: `http://localhost:8000/auth/google/callback` (local) or `https://your-domain/admin/auth/google/callback` (production)
- **Scopes**: At minimum `openid`, `email`, and `profile`
- **Grant type**: Authorization Code

### Claim mapping

The system automatically handles different claim formats from various providers:

- **Email**: `email`, `preferred_username`, `upn`, `sub`
- **Name**: `name`, `display_name`, or `given_name` and `family_name` combined
- **Picture**: `picture`, `avatar_url`, `photo`

### Priority order

When multiple OAuth configurations exist, the system uses them in the following order:

1. Generic OIDC (`OAUTH_DISCOVERY_URL` and credentials) — highest priority.
2. Named provider (`OAUTH_PROVIDER` and generic credentials).
3. Google OAuth (`GOOGLE_CLIENT_ID` and secret) — backwards compatible.
4. File-based (`client_secret.json`) — legacy support.

## Secrets configuration

### The `.env.secrets` file

All secrets must be in the `.env.secrets` file, not in environment variables.

Create your `.env.secrets` file:

```bash
# API Keys
GEMINI_API_KEY=your-gemini-api-key-here

# OAuth Configuration (choose ONE option)

# Option A: Generic OIDC (works with ANY provider)
OAUTH_DISCOVERY_URL=https://your-provider.com/.well-known/openid-configuration
OAUTH_CLIENT_ID=your-client-id
OAUTH_CLIENT_SECRET=your-client-secret

# Option B: Google OAuth (simpler if only using Google)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Super Admin Configuration
SUPER_ADMIN_EMAILS=user1@example.com,user2@example.com

# GAM OAuth Configuration (required for Google Ad Manager functionality)
# Note: This is separate from Admin UI OAuth - it's for GAM API access
GAM_OAUTH_CLIENT_ID=your-gam-client-id.apps.googleusercontent.com
GAM_OAUTH_CLIENT_SECRET=your-gam-client-secret

# Optional
SUPER_ADMIN_DOMAINS=example.com
```

### Benefits of `.env.secrets`

The `.env.secrets` file provides the following benefits:

- **Single source**: All secrets live in one place.
- **Gitignore protection**: The file is not committed to the repository.
- **Workspace isolation**: Each workspace can have different secrets.
- **Reduced risk**: No accidental secret exposure through environment variables.

### Security best practices

Follow these practices:

- Never commit secrets to version control.
- Use different secrets for dev, staging, and production.
- Rotate secrets regularly, at least quarterly.
- Audit secret access through logs.
- Use a secrets manager in production, such as Fly.io secrets or AWS Secrets Manager.
