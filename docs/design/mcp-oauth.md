# MCP OAuth protocol

## Issuer and tenant resources

The authorization issuer is shared across tenants. Set `MCP_OAUTH_ISSUER` to its
public origin or let `SALES_AGENT_DOMAIN` supply that origin. Issuer path prefixes,
query strings, and fragments are unsupported and fail settings validation.

Each active tenant has its own MCP resource at `https://<virtual_host>/mcp`.
Protected-resource metadata uses the registered tenant host, not the shared
issuer's host. Unknown hosts receive `invalid_target`. The challenge renderer
links to metadata on the request host; metadata validates that host against the
tenant registry. Local hosts use the application's shared HTTP scheme policy.

OAuth clients belong to a tenant. Both grants bind access tokens to that tenant's
resource, even when the client sends its request to the shared issuer. Token
authentication checks the tenant, resource, required `mcp:principal` scope,
expiry, revocation, and active client. Permanent buyer tokens retain their
separate lookup path. `MCP_OAUTH_AUDIENCE` supplies only the deployment-wide
fallback helper; tenant requests derive their resource from the tenant registry.

## Protocol requirements

These requirements supplement the repository's AdCP 3.1.1 pin (`adcp==6.6.0`).
OAuth HTTP grant endpoints are outside the AdCP conformance storyboard and are
ungraded there. The integration suite grades them directly against HTTP and
PostgreSQL without replacing the application's authentication or storage code.

- [RFC 6749 sections 3.1, 3.2, and 5.2](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.1): Reject repeated parameters and multiple client authentication methods.
- [RFC 6749 section 3.3](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.3): Validate space-delimited scope tokens and report the granted scope.
- [RFC 6749 section 4.1.2.1](https://www.rfc-editor.org/rfc/rfc6749.html#section-4.1.2.1): Redirect authorization errors to a validated callback and echo `state`. Unknown clients and unregistered callbacks receive direct errors.
- [RFC 7636 sections 4.1 through 4.6](https://www.rfc-editor.org/rfc/rfc7636.html#section-4.1): Require S256, validate verifier syntax, bind the challenge to the code, and reject mismatches.
- [RFC 8707 sections 2 and 3](https://www.rfc-editor.org/rfc/rfc8707.html#section-2): Bind grants and tokens to the intended tenant resource. This service accepts one resource per request and rejects repeated resource parameters.
- [RFC 9728 sections 3 and 5](https://www.rfc-editor.org/rfc/rfc9728.html#section-3): Publish tenant resource metadata and link to it from authentication challenges.
- [RFC 8414 section 3](https://www.rfc-editor.org/rfc/rfc8414.html#section-3): Publish authorization-server metadata for the origin-only issuer.

## Regression coverage

`tests/integration/test_oauth_protocol.py` covers grant validation, PKCE, error
callbacks, metadata, scope and resource enforcement, and two-tenant flows through
the shared issuer. `tests/integration/test_mcp_oauth_discovery_challenge.py`
grades challenges through the full MCP app. The service unit tests grade pure
PKCE helpers and unsupported settings. The token repository integration tests
grade SQL expiry, revocation, and tenant scoping.