"""Integration tests for product discovery auth & identity obligations.

Tests authentication, identity resolution, and principal-scoped visibility
through the real _get_products_impl pipeline with real DB data.

Covers obligations:
- BR-RULE-041-01: Discovery endpoint authentication
- BR-RULE-003-01: Principal-scoped product visibility
- CONSTR-DISCOVERY-AUTH-01: Discovery auth pattern constraint
"""

import pytest

from src.core.resolved_identity import PublicIdentity, ResolvedIdentity
from src.core.tenant_context import TenantContext
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness._identity import make_identity
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _lazy_identity(
    tenant_id: str,
    principal_id: str | None = None,
    protocol: str = "mcp",
) -> ResolvedIdentity | PublicIdentity:
    """An identity carrying the tenant row the database holds for *tenant_id*.

    Through the canonical harness helper, so ``principal_id=None`` builds the
    ``PublicIdentity`` a public tool takes rather than a ``ResolvedIdentity`` whose
    principal is None -- a shape the type no longer has.
    """
    return make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=TenantContext.load(tenant_id),
    )


class TestDiscoveryEndpointAuthentication:
    """BR-RULE-041-01: Discovery endpoint authentication.

    Auth is optional for discovery: an absent credential is served anonymously. A presented
    credential that does not resolve is refused with AUTH_INVALID by the resolver before the
    implementation runs (BR-SECURITY-002); the "invalid token" test below builds the identity
    the implementation would see only if it did run, which is the anonymous one. Data is not
    scoped by identity for unrestricted products.
    """

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_unrestricted_products(self, integration_db):
        """No auth token returns all unrestricted products.

        Covers: BR-RULE-041-01
        """
        with ProductEnv(tenant_id="auth-notoken", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="auth-notoken",
                subdomain="auth-notoken",
                brand_manifest_policy="public",
            )
            p1 = ProductFactory(tenant=tenant, product_id="public-1", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="public-2", allowed_principal_ids=None)
            PricingOptionFactory(product=p2)

            env._identity = _lazy_identity("auth-notoken", principal_id=None)
            result = await env.call_impl(brief="test")

        assert len(result.products) == 2
        product_ids = {p.product_id for p in result.products}
        assert product_ids == {"public-1", "public-2"}

    @pytest.mark.asyncio
    async def test_valid_auth_token_returns_all_visible_products(self, integration_db):
        """Valid auth token returns both restricted and unrestricted products visible to that principal.

        Covers: BR-RULE-041-01
        """
        with ProductEnv(tenant_id="auth-valid", principal_id="buyer-1") as env:
            tenant = TenantFactory(tenant_id="auth-valid", subdomain="auth-valid")
            PrincipalFactory(tenant=tenant, principal_id="buyer-1")

            p1 = ProductFactory(tenant=tenant, product_id="open-product", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="exclusive-product", allowed_principal_ids=["buyer-1"])
            PricingOptionFactory(product=p2)

            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert "open-product" in product_ids
        assert "exclusive-product" in product_ids
        assert len(result.products) == 2

    @pytest.mark.asyncio
    async def test_invalid_auth_treated_as_anonymous_mcp(self, integration_db):
        """Invalid auth token in MCP is treated as anonymous -- restricted products excluded.

        Covers: BR-RULE-041-01
        """
        with ProductEnv(tenant_id="auth-invalid", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="auth-invalid",
                subdomain="auth-invalid",
                brand_manifest_policy="public",
            )
            p1 = ProductFactory(tenant=tenant, product_id="pub-product", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="locked-product", allowed_principal_ids=["real-buyer"])
            PricingOptionFactory(product=p2)

            # Simulate invalid token scenario: principal_id=None (token resolved to nothing)
            env._identity = _lazy_identity("auth-invalid", principal_id=None, protocol="mcp")
            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert "pub-product" in product_ids
        assert "locked-product" not in product_ids
        assert len(result.products) == 1

    # test_require_auth_policy_rejects_anonymous is REMOVED. It built an anonymous identity
    # against a tenant whose brand_manifest_policy is "require_auth" and asserted
    # _get_products_impl raised AdCPAuthenticationError itself.
    #
    # Neither half is constructible now. The anonymous caller of a public tool is a
    # PublicIdentity, not a ResolvedIdentity with a None principal (the field is required),
    # and the seller policy is no longer read in the implementation at all --
    # src/core/tools/products.py:214 says so, and the refusal is minted by the resolver,
    # which asks ToolSpec.requires_credential(tenant) once the tenant row is loaded
    # (registry.py:195). ruff-boundary.toml bans raising AUTH_MISSING / AUTH_INVALID
    # anywhere but the resolver.
    #
    # The obligation (BR-RULE-041-01 / BR-UC-001 INV-1: a require_auth seller makes
    # get_products need a caller) is graded where it is decided -- the resolver, for every
    # transport at once -- by the transport-blind auth scenarios asserting the AUTH_MISSING
    # wire envelope across a2a, mcp and rest.
    #
    # Same removal, same reason, as tests/unit/test_media_buy.py:3869.


class TestPrincipalScopedProductVisibility:
    """BR-RULE-003-01: Principal-scoped product visibility.

    Products with allowed_principal_ids are visible only to listed principals.
    Unrestricted products (null/empty allowed_principal_ids) are visible to all.
    Anonymous users cannot see restricted products.
    """

    @pytest.mark.asyncio
    async def test_principal_sees_own_restricted_products(self, integration_db):
        """Principal in allowed_principal_ids sees the restricted product.

        Covers: BR-RULE-003-01
        """
        with ProductEnv(tenant_id="scope-own", principal_id="p-alpha") as env:
            tenant = TenantFactory(tenant_id="scope-own", subdomain="scope-own")
            PrincipalFactory(tenant=tenant, principal_id="p-alpha")

            p = ProductFactory(
                tenant=tenant,
                product_id="alpha-only",
                allowed_principal_ids=["p-alpha"],
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 1
        assert result.products[0].product_id == "alpha-only"

    @pytest.mark.asyncio
    async def test_principal_excluded_from_other_restricted_products(self, integration_db):
        """Principal not in allowed_principal_ids cannot see the restricted product.

        Covers: BR-RULE-003-01
        """
        with ProductEnv(tenant_id="scope-excl", principal_id="p-beta") as env:
            tenant = TenantFactory(tenant_id="scope-excl", subdomain="scope-excl")
            PrincipalFactory(tenant=tenant, principal_id="p-beta")

            p = ProductFactory(
                tenant=tenant,
                product_id="alpha-exclusive",
                allowed_principal_ids=["p-alpha"],
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_anonymous_cannot_see_restricted_products(self, integration_db):
        """Anonymous users cannot see products with allowed_principal_ids set.

        Covers: BR-RULE-003-01
        """
        with ProductEnv(tenant_id="scope-anon", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="scope-anon",
                subdomain="scope-anon",
                brand_manifest_policy="public",
            )
            p_open = ProductFactory(tenant=tenant, product_id="open-item", allowed_principal_ids=None)
            PricingOptionFactory(product=p_open)
            p_closed = ProductFactory(
                tenant=tenant,
                product_id="closed-item",
                allowed_principal_ids=["some-principal"],
            )
            PricingOptionFactory(product=p_closed)

            env._identity = _lazy_identity("scope-anon", principal_id=None)
            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert "open-item" in product_ids
        assert "closed-item" not in product_ids

    @pytest.mark.asyncio
    async def test_mixed_visibility_with_authenticated_principal(self, integration_db):
        """Authenticated principal sees unrestricted + own restricted, not others' restricted.

        Covers: BR-RULE-003-01
        """
        with ProductEnv(tenant_id="scope-mix", principal_id="buyer-x") as env:
            tenant = TenantFactory(tenant_id="scope-mix", subdomain="scope-mix")
            PrincipalFactory(tenant=tenant, principal_id="buyer-x")

            # Unrestricted
            p1 = ProductFactory(tenant=tenant, product_id="everyone", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            # Restricted to buyer-x
            p2 = ProductFactory(tenant=tenant, product_id="for-x", allowed_principal_ids=["buyer-x"])
            PricingOptionFactory(product=p2)
            # Restricted to buyer-y
            p3 = ProductFactory(tenant=tenant, product_id="for-y", allowed_principal_ids=["buyer-y"])
            PricingOptionFactory(product=p3)
            # Restricted to both
            p4 = ProductFactory(tenant=tenant, product_id="for-xy", allowed_principal_ids=["buyer-x", "buyer-y"])
            PricingOptionFactory(product=p4)

            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert product_ids == {"everyone", "for-x", "for-xy"}


class TestDiscoveryAuthPatternConstraint:
    """CONSTR-DISCOVERY-AUTH-01: Discovery auth pattern constraint.

    Discovery endpoints support optional auth. When auth is present, the response
    is scoped to the principal. When auth is absent (public policy), unrestricted
    products are returned.
    """

    @pytest.mark.asyncio
    async def test_discovery_with_auth_scopes_by_principal(self, integration_db):
        """Discovery with valid auth scopes products by principal visibility.

        Covers: CONSTR-DISCOVERY-AUTH-01
        """
        with ProductEnv(tenant_id="disc-auth", principal_id="disc-buyer") as env:
            tenant = TenantFactory(tenant_id="disc-auth", subdomain="disc-auth")
            PrincipalFactory(tenant=tenant, principal_id="disc-buyer")

            p1 = ProductFactory(tenant=tenant, product_id="gen-avail", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="disc-exclusive", allowed_principal_ids=["disc-buyer"])
            PricingOptionFactory(product=p2)
            p3 = ProductFactory(tenant=tenant, product_id="other-exclusive", allowed_principal_ids=["other-buyer"])
            PricingOptionFactory(product=p3)

            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert "gen-avail" in product_ids
        assert "disc-exclusive" in product_ids
        assert "other-exclusive" not in product_ids

    @pytest.mark.asyncio
    async def test_discovery_without_auth_returns_only_unrestricted(self, integration_db):
        """Discovery without auth returns only unrestricted products (public policy).

        Covers: CONSTR-DISCOVERY-AUTH-01
        """
        with ProductEnv(tenant_id="disc-noauth", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="disc-noauth",
                subdomain="disc-noauth",
                brand_manifest_policy="public",
            )
            p1 = ProductFactory(tenant=tenant, product_id="public-prod", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="private-prod", allowed_principal_ids=["vip"])
            PricingOptionFactory(product=p2)

            env._identity = _lazy_identity("disc-noauth", principal_id=None)
            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert product_ids == {"public-prod"}

    @pytest.mark.asyncio
    async def test_a2a_discovery_with_valid_auth(self, integration_db):
        """A2A discovery with valid auth returns principal-scoped products.

        Covers: CONSTR-DISCOVERY-AUTH-01
        """
        with ProductEnv(tenant_id="disc-a2a", principal_id="a2a-buyer") as env:
            tenant = TenantFactory(tenant_id="disc-a2a", subdomain="disc-a2a")
            PrincipalFactory(tenant=tenant, principal_id="a2a-buyer")

            p1 = ProductFactory(tenant=tenant, product_id="a2a-public", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            p2 = ProductFactory(tenant=tenant, product_id="a2a-restricted", allowed_principal_ids=["a2a-buyer"])
            PricingOptionFactory(product=p2)

            env._identity = _lazy_identity("disc-a2a", principal_id="a2a-buyer", protocol="a2a")
            result = await env.call_impl(brief="test")

        product_ids = {p.product_id for p in result.products}
        assert "a2a-public" in product_ids
        assert "a2a-restricted" in product_ids
