# Security and authentication

This document is the reference for the security architecture of the sales agent: admin authentication, tenant and principal isolation, OAuth and OIDC configuration, and secrets handling. It describes what the system does and what you must configure or test when you change authentication code. Outbound HTTP and SSRF protection appear here only in summary; [Outbound egress](security/outbound-egress.md) is the full reference.

## Contents

- [Admin authentication architecture](#admin-authentication-architecture) — the super admin check, session caching, the tenant access guard, and cross-site request forgery protection
- [Tenant registration security](#tenant-registration-security) — subdomain assignment, branded URLs, ad server checks
- [Access control patterns](#access-control-patterns) — super admins, tenant users, principal isolation, audit trail
- [Message signing (RFC 9421)](#message-signing-rfc-9421) — agent-to-agent authentication, and where it is documented in full
- [Outbound egress (SSRF)](#outbound-egress-ssrf) — the single gateway for outbound HTTP
- [Security testing requirements](#security-testing-requirements)
- [OAuth cross-domain authentication](#oauth-cross-domain-authentication) — why cross-domain login fails, and the workaround
- [Generic OIDC provider support](#generic-oidc-provider-support) — configuration for any OIDC provider, or Google OAuth
- [Secrets configuration](#secrets-configuration) — the secrets file and handling practices

## Admin authentication architecture

Three functions in `src/admin/utils/helpers.py` decide admin access. `is_super_admin()` decides whether a user is a super admin. `require_tenant_access()` decides whether a signed-in user reaches a tenant. `require_auth()` checks that a user is signed in and, with `admin_only=True`, that the user is a super admin. On a route that takes a `tenant_id`, it also refuses a tenant SSO session for any tenant but its own.

### Super admin check

`is_super_admin()` decides in the following order:

1. A session from a tenant's own SSO, whose `auth_method` session key is `oidc`, is never a super admin, whatever email it carries. [Super admins](#super-admins) explains why.
2. A positive result in the session cache for the same email makes the user a super admin.
3. Otherwise the user is a super admin when the email is in the `SUPER_ADMIN_EMAILS` list or the `super_admin_emails` row, or when its domain is in the `SUPER_ADMIN_DOMAINS` list or the `super_admin_domains` row.

`super_admin_grants()` reads the two settings and the two rows of the `superadmin_config` table, which the `TenantManagementConfig` model maps, into one set of emails and one set of domains. No source takes precedence over another. Every call that reaches the third step reads the database, and a failed read fails the request rather than counting as no match.

### Session caching

`is_super_admin()` writes its result to the session keys `is_super_admin` and `admin_email`. Only a cached positive result ends the check early. After a negative result, the next request reads the settings and the database again. Each sign-in first clears the identity keys that an earlier sign-in left in the session, these two included.

### Tenant access guard

`require_tenant_access()` protects the tenant routes. `tenant_access()` makes the decision, in the following order:

1. Outside production, a session that carries the `test_user` key passes for the tenant it names, or for every tenant when it names the super admin role. Only the test helpers sign such a session; no route creates one.
2. The guard redirects a request with no signed-in user to the tenant login page. When the caller reads JSON, it answers `401` instead.
3. A session from a tenant's own SSO reaches only the tenant it was issued for. For any other tenant, the guard answers `403`.
4. A super admin passes for every tenant.
5. Any other user needs an active `User` row for the tenant. Without one, the guard answers `403`.
6. The role on that row must permit the request, as [Tenant users](#tenant-users) describes. Otherwise the guard answers `403`.

The caller reads JSON unless the request asks for a page: a browser navigation, which sends `Sec-Fetch-Mode: navigate`, or a request whose `Accept` header ranks `text/html` above `application/json`. To a caller that reads JSON, the `401` and each `403` carry the body `{"success": false, "error": ...}`. A browser gets the login redirect or an HTML page.

### Cross-site request forgery

The admin UI authenticates browsers with the session cookie, so a page on another site could otherwise submit a form to an admin route with a signed-in user's cookie attached. Two controls stop this:

- **Origin check** (`src/admin/csrf.py`): every state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) must come from the admin UI's own origin. The check reads `Sec-Fetch-Site` (only `same-origin` or `none` pass), or, if a browser doesn't send that, `Origin`, which must equal the address the request was sent to. A request with neither header isn't from a browser and passes. Sibling subdomains are refused. Forms and `fetch` calls need no token. Views authenticated only by an API-key header (the tenant management and sync APIs) are exempt with `csrf_exempt`.
- **`SameSite=Lax` session cookie**: browsers don't send it on cross-site posts, frames or `fetch` calls, but do send it on top-level navigations, which is what the OAuth and OIDC callbacks are.

Behind a reverse proxy, the `Origin` fallback compares against the request's own scheme and host, which `ProxyFix` takes from `X-Forwarded-Proto` and `X-Forwarded-Host` (configured in production). **The proxy must send both.** Without them, a browser that sends `Origin` but not `Sec-Fetch-Site` (`https://admin.example.com`) is compared with the internal address (`http://...`) and refused: the check fails closed, it is not bypassed. Current browsers send `Sec-Fetch-Site`, which does not depend on the proxy.

## Tenant registration security

### Subdomain assignment

To prevent subdomain squatting and brand impersonation, self-service signup generates each tenant subdomain from a random universally unique identifier (UUID):

- Subdomains are 8-character hexadecimal strings, for example `a7f3d92b`.
- The system takes the first 8 characters of a UUID4 value.
- Users cannot choose custom subdomains during signup.
- This approach eliminates the risk of a user claiming a subdomain like `nytimes` or `cnn`.

The super admin's tenant form uses its subdomain field or derives a subdomain from the tenant name when the field is empty. The Tenant Management API requires the subdomain in the request.

### Branded URLs

Publishers who want branded URLs can use virtual hosts (custom domains). Configure a custom domain, for example `sales.publisher.com`, in the tenant settings. From the tenant settings, you can register the virtual host with the Approximated proxy service for Transport Layer Security (TLS) and routing. The registration accepts only the tenant's own virtual host.

### Ad server configuration check

The landing page of a tenant without a configured ad server shows a "Pending Configuration" page instead of the agent endpoint URLs. This prevents incomplete registrations from appearing operational.

## Access control patterns

### Super admins

Super admins pass `require_tenant_access` for every tenant, and they pass `require_auth(admin_only=True)`. In the Admin UI, they create, deactivate, and reactivate tenants, and they add users, change user roles, and deactivate users. No Admin UI route deletes a tenant or a user; the Tenant Management API deletes tenants. You configure super admins through environment variables or the database.

Super-admin powers come only from the deployment's own sign-in (Google, or the global `OAUTH_*` provider). A tenant's own SSO is configured by that tenant's admin, so the email its provider asserts is only as trustworthy as that admin. A session signed in through a tenant's SSO is therefore bound to that tenant: it is never a super admin, whatever email it carries, and it reaches no other tenant. A super admin can still be a user of a tenant and sign in through that tenant's SSO, but that session has only the role the tenant gave them, in that tenant. A deployment with no global sign-in configured has no way to sign in as a super admin.

### Tenant users

Tenant users have access to specific tenants through the `User` model. On admin routes decorated with `require_tenant_access`, that decorator refuses a user with no active `User` row in the tenant, and checks the role on the row on every request:

| Role | Can |
| --- | --- |
| `viewer` | Read only: no `POST`, `PUT`, `PATCH` or `DELETE` |
| `manager` | Change everything except the admin-only routes |
| `admin` | Everything, including the admin-only routes: managing users and roles, authorized domains and emails, setup mode, SSO configuration and its connection test, and deactivating the tenant |

A route is admin-only when it is decorated `require_tenant_access(role=UserRole.ADMIN)` (the least role the route needs). The role follows what the request can change: the SSO connection test is admin-only because a successful test turns SSO on, and it is a `POST` so a link from another site cannot start one. A refusal is a 403, in the form that [Tenant access guard](#tenant-access-guard) describes. The Users page adds people as viewers by default; admins choose a role when adding someone and can change it later. A person who signs in from an authorized domain without an invitation becomes an admin. So does a person on the authorized email list who signs in through the deployment's OAuth client. That is why changing those lists is admin-only. The Users page refuses to demote or deactivate a tenant's last active admin. Super admins signed in through the deployment's own sign-in pass every check; a session from a tenant's SSO never counts as a super admin.

Some tenant routes check the tenant inline or not at all instead of using `require_tenant_access`, so they do not get these checks. #2233 and #2496 track them, and the `EXEMPT` set in `tests/unit/test_architecture_admin_tenant_scoping.py` names each one.

### Principal isolation

Each advertiser (principal) has isolated access tokens. Tokens are scoped to a specific tenant and cannot access other tenants or principals.

### Audit trail

The `log_admin_action` decorator in `src/admin/utils/audit_decorator.py` writes one row to the `audit_logs` table for each call of a route that carries it. The row holds the timestamp, the user, the operation, the HTTP method, the fields of a `POST` request, and whether the call succeeded. The decorator leaves out fields with sensitive names, such as passwords, tokens, and keys. It writes a row only when the request names a tenant.

For admin actions, audit coverage is per route: a route without the decorator writes no row through it. Not every state-changing admin route carries the decorator, so do not treat the `audit_logs` table as a complete record of admin actions. When you add a state-changing admin route, add the decorator to it.

## Message signing (RFC 9421)

Everything above concerns **admin** authentication — who may log into the dashboard and
what they may do. Protocol-level authentication between agents is a separate mechanism:
RFC 9421 HTTP message signatures, covering both inbound requests we verify and outbound
webhooks we sign.

It is documented in full elsewhere; this section exists so a reader arriving here for
"how do agents authenticate to each other" is not left at a dead end.

- **[Signing posture and key discovery](signing/posture-and-discovery.md)** — what we
  advertise, the `supported_for` / `required_for` enforcement ladder, and the brand.json walk
  a counterparty uses to find our public key. Includes the documented trap: a bare
  `.well-known/jwks.json` lookup is **not** how you discover OUR key — and the converse, that
  it is exactly where we read YOURS.
- **[Verifying our outbound webhooks](signing/verifying-our-webhooks.md)** — the profile
  tag, which document answers which question, and the deprecated HMAC-SHA256 path with its
  AdCP 4.0 removal.
- **[Signing key runbook](operations/signing-key-runbook.md)** — operator-facing:
  provisioning, rotation, revocation, rollout and rollback.

Four properties are security-relevant enough to state here rather than only in the
runbook:

- **No private key material is written to a filesystem.** A provisioned key's private PEM
  is stored encrypted on its own database row under a deployment-wide key encryption key
  (KEK), named by `settings.signing.key_passphrase_env`
  (`ADCP_SIGNING_KEY_PASSPHRASE_ENV`).
- **There is no plaintext fallback.** Minting refuses outright when no KEK is configured,
  rather than degrading to storing the key unencrypted.
- **Signing failures fail closed, not open.** A tenant with no usable signing key cannot
  activate a notification subscriber, because the proof-of-control challenge must be
  signed and we will not send an unsigned one.
- **Rollback is a configuration change, never a deploy.** A per-tenant posture edit rolls
  back one counterparty; `settings.signing.verifier_enabled` is the deployment-wide kill
  switch. See the runbook for which to reach for — they differ in blast radius.

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

- Each super admin source: the two settings and the two `superadmin_config` rows
- The binding of a tenant SSO session to its tenant
- A new state-changing admin route carries `log_admin_action`
- The session cookie attributes that `create_app` sets
- The cross-site request check in `src/admin/csrf.py`
- The OAuth `state` check

The Admin UI sets no session lifetime, so the session cookie has no expiry date. Flask still rejects a session cookie signed more than 31 days earlier, which is its default `PERMANENT_SESSION_LIFETIME`. Unless you set `FLASK_SECRET_KEY`, each process signs sessions with its own random key, so a restart ends every session. Besides the origin check that [Cross-site request forgery](#cross-site-request-forgery) describes, the OAuth `state` parameter protects the sign-in flows and the Google Ad Manager authorization flow.

**Test locations**: `TestEnvironmentFirstAuthentication` in `tests/integration/test_product_deletion.py` checks that each of the `SUPER_ADMIN_EMAILS` list, the `SUPER_ADMIN_DOMAINS` list, and the `super_admin_emails` row grants super admin on its own, and that a database match writes the session cache. No test checks that the `super_admin_domains` row grants super admin, or what a failed database read does. `tests/unit/test_admin_tenant_sso_scope.py` and `tests/integration/test_tenant_sso_privilege.py` check that a tenant SSO session is never a super admin and reaches no other tenant, and that each sign-in clears the identity an earlier one left. `test_tenant_sso_privilege.py` also covers each check of the SSO connection test. `tests/integration/test_audit_decorator.py` checks the rows that `log_admin_action` writes. `tests/unit/test_admin_csrf_protection.py` checks the cross-site request check, and that the production session cookie is `SameSite=Lax`, `HttpOnly`, and `Secure`. No test checks `SESSION_COOKIE_PATH` or `SESSION_COOKIE_DOMAIN`.

## OAuth cross-domain authentication

In this section, `sales-agent.example.com` stands for the deployment's own domain, `SALES_AGENT_DOMAIN`. In production multi-tenant mode, the session cookie covers that domain and its subdomains, so OAuth authentication works across them. OAuth authentication from external domains, such as `test-agent.adcontextprotocol.org`, is limited by browser cookie security restrictions.

### How OAuth works

#### Same-domain OAuth

Same-domain OAuth is fully functional:

1. The user visits `https://tenant.sales-agent.example.com/admin/`.
2. The OAuth flow completes with session cookies.
3. After authentication, the callback redirects the user within the host that received the callback. For a tenant sign-in in production, that host comes from `GOOGLE_OAUTH_REDIRECT_URI`, or from `SALES_AGENT_DOMAIN` when that variable is unset.

#### Cross-domain OAuth

Cross-domain OAuth is limited:

1. The user visits an external domain, for example `https://test-agent.adcontextprotocol.org/admin/`.
2. OAuth initiation works and stores the originating host and the tenant in the session.
3. The OAuth callback cannot retrieve the session data because of cookie domain restrictions.
4. The callback redirects the user to the login page.

### Technical details

#### Session cookie configuration

```python
# Production session config (src/admin/app.py)
SESSION_COOKIE_SECURE = True                        # HTTPS only
SESSION_COOKIE_HTTPONLY = True                      # No script reads the session cookie
SESSION_COOKIE_SAMESITE = "Lax"                     # Sent on top-level navigations (OAuth callbacks), not on cross-site posts
SESSION_COOKIE_PATH = "/"                           # Covers /admin/* and /auth/* (OAuth callbacks)
SESSION_COOKIE_DOMAIN = ".sales-agent.example.com"  # Multi-tenant mode only: "." + SALES_AGENT_DOMAIN, if set
```

Outside production, the app sets `SESSION_COOKIE_SECURE = False` and no cookie domain. The other values match production.

#### OAuth flow architecture

```python
# OAuth initiation for a tenant login (stores the originating host and the tenant in the session)
session["oauth_originating_host"] = request.headers.get("Host", "")
session["oauth_tenant_context"] = tenant_id

# OAuth callback (reads the tenant from the session - fails cross-domain)
tenant_context = session.get("oauth_tenant_context")
```

### Browser security limitation

The limitation comes from fundamental browser security: browsers cannot share cookies across different domains. When a user comes from `test-agent.adcontextprotocol.org`, the browser cannot access session cookies scoped to `.sales-agent.example.com`.

### Workaround

Direct users to sign in on the deployment's own domain or one of its subdomains rather than on an external domain. A host names a tenant only when it is the virtual host that the tenant declares.

### Test coverage

`tests/integration/test_oauth_session_handling.py` records the cross-domain limitation as data, and its tests do not call the sign-in code. `test_oauth_callback_redirects_to_onboarding_for_signup_flow` in `tests/integration/test_self_service_signup.py` calls `google_callback` for a signup. The session cookie test is listed under [Security testing requirements](#security-testing-requirements).

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
- **Redirect URI**: locally, register both `http://localhost:8000/admin/auth/google/callback` and `http://localhost:8000/auth/google/callback`. In production, register `https://your-domain/admin/auth/google/callback`, or the value of `GOOGLE_OAUTH_REDIRECT_URI`.
- **Scopes**: At minimum `openid`, `email`, and `profile`
- **Grant type**: Authorization Code

### Claim mapping

`extract_user_info()` in `src/admin/auth_utils.py` reads the claims only from the ID token that Authlib verified, and handles different claim formats from various providers:

- **Email**: `email`, or else `preferred_username` or `upn` when the value is an email address. `sub` is never used as the email.
- **Name**: `name`, `display_name`, or `given_name` and `family_name` combined
- **Picture**: `picture`, `avatar_url`, `photo`

A sign-in whose `email_verified` claim is `false` is refused. A sign-in without that claim is accepted, because some providers, such as Microsoft Entra ID, do not send it.

### Priority order

When multiple OAuth configurations exist, the system uses them in the following order:

1. Generic OIDC (`OAUTH_DISCOVERY_URL` and credentials) — highest priority.
2. Named provider (`OAUTH_PROVIDER` and generic credentials).
3. Google OAuth (`GOOGLE_CLIENT_ID` and secret) — backwards compatible.
4. File-based (`client_secret.json`) — legacy support.

## Secrets configuration

### The secrets file

The Docker Compose files read secrets from files that `.gitignore` excludes.
`docker-compose.yml` reads `.env`. `docker-compose.multi-tenant.yml` reads `.env` and then
`.env.secrets`, so a value in `.env.secrets` wins there.

Create your `.env` file:

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

### Benefits of a secrets file

A secrets file provides the following benefits:

- **Gitignore protection**: `.gitignore` excludes `.env` and `.env.secrets`, so neither file
  reaches the repository.
- **Workspace isolation**: Each workspace can have different secrets.

### Security best practices

Follow these practices:

- Never commit secrets to version control.
- Use different secrets for dev, staging, and production.
- Rotate secrets regularly, at least quarterly.
- Audit secret access through logs.
- Use a secrets manager in production, such as Fly.io secrets or AWS Secrets Manager.
