"""Given steps for authentication and tenant context.

These steps set up the authentication state in ``ctx`` that When/Then steps
rely on. They are generic across all use cases — any scenario that needs
an authenticated buyer, a missing tenant, or a sandbox account can reuse them.
"""

from __future__ import annotations

from pytest_bdd import given

# ── Authenticated / tenant-present paths ────────────────────────────


@given("a valid tenant context exists")
@given("the Buyer has tenant context")
def given_buyer_has_tenant_context(ctx: dict) -> None:
    """Buyer has valid tenant context (happy path)."""
    ctx["has_tenant"] = True
    ctx.setdefault("tenant_id", "test_tenant")


@given("the Buyer has tenant context via MCP session")
def given_buyer_has_tenant_context_mcp(ctx: dict) -> None:
    """Buyer has tenant context via MCP session."""
    ctx["has_tenant"] = True
    ctx["transport"] = "mcp"
    ctx.setdefault("tenant_id", "test_tenant")


# ── Missing-auth / missing-tenant paths ─────────────────────────────


@given("the Buyer has no authentication credentials")
def given_buyer_no_auth(ctx: dict) -> None:
    """Buyer has no authentication credentials at all."""
    ctx["has_auth"] = False
    ctx["identity"] = None


@given("the request has no valid authentication")
@given("But the request has no valid authentication")
def given_request_no_valid_auth(ctx: dict) -> None:
    """Request has credentials but they are invalid (no principal_id).

    Unlike 'no authentication credentials' (no identity at all), this
    simulates an expired/revoked token where the identity resolves but
    the principal_id is missing. Auth middleware should reject before
    any business logic (adapter calls, DB writes) executes.
    """
    from tests.factories.principal import PrincipalFactory

    env = ctx["env"]
    ctx["has_auth"] = False
    ctx["identity"] = PrincipalFactory.make_identity(
        principal_id=None,
        tenant_id=env._tenant_id,
    )
    # Reset adapter mock so Then can verify no side effects
    if "adapter" in env.mock:
        env.mock["adapter"].return_value.create_media_buy.reset_mock()


@given("no hostname-based tenant resolution is possible")
def given_no_hostname_tenant(ctx: dict) -> None:
    """No tenant can be resolved from hostname."""
    ctx["hostname_tenant"] = None


@given("no tenant can be resolved from the request context")
def given_no_tenant_resolved(ctx: dict) -> None:
    """No tenant can be resolved from any source (MCP path)."""
    ctx["has_tenant"] = False
    ctx["identity"] = None


# ── Sandbox / production account ─────────────────────────────────────


@given("the request targets a sandbox account")
def given_sandbox_account(ctx: dict) -> None:
    """Request is for a sandbox (dry_run) account."""
    ctx["sandbox"] = True
    ctx["has_tenant"] = True
    ctx.setdefault("tenant_id", "sandbox_tenant")


@given("the request targets a production account")
def given_production_account(ctx: dict) -> None:
    """Request is for a production (non-sandbox) account."""
    ctx["sandbox"] = False
    ctx["has_tenant"] = True
    ctx.setdefault("tenant_id", "prod_tenant")
