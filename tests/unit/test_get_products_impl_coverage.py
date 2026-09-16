"""Unit tests for _get_products_impl error paths and filter branches.

Covers uncovered lines in src/core/tools/products.py:
- Identity validation (L231-240)
- Product conversion error (L418-424)
- Property list resolution errors (L476-482)
- Filter type dispatch: is_fixed_price, format_types, format_ids, standard_formats_only (L539-614)
- Policy eligibility filtering (L697)
- AI ranking disabled (L780)
- Adapter pricing annotation error (L811-812)
- get_product_catalog conversion error (L994-996)

Tests verify intended behavioral contracts, not just line coverage:
- Error paths produce specific exception types with descriptive messages
- Filters correctly include/exclude products based on criteria
- Graceful degradation paths return products despite failures
- Edge cases around format_id shapes (str, dict, FormatId) all work correctly

"""

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from adcp.types import FormatId

from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity
from src.services.ai.config import CANONICAL_GOOGLE_PROVIDER, DEFAULT_GOOGLE_MODEL, TenantAIConfig
from tests.harness.product_unit import ProductEnv
from tests.helpers.adcp_factories import create_test_cpm_pricing_option, create_test_product

# The two configurations a tenant can carry, and what the single owner of
# "tenant -> TenantAIConfig" must resolve each of them to. They differ in provider AND
# in key so that the both-set case can tell which one won.
_TENANT_AI_CONFIG = {"provider": "anthropic", "model": "claude-x", "api_key": "tenant-key"}
_RESOLVED_FROM_AI_CONFIG = TenantAIConfig(provider="anthropic", model="claude-x", api_key="tenant-key")
_LEGACY_KEY = "legacy-key"
_RESOLVED_FROM_LEGACY_KEY = TenantAIConfig(
    provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key=_LEGACY_KEY
)

# The OPERATOR's own credential, present in the environment exactly as it is in a real
# deployment — and as ``tests/conftest.py`` sets it for every test in this suite. Given a
# recognisable value so "which vendor received which secret" is an assertion on a literal
# rather than an inference.
_PLATFORM_GEMINI_SECRET = "AIzaSy-PLATFORM-GEMINI-SECRET"


@contextlib.contextmanager
def _platform_ai_environment(monkeypatch, *, provider=None, model=None):
    """Pin the PLATFORM half of the AI environment and watch what gets built on it.

    Yields ``(factory, built)``:

    * ``factory`` is a fresh ``AIServiceFactory`` constructed against the environment
      this manager just pinned. It is built directly, never through ``get_factory()``,
      because that lookup is ``lru_cache``d with ``maxsize=1``: the first call anywhere in
      the process captures ``get_platform_defaults()`` into ``_platform_defaults``, and
      every later ``monkeypatch.setenv`` / ``delenv`` leaves that cached instance
      untouched. A test that only edits the environment and then lets production call
      ``get_factory()`` grades whichever environment happened to warm the cache — in file
      order that is ``tests/conftest.py``'s ``GEMINI_API_KEY``, so such a test would take
      the platform-key branch it believes it removed, and would reach a real provider the
      moment its path built a model.
    * ``built`` records every ``(provider, model_name, api_key)`` triple that
      ``AIServiceFactory._create_provider_model`` is asked to construct, with the real
      implementation still running underneath. That method IS the cross-vendor decision:
      it is the single place where a provider name and an API key are handed to a client
      constructor together, so recording its arguments states, as a literal, which vendor
      would receive which secret. Reading the key back off a built ``GoogleModel``
      (``model._provider.client._api_client.api_key``) tests the same fact through three
      layers of pydantic-ai private attributes.

    The platform key is left PRESENT on purpose. Every pre-existing ranking test either
    supplies a tenant key or deletes ``GEMINI_API_KEY``, so the configuration that
    actually ships — an operator-wide Google key plus whatever the seller stored — was
    graded by nothing.
    """
    monkeypatch.setenv("GEMINI_API_KEY", _PLATFORM_GEMINI_SECRET)
    # Every other provider credential removed, so "no key resolved" means exactly that
    # and cannot be satisfied by a variable that happens to be set on the dev box.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "PYDANTIC_AI_PROVIDER", "PYDANTIC_AI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    if provider is not None:
        monkeypatch.setenv("PYDANTIC_AI_PROVIDER", provider)
    if model is not None:
        monkeypatch.setenv("PYDANTIC_AI_MODEL", model)

    from src.services.ai.factory import AIServiceFactory

    factory = AIServiceFactory()
    built: list[tuple[str, str, str | None]] = []
    real_create = AIServiceFactory._create_provider_model

    def _record(self, provider_name, model_name, api_key):
        built.append((provider_name, model_name, api_key))
        return real_create(self, provider_name, model_name, api_key)

    with patch.object(AIServiceFactory, "_create_provider_model", _record):
        yield factory, built


def _assert_one_products_warning(caplog, needle: str):
    """Exactly one WARNING on ``src.core.tools.products`` whose message contains *needle*.

    The logger is named explicitly rather than leaning on caplog's root capture, because
    these are the SELLER-facing notices — the other half of "tell the seller in the log,
    keep it off the buyer's wire" — and nothing else in the repo binds that logger.
    Demoting one of them to ``logger.debug`` silences an operator's only signal that AI
    stopped working, and before these assertions existed it broke no test in the suite.

    Returns the record, so a caller can assert more about it.
    """
    matches = [r for r in caplog.records if r.name == "src.core.tools.products" and needle in r.getMessage()]
    assert len(matches) == 1, (
        f"expected exactly one src.core.tools.products WARNING containing {needle!r}, got {len(matches)}; "
        f"captured: {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert matches[0].levelno == logging.WARNING, matches[0].levelname
    return matches[0]


def _make_identity(principal_id=None, tenant=None, tenant_id=None):
    """Create a ResolvedIdentity for testing."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
    )


def _make_tenant(tenant_id="test-tenant"):
    """Create a minimal tenant dict."""
    return {
        "tenant_id": tenant_id,
        "name": "Test Tenant",
        "subdomain": "test",
        "ad_server": {"adapter": "mock"},
        "advertising_policy": None,
    }


def _make_request(brief="test brief", filters=None):
    """Create a GetProductsRequest using the factory."""
    from src.core.schema_helpers import create_get_products_request

    return create_get_products_request(brief=brief, filters=filters)


def _mock_uow_with_products(products):
    """Create a mock UoW context manager that returns the given products."""
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.products.list_all.return_value = products
    return mock_uow


def _standard_patches(mock_uow, principal=None, convert_fn=None):
    """Return list of patch context managers common to most tests.

    Patches lazy imports at their source modules since products.py uses
    inline imports.

    NOTE: get_db_session must be patched on the products module because the
    unit conftest autouse fixture patches it at the source module, which affects
    products.py's top-level import binding. Without this patch, the dynamic
    pricing block's get_db_session() succeeds (returns a MagicMock session),
    and enrich_products_with_pricing() replaces the products list with a
    MagicMock, losing all products.
    """
    if convert_fn is None:
        convert_fn = lambda p, **kw: p  # noqa: E731
    return [
        patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
        patch("src.core.tools.products.get_principal_object", return_value=principal),
        patch("src.core.tools.products.convert_product_model_to_schema", side_effect=convert_fn),
        patch(
            "src.services.dynamic_products.generate_variants_for_brief",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.services.dynamic_pricing_service.DynamicPricingService",
            **{"return_value.enrich_products_with_pricing.side_effect": lambda products, **kw: products},
        ),
    ]


class TestIdentityValidation:
    """Test _get_products_impl identity validation error paths.

    Intent: _get_products_impl must refuse requests where tenant context
    cannot be determined. The require_tenant guard rejects any missing tenant
    (None or empty dict) with AdCPAuthenticationError, regardless of whether a
    principal is present.
    """

    @pytest.mark.asyncio
    async def test_principal_without_tenant_raises_auth_error(self):
        """Principal present but no tenant → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="user-123", tenant=None)
        req = _make_request()

        from src.core.tools.products import _get_products_impl

        with pytest.raises(AdCPAuthenticationError, match="No tenant context available"):
            await _get_products_impl(req, identity)

    @pytest.mark.asyncio
    async def test_no_principal_no_tenant_raises_authentication_error(self):
        """No principal AND no tenant → AdCPAuthenticationError."""
        identity = _make_identity(principal_id=None, tenant=None)
        req = _make_request()

        from src.core.tools.products import _get_products_impl

        with pytest.raises(AdCPAuthenticationError, match="No tenant context available"):
            await _get_products_impl(req, identity)

    @pytest.mark.asyncio
    async def test_empty_tenant_dict_treated_as_no_tenant(self):
        """Empty tenant dict {} is falsy and treated as no tenant."""
        identity = _make_identity(principal_id="user-1", tenant={})
        req = _make_request()

        from src.core.tools.products import _get_products_impl

        with pytest.raises(AdCPAuthenticationError, match="No tenant context available"):
            await _get_products_impl(req, identity)


class TestProductConversionError:
    """Test product conversion error path.

    Intent: when a product stored in DB has corrupt data that fails schema
    conversion, the error must be fatal (not silently skipped) and include
    the product_id for debugging.
    """

    @pytest.mark.asyncio
    async def test_convert_failure_raises_adapter_error_with_product_id(self):
        """convert_product_model_to_schema raises → AdCPAdapterError with product_id."""
        from src.core.exceptions import AdCPAdapterError

        tenant = _make_tenant()
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_product = MagicMock()
        mock_product.product_id = "corrupt-product-42"

        mock_uow = _mock_uow_with_products([mock_product])

        with (
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch("src.core.tools.products.get_principal_object", return_value=None),
            patch(
                "src.core.tools.products.convert_product_model_to_schema",
                side_effect=Exception("missing required field 'delivery_type'"),
            ),
        ):
            from src.core.tools.products import _get_products_impl

            with pytest.raises(AdCPAdapterError, match="corrupt-product-42") as exc_info:
                await _get_products_impl(req, identity)

            assert "missing required field" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_convert_failure_is_not_silently_swallowed(self):
        """Unlike get_product_catalog, _get_products_impl must raise on conversion error."""
        from src.core.exceptions import AdCPAdapterError

        tenant = _make_tenant()
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        good_product = MagicMock()
        good_product.product_id = "good-1"
        bad_product = MagicMock()
        bad_product.product_id = "bad-1"

        def convert_with_error(p, **kw):
            if p.product_id == "bad-1":
                raise TypeError("unexpected None for pricing_options")
            return p

        mock_uow = _mock_uow_with_products([good_product, bad_product])

        with (
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch("src.core.tools.products.get_principal_object", return_value=None),
            patch(
                "src.core.tools.products.convert_product_model_to_schema",
                side_effect=convert_with_error,
            ),
        ):
            from src.core.tools.products import _get_products_impl

            with pytest.raises(AdCPAdapterError, match="bad-1"):
                await _get_products_impl(req, identity)


class TestPropertyListResolution:
    """Test property list resolution error paths.

    Intent: a typed AdCPAdapterError passes through untouched. The
    generic-exception case is NOT tested here on purpose: what matters about it
    is the code and recovery hint the BUYER receives, which only a wire test can
    see -- tests/integration/test_get_products_property_list_error_wire.py.
    """

    @pytest.mark.asyncio
    async def test_adapter_error_propagates_directly(self):
        """AdCPAdapterError from resolve_property_list → re-raised as-is."""
        from src.core.exceptions import AdCPAdapterError

        tenant = _make_tenant()
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)

        from adcp.types import PropertyListReference

        req = _make_request()
        req.property_list = PropertyListReference(agent_url="https://example.com", list_id="test-list")

        mock_uow = _mock_uow_with_products([])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "src.core.property_list_resolver.resolve_property_list",
                    new_callable=AsyncMock,
                    side_effect=AdCPAdapterError("property list service unreachable"),
                )
            )

            from src.core.tools.products import _get_products_impl

            with pytest.raises(AdCPAdapterError, match="property list service unreachable"):
                await _get_products_impl(req, identity)


class TestFilterBranches:
    """Test filter type dispatch branches.

    Intent: product filtering supports multiple format_id representations
    (str, dict, FormatId) because products come from different sources.
    Each filter must handle all three shapes correctly.
    """

    async def _run_with_products_and_filters(self, products, filters_dict, extra_patches=None):
        """Helper: run _get_products_impl with pre-built products and filters."""
        tenant = _make_tenant()
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request(filters=filters_dict)

        mock_uow = _mock_uow_with_products(products)
        all_patches = _standard_patches(mock_uow) + (extra_patches or [])

        with contextlib.ExitStack() as stack:
            for p in all_patches:
                stack.enter_context(p)

            from src.core.tools.products import _get_products_impl

            return await _get_products_impl(req, identity)

    @pytest.mark.asyncio
    async def test_is_fixed_price_exercises_pricing_check(self):
        """Cover is_fixed_price filter branch.

        Spec: is_fixed_price=true matches products with at least one pricing
        option that has fixed_price set. Uses po.root.fixed_price on the
        PricingOption RootModel wrapper.
        """
        product = create_test_product(
            product_id="fixed-prod",
            pricing_options=[create_test_cpm_pricing_option(is_fixed=True, fixed_price=10.0)],
        )

        result = await self._run_with_products_and_filters([product], {"is_fixed_price": True})
        assert len(result.products) == 1

    @pytest.mark.asyncio
    async def test_is_fixed_price_false_also_exercises_branch(self):
        """Cover is_fixed_price=False — matches products with auction pricing (no fixed_price)."""
        product = create_test_product(
            product_id="auction-prod",
            pricing_options=[create_test_cpm_pricing_option(is_fixed=False)],
        )

        result = await self._run_with_products_and_filters([product], {"is_fixed_price": False})
        # Auction option has fixed_price=None, so matches is_fixed_price=False
        assert len(result.products) == 1

    @pytest.mark.asyncio
    async def test_format_types_filter_with_format_id_objects(self):
        """Cover format_types filter FormatId branch (L546-563).

        Product.format_ids are FormatId objects. The filter dispatches through
        isinstance(format_id, FormatId) and looks up the format type via
        get_format_by_id. The format type is added to product_format_types as
        a string, but req.filters.format_ids contains FormatCategory enum
        values. Since FormatCategory is not a str enum, the comparison always
        fails. This documents the current behavior.
        """
        format_obj = MagicMock()
        format_obj.type = "display"

        product = create_test_product(product_id="prod1", format_ids=["display_300x250"])

        result = await self._run_with_products_and_filters(
            [product],
            {"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_standard"}]},
            extra_patches=[
                patch("src.core.schemas.get_format_by_id", return_value=format_obj),
            ],
        )
        # FormatCategory.display != "display" (enum is not str mixin), so filter excludes all
        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_format_types_filter_excludes_wrong_type(self):
        """format_types filter excludes products whose formats don't match."""
        format_obj = MagicMock()
        format_obj.type = "video"

        product = create_test_product(product_id="prod1", format_ids=["video_preroll"])

        result = await self._run_with_products_and_filters(
            [product],
            {"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_standard"}]},
            extra_patches=[
                patch("src.core.schemas.get_format_by_id", return_value=format_obj),
            ],
        )
        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_format_ids_filter_matches_format_id_objects(self):
        """format_ids filter extracts .id from FormatId objects on both sides.

        Product.format_ids contains FormatId objects. Request filter.format_ids
        also contains FormatId objects. The filter extracts .id from both and
        compares the string IDs.
        """
        product = create_test_product(product_id="prod1", format_ids=["display_300x250"])

        filter_format_id = FormatId(agent_url="https://example.com", id="display_300x250")
        result = await self._run_with_products_and_filters([product], {"format_ids": [filter_format_id]})
        assert len(result.products) == 1

    @pytest.mark.asyncio
    async def test_format_ids_filter_no_match_excludes(self):
        """format_ids filter excludes products with no matching format IDs."""
        product = create_test_product(product_id="prod1", format_ids=["video_preroll"])

        filter_format_id = FormatId(agent_url="https://example.com", id="display_300x250")
        result = await self._run_with_products_and_filters([product], {"format_ids": [filter_format_id]})
        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_standard_formats_only_includes_standard(self):
        """standard_formats_only includes products with IAB standard format prefixes.

        Standard prefixes: display_, video_, audio_, native_.
        FormatId objects are dispatched through isinstance(format_id, FormatId)
        and .id is checked against the prefix list.
        """
        product = create_test_product(product_id="prod1", format_ids=["display_300x250"])

        result = await self._run_with_products_and_filters([product], {"standard_formats_only": True})
        assert len(result.products) == 1

    @pytest.mark.asyncio
    async def test_standard_formats_only_excludes_custom(self):
        """standard_formats_only excludes products with non-standard format IDs."""
        product = create_test_product(product_id="prod1", format_ids=["takeover_homepage"])

        result = await self._run_with_products_and_filters([product], {"standard_formats_only": True})
        assert len(result.products) == 0


class TestPolicyEligibility:
    """Test policy eligibility filtering.

    Intent: when advertising policy is enabled, products must pass the policy
    check to be included. Products that fail are excluded, not errored.
    """

    @pytest.mark.asyncio
    async def test_policy_enabled_eligible_product_included(self):
        """Product passing policy check is included in results."""
        tenant = _make_tenant()
        tenant["advertising_policy"] = '{"enabled": true}'
        tenant["gemini_api_key"] = "fake-key"
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        product = create_test_product(product_id="eligible-prod")

        mock_policy_service = MagicMock()
        mock_policy_result = MagicMock()
        mock_policy_result.status = "compliant"
        mock_policy_service.check_brief_compliance = AsyncMock(return_value=mock_policy_result)
        mock_policy_service.check_product_eligibility.return_value = (True, None)

        mock_uow = _mock_uow_with_products([product])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch("src.core.tools.products.PolicyCheckService", return_value=mock_policy_service))

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert len(result.products) == 1
        assert result.products[0].product_id == "eligible-prod"

    @pytest.mark.asyncio
    async def test_policy_enabled_ineligible_product_excluded(self):
        """Product failing policy check is excluded from results."""
        tenant = _make_tenant()
        tenant["advertising_policy"] = '{"enabled": true}'
        tenant["gemini_api_key"] = "fake-key"
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        good = create_test_product(product_id="good-prod")
        bad = create_test_product(product_id="policy-fail-prod")

        mock_policy_service = MagicMock()
        mock_policy_result = MagicMock()
        mock_policy_result.status = "compliant"
        mock_policy_service.check_brief_compliance = AsyncMock(return_value=mock_policy_result)

        def check_eligibility(policy_result, product):
            if product.product_id == "policy-fail-prod":
                return (False, "violates alcohol content policy")
            return (True, None)

        mock_policy_service.check_product_eligibility.side_effect = check_eligibility

        mock_uow = _mock_uow_with_products([good, bad])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch("src.core.tools.products.PolicyCheckService", return_value=mock_policy_service))

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert len(result.products) == 1
        assert result.products[0].product_id == "good-prod"

    @pytest.mark.asyncio
    async def test_policy_check_runs_for_ai_config_only_tenant(self):
        """A tenant whose AI config lives in ai_config still gets its policy checks run.

        The Admin UI writes ai_config; only the legacy gemini_api_key column was read
        here, so ticking "enabled" did nothing for those tenants and the brief was never
        checked. The service must also be constructed from the tenant's own configuration
        rather than the deprecated gemini_api_key= parameter, which pins provider/model.
        """
        from src.services.ai.config import TenantAIConfig

        tenant = _make_tenant()
        tenant["advertising_policy"] = '{"enabled": true}'
        tenant["ai_config"] = {"provider": "anthropic", "model": "claude-x", "api_key": "tenant-key"}
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_policy_service = MagicMock()
        mock_policy_result = MagicMock()
        mock_policy_result.status = "compliant"
        mock_policy_service.check_brief_compliance = AsyncMock(return_value=mock_policy_result)
        mock_policy_service.check_product_eligibility.return_value = (True, None)

        mock_uow = _mock_uow_with_products([create_test_product(product_id="prod-a")])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            policy_cls = stack.enter_context(patch("src.core.tools.products.PolicyCheckService"))
            policy_cls.return_value = mock_policy_service

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        policy_cls.assert_called_once_with(
            tenant_ai_config=TenantAIConfig(provider="anthropic", model="claude-x", api_key="tenant-key")
        )
        mock_policy_service.check_brief_compliance.assert_awaited_once()
        assert [p.product_id for p in result.products] == ["prod-a"]

    @pytest.mark.asyncio
    async def test_policy_check_skipped_when_tenant_configures_no_ai(self):
        """No tenant AI configuration → the check is skipped, not run on platform credentials.

        The gate is the tenant's own configuration, never factory.is_ai_enabled(): the
        latter counts the platform GEMINI_API_KEY (which tests/conftest.py sets for every
        test, as production deployments do), so gating on it would start running policy
        checks — and raising AdCPPolicyViolationError — for tenants that configured none.
        """
        tenant = _make_tenant()
        tenant["advertising_policy"] = '{"enabled": true}'  # enabled, but nothing configured

        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_uow = _mock_uow_with_products([create_test_product(product_id="prod-a")])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            policy_cls = stack.enter_context(patch("src.core.tools.products.PolicyCheckService"))

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        policy_cls.assert_not_called()
        assert [p.product_id for p in result.products] == ["prod-a"]


class TestAIRankingDisabled:
    """Test AI ranking disabled path.

    Intent: when a tenant has a ranking prompt but AI is not enabled,
    products should still be returned unranked.
    """

    @pytest.mark.asyncio
    async def test_ai_not_enabled_returns_unranked_products(self):
        """When AI is not enabled, products are returned in their original order."""
        tenant = _make_tenant()
        tenant["product_ranking_prompt"] = "rank by relevance"
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        product_a = create_test_product(product_id="prod-a")
        product_b = create_test_product(product_id="prod-b")

        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = False

        mock_uow = _mock_uow_with_products([product_a, product_b])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch("src.services.ai.factory.get_factory", return_value=mock_factory))

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert len(result.products) == 2
        assert result.products[0].product_id == "prod-a"
        assert result.products[1].product_id == "prod-b"

    @pytest.mark.asyncio
    async def test_unresolvable_ai_config_reports_one_advisory(self, monkeypatch, caplog):
        """Ranking configured but nothing resolves → unranked products AND one advisory.

        The buyer must be able to tell an unranked list from a ranked one; a plausible
        looking list returned in catalog order with no signal is the silent failure
        this advisory exists to end.

        The platform environment key is deleted deliberately and the factory rebuilt
        against that environment: ``is_ai_enabled`` returns True on the platform key
        alone (``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test), so a test
        that left it set would take the ranked branch regardless of what the tenant
        configured. Only the singleton lookup is patched — the resolution rule under
        test (``TenantAIConfig.from_tenant`` + the real ``is_ai_enabled``) runs for real.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("PYDANTIC_AI_PROVIDER", raising=False)

        from src.services.ai.factory import AIServiceFactory

        keyless_factory = AIServiceFactory()

        tenant = _make_tenant()
        tenant["product_ranking_prompt"] = "rank by relevance"
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_uow = _mock_uow_with_products([create_test_product(product_id="prod-a")])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(patch("src.services.ai.factory.get_factory", return_value=keyless_factory))
            stack.enter_context(caplog.at_level(logging.WARNING, logger="src.core.tools.products"))

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert [p.product_id for p in result.products] == ["prod-a"]
        assert result.errors is not None and len(result.errors) == 1
        advisory = result.errors[0]
        assert advisory.code == "CONFIGURATION_ERROR"
        assert advisory.recovery == "terminal"
        assert advisory.field == "products[]"
        assert "unranked" in advisory.message

        # The seller's own log line, at WARNING. The advisory tells the BUYER the list is
        # unranked; this tells the SELLER that their AI configuration is the reason, and
        # it is the half an operator greps for.
        _assert_one_products_warning(caplog, "no usable AI configuration")

        # The advisory has to be legal on the wire, not merely constructible: it rides
        # in a completed (not failed) response, which the pinned schema permits only
        # because errors[] is required just for status == "failed".
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(
            "media-buy/get-products-response.json", result.model_dump(mode="json", exclude_none=True)
        )

    @pytest.mark.asyncio
    async def test_no_ranking_prompt_emits_no_advisory(self, monkeypatch):
        """A tenant that never asked for ranking is not told ranking is unavailable.

        The singleton lookup is PATCHED, the way the two advisory tests above do it,
        instead of leaning on ``monkeypatch.delenv("GEMINI_API_KEY")``. Deleting the
        variable alone grades nothing here: ``get_factory`` is ``lru_cache``d, so the
        first test in the process to call it builds the factory while
        ``tests/conftest.py`` still sets the key, and every later deletion leaves that
        cached instance — with the key baked into ``_platform_defaults`` — in place. A
        test whose premise is "no platform key" while the live factory still holds one
        passes for the wrong reason, and would reach a real provider the moment the
        code path it exercises grew a model call.

        The stronger assertion is that the factory is never CONSULTED at all: a tenant
        with no ``product_ranking_prompt`` must not reach the ranking decision, so
        neither the advisory nor an AI call can be produced regardless of what any
        environment holds.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("PYDANTIC_AI_PROVIDER", raising=False)

        from src.services.ai.factory import AIServiceFactory

        keyless_factory = AIServiceFactory()

        tenant = _make_tenant()  # no product_ranking_prompt
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_uow = _mock_uow_with_products([create_test_product(product_id="prod-a")])
        patches = _standard_patches(mock_uow)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            get_factory = stack.enter_context(
                patch("src.services.ai.factory.get_factory", return_value=keyless_factory)
            )

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert result.errors is None
        get_factory.assert_not_called()


class TestAIRankingUsesTenantConfig:
    """The ranking path must consult the tenant's own AI configuration.

    Intent: a tenant that configures its API key in the Admin UI (stored in
    tenants.ai_config) gets AI ranking. Reading only the platform-level environment
    key meant such a tenant silently got no ranking at all.

    BOTH factory calls are graded here. The previous pair of tests set
    ``is_ai_enabled.return_value = False``, which stops the impl before
    ``create_model`` is ever reached — so the argument that actually configures the
    model went ungraded, and reverting the production line left them green.
    ``create_autospec`` binds each assertion against the real ``AIServiceFactory``
    signature, so it grades the call rather than this call site's punctuation.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tenant_ai_fields", "expected_config"),
        [
            pytest.param({"ai_config": _TENANT_AI_CONFIG}, _RESOLVED_FROM_AI_CONFIG, id="ai_config_only"),
            pytest.param({"gemini_api_key": _LEGACY_KEY}, _RESOLVED_FROM_LEGACY_KEY, id="legacy_key_only"),
            pytest.param(
                {"ai_config": _TENANT_AI_CONFIG, "gemini_api_key": _LEGACY_KEY},
                _RESOLVED_FROM_AI_CONFIG,
                id="both_set_ai_config_wins",
            ),
            pytest.param({}, None, id="neither_set"),
        ],
    )
    async def test_resolved_tenant_config_reaches_both_factory_calls(self, tenant_ai_fields, expected_config):
        """Every resolution branch reaches is_ai_enabled AND create_model unchanged.

        The ``both_set`` case is the one that pins PRECEDENCE: ai_config and the legacy
        column resolve to different providers and different keys, so swapping the two
        resolution lines in ``TenantAIConfig.from_tenant`` fails this branch. Without
        it, the precedence rule is asserted nowhere.

        The ``neither_set`` case asserts ``None`` rather than skipping the factory:
        ``is_ai_enabled`` is mocked truthy so the ranked branch is taken regardless, and
        an autospec mock distinguishes ``is_ai_enabled()`` from ``is_ai_enabled(None)``
        — dropping the argument entirely does not pass.
        """
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult
        from src.services.ai.factory import AIServiceFactory

        model = object()
        factory = create_autospec(AIServiceFactory, instance=True)
        factory.is_ai_enabled.return_value = True
        factory.create_model.return_value = model

        ranked = ProductRankingResult(
            rankings=[ProductRanking(product_id="prod-a", relevance_score=0.9, reason="matches the brief")]
        )

        with ProductEnv(product_ranking_prompt="rank by relevance", **tenant_ai_fields) as env:
            product_a = env.add_product(product_id="prod-a")
            env.mock["ranking_factory"].return_value = factory

            with (
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent") as make_agent,
                patch(
                    "src.services.ai.agents.ranking_agent.rank_products_async",
                    new_callable=AsyncMock,
                    return_value=ranked,
                ) as rank,
            ):
                response = await env.call_impl(brief="video inventory")

        factory.is_ai_enabled.assert_called_once_with(expected_config)
        factory.create_model.assert_called_once_with(tenant_ai_config=expected_config)
        # The model the factory built is the one the ranking agent runs on: without
        # this, create_model could receive the right config and have its result dropped.
        make_agent.assert_called_once_with(model)
        # WHICH text went in as WHICH argument, not merely that the call happened.
        # ``rank_products_async`` takes the seller's ranking prompt as ``custom_prompt``
        # and the buyer's brief as ``brief``; the prompt builder concatenates both, so
        # swapping them changes the wording sent to the model and nothing else. An
        # ``await_count == 1`` assertion could not see that: with the two keyword
        # arguments exchanged in ``_rank_products_with_ai`` this file and the BDD
        # feature were both fully green (39 passed).
        rank.assert_awaited_once_with(
            agent=make_agent.return_value,
            custom_prompt="rank by relevance",
            brief="video inventory",
            products=[product_a],
        )
        assert [p.product_id for p in response.products] == ["prod-a"]
        assert response.errors is None

    @pytest.mark.asyncio
    async def test_malformed_ai_config_returns_unranked_products(self, monkeypatch, caplog):
        """A broken seller config row degrades the AI feature, it does not fail the request.

        Deliberately runs the REAL ``AIServiceFactory``. Strict
        ``TenantAIConfig.model_validate`` raised pydantic's ``ValidationError`` inside
        ``is_ai_enabled``, which the impl's ``(ImportError, RuntimeError, OSError)``
        handler does not catch; ``ValidationError`` subclasses ``ValueError``, so every
        transport normalised it into a VALIDATION_ERROR/correctable envelope naming
        ``settings.temperature`` — a field that does not exist in the get_products
        request schema. Mocking the factory is precisely how that escaped review, so a
        mock here would grade nothing.

        The platform environment key is deleted and the factory rebuilt against that
        environment: ``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test and
        ``is_ai_enabled`` returns True on the platform key alone, so leaving it set
        would take the ranked branch no matter what the tenant stored.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("PYDANTIC_AI_PROVIDER", raising=False)

        from src.services.ai.factory import AIServiceFactory

        keyless_factory = AIServiceFactory()

        # temperature 9 violates ModelSettings' le=2 bound — the reviewer's reproduction.
        malformed = {"provider": "google", "api_key": "tenant-key", "settings": {"temperature": 9}}

        with ProductEnv(product_ranking_prompt="rank by relevance", ai_config=malformed) as env:
            env.add_product(product_id="prod-a")
            env.add_product(product_id="prod-b")
            env.mock["ranking_factory"].return_value = keyless_factory

            with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
                response = await env.call_impl(brief="video inventory")

        # Catalog order, not an exception and not a truncated list.
        assert [p.product_id for p in response.products] == ["prod-a", "prod-b"]

        # getMessage() renders the %s arguments; `record.message` is only populated
        # once a formatter has run, so it would be missing on the raw record.
        ignored = [r for r in caplog.records if "malformed tenant ai_config" in r.getMessage()]
        assert len(ignored) == 1, [r.getMessage() for r in caplog.records]
        assert ignored[0].levelno == logging.WARNING

        # And the buyer is told the list is unranked rather than left to guess.
        assert response.errors is not None and len(response.errors) == 1
        assert response.errors[0].code == "CONFIGURATION_ERROR"


class TestProductionShapedTenantAIConfigurations:
    """The three tenant rows that actually occur in production, with the platform key SET.

    Every other ranking test in this file either gives the tenant its own well-formed
    ``api_key`` or deletes ``GEMINI_API_KEY`` first. Neither is the deployed shape: a real
    operator sets one platform Google key and then sellers store whatever the Admin UI
    wrote for them. The three rows below are what that produces, and each one is a
    reproduction from review:

    * ``{"provider": ..., "model": ...}`` with no ``api_key`` — literally what
      ``src/admin/blueprints/settings.py`` writes when the seller leaves the key field
      blank, while flashing "AI features will be disabled".
    * a row that fails validation but carries a real, usable ``api_key``.
    * a provider for which no credential exists anywhere, while the operator's Google key
      sits in the environment.

    What each must NOT do is the same in all three: no request may be built for one vendor
    carrying another vendor's secret, and no seller who was told AI is off may have a live
    LLM call made on the operator's credential. ``get_products`` is auth-OPTIONAL
    discovery (``src/core/routes/api_v1.py``), so an anonymous buyer's brief is enough to
    trigger any of these paths.
    """

    @staticmethod
    def _ranked(product_id: str = "prod-a"):
        """A successful ranking result, so the tests can tell "ranked" from "degraded"."""
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        return ProductRankingResult(
            rankings=[ProductRanking(product_id=product_id, relevance_score=0.9, reason="matches the brief")]
        )

    @pytest.mark.asyncio
    async def test_keyless_admin_ui_row_skips_policy_and_ranks_on_the_platforms_own_vendor(self, monkeypatch, caplog):
        """A provider/model row with no key is not a tenant configuration.

        Two failures were reproduced from this one row while the platform key was set.
        The policy gate opened on it, so ``check_brief_compliance`` made a live LLM call
        on the OPERATOR's credential for a seller the Admin UI had just told "AI features
        will be disabled" — and a BLOCKED verdict there raises
        ``AdCPPolicyViolationError``, a new rejection visible to the buyer. And the
        ranking path resolved provider and key from independent expressions, so the row's
        ``provider: anthropic`` was paired with the platform's ``GEMINI_API_KEY`` and the
        operator's Google secret was addressed to api.anthropic.com.

        Correct: the row resolves to no tenant configuration at all, the policy gate skips
        exactly as it did before this feature existed, and ranking falls back to the
        platform's OWN coherent provider+model+key — Google's model on Google's key.
        """
        admin_ui_row = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}  # key left blank

        with _platform_ai_environment(monkeypatch) as (factory, built):
            with ProductEnv(
                product_ranking_prompt="rank by relevance",
                ai_config=admin_ui_row,
                advertising_policy={"enabled": True},
            ) as env:
                env.add_product(product_id="prod-a")
                env.mock["ranking_factory"].return_value = factory
                policy_cls = env.mock["policy_service"]

                with (
                    patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                    patch(
                        "src.services.ai.agents.ranking_agent.rank_products_async",
                        new_callable=AsyncMock,
                        return_value=self._ranked(),
                    ),
                    caplog.at_level(logging.WARNING, logger="src.core.tools.products"),
                ):
                    response = await env.call_impl(brief="video inventory")

        # Not constructed, so no brief was sent anywhere on the operator's credential.
        policy_cls.assert_not_called()
        # And skipped for the RIGHT reason. `assert_not_called()` alone is also satisfied
        # by policy checks being off, which is not what this tenant configured — the row
        # says enabled, and it is the missing tenant credential that stops the check.
        _assert_one_products_warning(caplog, "no AI configuration")
        assert built == [(CANONICAL_GOOGLE_PROVIDER, DEFAULT_GOOGLE_MODEL, _PLATFORM_GEMINI_SECRET)], built
        assert [p.product_id for p in response.products] == ["prod-a"]
        assert response.errors is None

    @pytest.mark.asyncio
    async def test_malformed_row_hands_its_key_to_nobody_and_still_ranks(self, monkeypatch, caplog):
        """A row that fails validation carries a usable key — and it must go nowhere.

        ``temperature: 9`` violates ``ModelSettings``' ``le=2``, so the row does not
        parse; the ``api_key`` beside it is perfectly good. Two opposite mistakes are
        available and both were reproduced: pairing the surviving fields with the platform
        key (the seller's chosen vendor addressed with the operator's Google secret), and
        pairing the seller's Anthropic key with the platform's Google client.

        Correct: a row that does not parse resolves to nothing, so neither half of it is
        used — not its provider, not its key — the seller gets one WARNING naming the row,
        and ranking runs on the platform's own coherent configuration. No advisory is due
        because the products really were ranked.
        """
        seller_key = "sk-ant-SELLER-OWN-KEY"
        malformed = {"provider": "anthropic", "api_key": seller_key, "settings": {"temperature": 9}}

        with _platform_ai_environment(monkeypatch) as (factory, built):
            with ProductEnv(
                product_ranking_prompt="rank by relevance",
                ai_config=malformed,
                advertising_policy={"enabled": True},
            ) as env:
                env.add_product(product_id="prod-a")
                env.mock["ranking_factory"].return_value = factory
                policy_cls = env.mock["policy_service"]

                with (
                    patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                    patch(
                        "src.services.ai.agents.ranking_agent.rank_products_async",
                        new_callable=AsyncMock,
                        return_value=self._ranked(),
                    ),
                    caplog.at_level(logging.WARNING),
                ):
                    response = await env.call_impl(brief="video inventory")

        policy_cls.assert_not_called()
        _assert_one_products_warning(caplog, "no AI configuration")
        assert built == [(CANONICAL_GOOGLE_PROVIDER, DEFAULT_GOOGLE_MODEL, _PLATFORM_GEMINI_SECRET)], built
        assert seller_key not in str(built), built
        assert [p.product_id for p in response.products] == ["prod-a"]
        assert response.errors is None

        # One warning per CONSUMER, not one per request: the policy gate and the ranking
        # path each ask `TenantAIConfig.from_tenant` the same question, and this request
        # takes both. Pinning 2 rather than "at least 1" is deliberate — a third copy
        # would mean a fourth consumer appeared, or that one of them started re-resolving
        # inside a loop.
        ignored = [r for r in caplog.records if "malformed tenant ai_config" in r.getMessage()]
        assert len(ignored) == 2, [r.getMessage() for r in caplog.records]
        assert {r.levelno for r in ignored} == {logging.WARNING}
        assert {r.name for r in ignored} == {"src.services.ai.config"}

    @pytest.mark.asyncio
    async def test_provider_without_a_matching_credential_builds_nothing_and_advises(self, monkeypatch, caplog):
        """No credential for the resolved provider means AI is off — the Google key is not a substitute.

        The deployment names Anthropic (``PYDANTIC_AI_PROVIDER`` / ``PYDANTIC_AI_MODEL``),
        only the operator's ``GEMINI_API_KEY`` is present, and the seller's own row is the
        keyless Admin-UI shape. The platform's key is whatever
        ``_get_provider_api_key(PYDANTIC_AI_PROVIDER)`` finds, so there is no Anthropic
        credential anywhere — and a Google key lying around must not stand in for one.
        "There is a key somewhere" is precisely the reasoning that green-lit a request
        which could only run by borrowing another vendor's secret.

        Correct: nothing coherent resolves, so nothing is built at all — ``built == []``
        is the strongest available statement that no secret crossed vendors — the catalog
        comes back unranked, and the buyer is TOLD it is unranked instead of being handed a
        plausible-looking list in catalog order.

        SCOPE. This is the products-path reachable half. The other half of the
        provider-blind fallback — a tenant config that names a foreign provider reaching
        the factory WITHOUT a key — cannot occur here any more, because
        ``TenantAIConfig.from_tenant`` now returns either a config with the tenant's own
        key or None. It is graded at the seam it can still reach, by
        ``tests/unit/test_ai_service_factory.py`` (``test_platform_key_is_never_lent_to_another_vendor``
        and ``test_ai_is_disabled_when_the_platform_key_belongs_to_another_vendor``);
        restating it here would be a second copy that grades nothing new.
        """
        with _platform_ai_environment(monkeypatch, provider="anthropic", model="claude-sonnet-4-20250514") as (
            factory,
            built,
        ):
            with ProductEnv(
                product_ranking_prompt="rank by relevance",
                ai_config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
                advertising_policy={"enabled": True},
            ) as env:
                env.add_product(product_id="prod-a")
                env.add_product(product_id="prod-b")
                env.mock["ranking_factory"].return_value = factory
                policy_cls = env.mock["policy_service"]

                with (
                    patch("src.services.ai.agents.ranking_agent.create_ranking_agent") as make_agent,
                    # Patched even though the point is that it is never reached: leaving
                    # the real coroutine in place means a regression that re-enables AI
                    # here reaches a live provider from a unit test instead of failing an
                    # assertion.
                    patch(
                        "src.services.ai.agents.ranking_agent.rank_products_async",
                        new_callable=AsyncMock,
                    ) as rank,
                    caplog.at_level(logging.WARNING, logger="src.core.tools.products"),
                ):
                    response = await env.call_impl(brief="video inventory")

        policy_cls.assert_not_called()
        assert built == [], built
        make_agent.assert_not_called()
        rank.assert_not_awaited()
        assert [p.product_id for p in response.products] == ["prod-a", "prod-b"]

        assert response.errors is not None and len(response.errors) == 1
        advisory = response.errors[0]
        assert advisory.code == "CONFIGURATION_ERROR"
        assert advisory.recovery == "terminal"
        assert advisory.severity == "warning"
        assert advisory.field == "products[]"
        assert "PRODUCT_RANKING_UNAVAILABLE" in advisory.message

        _assert_one_products_warning(caplog, "no usable AI configuration")

        # Legal on the wire, not merely constructible: a COMPLETED response carrying a
        # non-fatal advisory.
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(
            "media-buy/get-products-response.json", response.model_dump(mode="json", exclude_none=True)
        )


class TestRankingFailureAdvisories:
    """EVERY unranked return says so on errors[], and says the right thing about retrying.

    The misconfiguration path had an advisory; the two runtime paths did not. A provider
    401/429/5xx, a timeout, a malformed model response — pydantic-ai raises
    ``ModelHTTPError`` / ``UnexpectedModelBehavior`` / ``UsageLimitExceeded`` /
    ``UserError``, ALL of which subclass ``RuntimeError`` — was caught in
    ``_get_products_impl``, logged, and dropped through with ``advisories`` empty, so the
    response went out with ``errors=None``: a plausible-looking catalog-order list and no
    way for the buyer to tell it was never ranked, on the paths most likely to happen in
    production.

    The CODE has to differ from the misconfiguration advisory's. CONFIGURATION_ERROR
    carries recovery ``terminal`` — the pinned enumMetadata spells it "the buyer cannot
    resolve a seller-side deployment misconfiguration and MUST NOT auto-retry" — which is
    the wrong instruction for a rate limit or a 503.
    """

    @staticmethod
    async def _run_with_failing_rank(exc: Exception):
        """One product, ranking configured and enabled, ``rank_products_async`` raising.

        The factory is an autospec of the real ``AIServiceFactory`` reporting AI enabled,
        so the impl reaches the ranking call — the point of the test is what happens
        AFTER production decides ranking should run. Awaited INSIDE the ``with``: the env
        stops its patches on exit, and a coroutine returned out of the block would run
        against the real database.
        """
        from src.services.ai.factory import AIServiceFactory

        factory = create_autospec(AIServiceFactory, instance=True)
        factory.is_ai_enabled.return_value = True
        factory.create_model.return_value = object()

        with ProductEnv(product_ranking_prompt="rank by relevance", ai_config=_TENANT_AI_CONFIG) as env:
            env.add_product(product_id="prod-a")
            env.add_product(product_id="prod-b")
            env.mock["ranking_factory"].return_value = factory

            with (
                patch("src.services.ai.agents.ranking_agent.create_ranking_agent"),
                patch(
                    "src.services.ai.agents.ranking_agent.rank_products_async",
                    new_callable=AsyncMock,
                    side_effect=exc,
                ),
            ):
                return await env.call_impl(brief="video inventory")

    @pytest.mark.asyncio
    async def test_failed_ranking_call_reports_one_transient_advisory(self):
        """A provider failure returns the catalog unranked AND says so, retriably."""
        response = await self._run_with_failing_rank(RuntimeError("429 Too Many Requests"))

        assert [p.product_id for p in response.products] == ["prod-a", "prod-b"]
        assert response.errors is not None and len(response.errors) == 1
        advisory = response.errors[0]
        assert advisory.code == "SERVICE_UNAVAILABLE"
        assert advisory.recovery == "transient"
        assert advisory.severity == "warning"
        assert advisory.field == "products[]"
        assert "PRODUCT_RANKING_UNAVAILABLE" in advisory.message

        # Legal on the wire, not merely constructible: it rides in a COMPLETED response.
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(
            "media-buy/get-products-response.json", response.model_dump(mode="json", exclude_none=True)
        )

    @pytest.mark.asyncio
    async def test_advisory_never_puts_the_provider_exception_text_on_the_wire(self, caplog):
        """``get_products`` is auth-OPTIONAL discovery, so this message reaches anonymous buyers.

        A provider exception's text routinely carries the request URL, the response body
        and key fragments; the pinned enumDescription for CONFIGURATION_ERROR is explicit
        that sellers "MUST NOT include credentials, connection strings, or stack traces —
        the message is wire-visible to the buyer". The class NAME is what classifies; the
        full text belongs in the seller's log.

        BOTH halves of that split are graded here, because either one alone is satisfied
        by a change that breaks the other: dropping the log entirely keeps the secret off
        the wire, and logging at DEBUG hides from the operator the only record of why
        ranking stopped. So the exception text must be ABSENT from the buyer's advisory
        and PRESENT in a WARNING on ``src.core.tools.products``.
        """

        class ModelHTTPError(RuntimeError):
            """Stands in for pydantic_ai.exceptions.ModelHTTPError (also a RuntimeError)."""

        secret = "AIzaSy-PLATFORM-SECRET-do-not-echo"
        with caplog.at_level(logging.WARNING, logger="src.core.tools.products"):
            response = await self._run_with_failing_rank(ModelHTTPError(f"401 from https://x?key={secret}"))

        message = response.errors[0].message
        assert secret not in message, message
        assert "401" not in message, message
        # The class name survives, so the seller can still classify from the wire alone.
        assert "ModelHTTPError" in message, message

        _assert_one_products_warning(caplog, secret)

    @pytest.mark.asyncio
    async def test_unbuildable_model_reports_a_terminal_advisory_not_a_failed_request(self):
        """``create_model`` refusing a config degrades ranking; it does not 500 discovery.

        ``is_ai_enabled`` can report a coherent provider/model/key and ``create_model``
        can still refuse the combination — a provider with no explicit-API-key
        integration, where handing pydantic-ai the string form would authenticate with
        whatever else is in the environment. ``AdCPConfigurationError`` extends
        ``Exception``, not ``RuntimeError``, so nothing caught it: an anonymous buyer's
        discovery call failed outright because the SELLER had misconfigured a provider.
        Same fault as "nothing usable resolved", so the same code and the same advice.
        """
        from src.core.exceptions import AdCPConfigurationError
        from src.services.ai.factory import AIServiceFactory

        factory = create_autospec(AIServiceFactory, instance=True)
        factory.is_ai_enabled.return_value = True
        factory.create_model.side_effect = AdCPConfigurationError("provider 'gateway/x' has no explicit-key branch")

        with ProductEnv(product_ranking_prompt="rank by relevance", ai_config=_TENANT_AI_CONFIG) as env:
            env.add_product(product_id="prod-a")
            env.mock["ranking_factory"].return_value = factory
            response = await env.call_impl(brief="video inventory")

        assert [p.product_id for p in response.products] == ["prod-a"]
        assert response.errors is not None and len(response.errors) == 1
        advisory = response.errors[0]
        assert advisory.code == "CONFIGURATION_ERROR"
        assert advisory.recovery == "terminal"
        assert advisory.severity == "warning"
        assert "AdCPConfigurationError" in advisory.message

    def test_every_advisory_code_carries_the_recovery_the_pin_publishes(self):
        """The wire ``recovery`` must equal the pinned ``enumMetadata`` for its code.

        ``recovery`` is stated on the wire because pinned ``core/error.json`` makes the
        code vocabulary OPEN — a client that cannot look a code up reads ``recovery``
        instead — which only helps if what we state is what the enum says. Graded against
        the installed SDK's own schema tree, not a literal copied into this file, so a
        spec bump that reclassifies a code fails here instead of shipping stale advice.
        """
        from src.core.tools.products import _ADVISORY_RECOVERY, _unranked_products_advisory
        from tests.helpers.pinned_schema import recovery_by_code

        pinned = recovery_by_code()
        assert set(_ADVISORY_RECOVERY) <= set(pinned), "advisory code absent from the pinned enum"
        for code, recovery in _ADVISORY_RECOVERY.items():
            assert recovery == pinned[code], f"{code}: emit {recovery!r}, pin says {pinned[code]!r}"
            # And the builder actually uses the table rather than a literal of its own.
            assert _unranked_products_advisory("t", code=code, cause="x").recovery == pinned[code]


class TestAdapterPricingAnnotation:
    """Adapter annotation fail-open on expected errors.

    Product decision: pricing annotation is best-effort enrichment. If the adapter
    fails with a service error, products must still be returned without annotations.

        Covers: UC-001-MAIN-43
    """

    @pytest.mark.asyncio
    async def test_adapter_error_returns_products_without_annotations(self):
        """RuntimeError degrades gracefully.

        Covers: UC-001-MAIN-43
        """
        tenant = _make_tenant()
        identity = _make_identity(principal_id="user-1", tenant_id="test-tenant", tenant=tenant)
        req = _make_request()

        mock_principal = MagicMock()
        product = create_test_product(product_id="prod1")

        mock_uow = _mock_uow_with_products([product])
        patches = _standard_patches(mock_uow, principal=mock_principal)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "src.core.helpers.adapter_helpers.get_adapter",
                    side_effect=RuntimeError("adapter config missing"),
                )
            )

            from src.core.tools.products import _get_products_impl

            result = await _get_products_impl(req, identity)

        assert len(result.products) == 1
        assert result.products[0].product_id == "prod1"


class TestGetProductCatalogConversionError:
    """Test get_product_catalog conversion error.

    Per "No Quiet Failures" (CLAUDE.md) and commit 5444a3fb, conversion errors
    must propagate. Corrupt products indicate data integrity issues.
    """

    def test_corrupt_product_raises_valueerror(self):
        """Conversion error propagates — not silently swallowed."""
        good_product = MagicMock()
        good_product.product_id = "good-prod"
        bad_product = MagicMock()
        bad_product.product_id = "bad-prod"

        mock_converted = MagicMock()
        mock_converted.product_id = "good-prod"

        def mock_convert(p, **kw):
            if p.product_id == "bad-prod":
                raise ValueError("corrupt pricing_options JSON")
            return mock_converted

        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.products.list_all_with_inventory.return_value = [good_product, bad_product]

        with (
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch("src.core.tools.products.convert_product_model_to_schema", side_effect=mock_convert),
        ):
            from src.core.tools.products import get_product_catalog

            with pytest.raises(ValueError, match="corrupt pricing_options JSON"):
                get_product_catalog(tenant_id="test-tenant")

    def test_conversion_error_propagates_not_empty_list(self):
        """Conversion error raises, not returns empty list."""
        bad_product = MagicMock()
        bad_product.product_id = "bad-prod"

        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.products.list_all_with_inventory.return_value = [bad_product]

        with (
            patch("src.core.database.repositories.uow.ProductUoW", return_value=mock_uow),
            patch(
                "src.core.tools.products.convert_product_model_to_schema",
                side_effect=ValueError("corrupt"),
            ),
        ):
            from src.core.tools.products import get_product_catalog

            with pytest.raises(ValueError, match="corrupt"):
                get_product_catalog(tenant_id="test-tenant")
