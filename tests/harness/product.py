"""ProductEnv — integration test environment for _get_products_impl.

Two envs live here: ``ProductEnv`` (everything external mocked) and
``RealResolverProductEnv`` (identical, minus the ``resolve_property_list``
patch) for the tests that must reach the real property-list resolver and the
real egress seam.

Patches: PolicyCheckService, generate_variants_for_brief,
         get_factory (ranking), resolve_property_list.
Real: ProductUoW, get_principal_object, convert_product_model_to_schema,
      DynamicPricingService, adapter metadata, audit logger, get_db_session.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with ProductEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            ProductFactory(tenant=tenant)
            PricingOptionFactory(product__tenant=tenant)

            response = await env.call_impl(brief="video ads")
            assert len(response.products) >= 1

Available mocks via env.mock:
    "policy_service"       -- PolicyCheckService class mock
    "dynamic_variants"     -- generate_variants_for_brief AsyncMock
    "ranking_factory"      -- get_factory mock (AI ranking)
    "resolve_property_list" -- resolve_property_list AsyncMock

Transport support:
    call_impl(**kw)          -- direct _get_products_impl (sync wrapper around async)
    call_a2a(**kw)           -- get_products_raw A2A wrapper
    call_mcp(**kw)           -- get_products via the registered MCP client
    build_rest_body(**kw)    -- POST /api/v1/products body
    parse_rest_response(d)   -- JSON -> GetProductsResponse
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

from src.core.schemas import GetProductsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import ProductMixin
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.harness.egress import EgressHatchMixin

# The environment variable the platform AI key is read from. Production spells it
# once, in ``src.services.ai.config._get_provider_api_key``'s provider map, and
# that spelling is not importable as a constant — so the name is repeated here and
# the REMOVAL is verified through production's own ``is_ai_enabled`` rather than
# trusted (see ``ProductEnv.set_tenant_ai_ranking``).
_PLATFORM_AI_KEY_ENV = "GEMINI_API_KEY"


class ProductEnv(ProductMixin, IntegrationEnv):
    """Integration test environment for _get_products_impl.

    Only mocks external services (policy, dynamic variants,
    AI ranking, property list resolution). Everything else is real:
    - Real ProductUoW -> real DB queries
    - Real get_principal_object -> real DB queries
    - Real convert_product_model_to_schema -> real conversion
    - Real DynamicPricingService -> real DB queries (FormatPerformanceMetrics)
    - Real audit logging

    Fluent API (from ProductMixin):
        set_policy_approved()            -- policy check returns approved
        set_policy_blocked(reason)       -- policy check returns blocked
        set_dynamic_variants(variants)   -- configure dynamic variant generation
        set_property_list(ids)           -- configure property list resolver
        set_ranking_disabled()           -- disable AI ranking
        call_impl(brief, **kw)           -- call _get_products_impl

    Plus the two AI-ranking setters defined below:
        set_tenant_ai_ranking(cfg, prompt) -- the tenant's own AI config and ranking
                                             prompt, with the platform key removed
        set_ranking_scores(scores)         -- deterministic relevance scores
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a.
    MCP_TOOL = "get_products"
    A2A_SKILL = "get_products"
    RESPONSE_MODEL = GetProductsResponse

    EXTERNAL_PATCHES = {
        "policy_service": "src.core.tools.products.PolicyCheckService",
        "dynamic_variants": "src.services.dynamic_products.generate_variants_for_brief",
        "ranking_factory": "src.services.ai.factory.get_factory",
        "resolve_property_list": "src.core.property_list_resolver.resolve_property_list",
    }

    ASYNC_PATCHES = {"dynamic_variants", "resolve_property_list"}

    REST_ENDPOINT = "/api/v1/products"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The two texts ``rank_products_async`` takes, recorded where each one is
        # actually decided, so ``set_ranking_scores``'s stub can grade WHICH argument
        # got which. See ``set_ranking_scores`` for why that needs grading at all.
        self._tenant_ranking_prompt: str | None = None
        self._dispatched_brief: str | None = None

    def _configure_mocks(self) -> None:
        self._configure_product_mocks()

    def call_via(self, transport: Any, **kwargs: Any) -> Any:
        """Record the buyer's brief on the way through, then dispatch normally.

        Every transport — impl, mcp, a2a, rest — reaches production through this one
        frame, so it is the only place that sees the brief for all four. Recording it
        in ``call_impl`` instead would cover the impl leg alone and leave the three
        wire transports grading nothing.
        """
        if "brief" in kwargs:
            self._dispatched_brief = kwargs["brief"]
        return super().call_via(transport, **kwargs)

    @realize_e2e(
        e2e_unsupported(
            "the platform GEMINI_API_KEY is read from the LIVE SERVER's own process environment "
            "(and cached there by get_factory's lru_cache), so the harness cannot remove it; "
            "'the tenant's own ai_config is the only AI configuration present' has no server surface"
        )
    )
    def set_tenant_ai_ranking(self, ai_config: dict[str, Any] | None, ranking_prompt: str) -> None:
        """Configure AI product ranking as the TENANT configured it, and only as the tenant did.

        Dual-write, for the same reason ``AccountSyncEnv.set_billing_policy`` is one:
        the REST/A2A in-process transports read the tenant off the injected identity
        (``_tenant_overrides``), while the MCP transport runs the real auth chain and
        reads it back out of the database. A setter that wrote only one of the two
        would grade a different tenant on different transports.

        The platform key is REMOVED here, and that is the load-bearing half.
        ``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test in the suite, and
        ``AIServiceFactory.is_ai_enabled`` returns True on the platform key ALONE — so
        with it left in place, ranking runs whatever the tenant has configured and a
        scenario asserting "ranking happened" passes with the production fix reverted.
        The removal is then VERIFIED through production's own predicate rather than
        assumed: if any other provider/key combination still enables AI (say
        ``PYDANTIC_AI_PROVIDER=openai`` with an ``OPENAI_API_KEY`` in the environment),
        this raises instead of handing back a vacuous scenario.

        ``get_factory`` stays patched, but its return value becomes a REAL
        ``AIServiceFactory`` built against the key-free environment: the whole point is
        to grade production's "is this tenant's AI usable?" rule, and a MagicMock
        answering ``is_ai_enabled`` would be the test grading its own stub.

        Args:
            ai_config: The tenant's ``ai_config`` column value, or None for a tenant
                that configured a ranking prompt but no AI at all.
            ranking_prompt: The tenant's ``product_ranking_prompt`` column value.
        """
        from src.services.ai.factory import AIServiceFactory

        self._tenant_overrides["ai_config"] = ai_config
        self._tenant_overrides["product_ranking_prompt"] = ranking_prompt
        self._tenant_ranking_prompt = ranking_prompt
        self._identity_cache.clear()

        tenant = self._require_tenant_row("set_tenant_ai_ranking")
        tenant.ai_config = ai_config
        tenant.product_ranking_prompt = ranking_prompt
        self._commit_factory_data()

        original = os.environ.pop(_PLATFORM_AI_KEY_ENV, None)
        if original is not None:
            self._guard(f"env:{_PLATFORM_AI_KEY_ENV}", lambda: os.environ.__setitem__(_PLATFORM_AI_KEY_ENV, original))

        factory = AIServiceFactory()
        if factory.is_ai_enabled(None):
            raise RuntimeError(
                f"set_tenant_ai_ranking() removed {_PLATFORM_AI_KEY_ENV} but production still reports "
                "AI enabled with no tenant configuration, so the tenant's own config would not be what "
                "makes ranking run and the scenario would pass with the fix reverted. Check "
                "PYDANTIC_AI_PROVIDER and the other provider key environment variables."
            )
        self.mock["ranking_factory"].return_value = factory

    @realize_e2e(
        e2e_unsupported(
            "the ranking factory has no live-server injection surface: over e2e the server builds a "
            "real model and calls the provider, so scripted relevance scores cannot be realized"
        )
    )
    def set_ranking_scores(self, scores: Mapping[str, float]) -> None:
        """Score each product deterministically, by product_id, instead of calling a model.

        Replaces ``rank_products_async`` with a plain async function rather than a mock:
        the scenario's obligation is the ORDER on the wire, and a mock would additionally
        invite Then steps to assert on call args — an in-process-only fact that the e2e
        parameterization could never reproduce.

        THE STUB GRADES ITS OWN ARGUMENTS, because nothing downstream can.
        ``rank_products_async`` takes two free-text arguments — ``custom_prompt`` (the
        SELLER's ``product_ranking_prompt``) and ``brief`` (the BUYER's brief) — and the
        prompt builder concatenates both into one string, so passing them in the wrong
        roles produces a differently-worded prompt and an identical response shape. A
        stub that accepted both and ignored both made that swap invisible: exchanging the
        two keyword arguments in ``_rank_products_with_ai`` left every scenario here and
        every test in ``tests/unit/test_get_products_impl_coverage.py`` green (39 passed).
        Asserting inside the stub keeps the check where the call actually lands, so no
        Then step has to read in-process mock arguments to see it.

        The two expected values are not passed in — they are read from where each one is
        really decided, so they cannot drift from the scenario: the prompt from
        ``set_tenant_ai_ranking`` (which writes ``tenant.product_ranking_prompt``), the
        brief from ``call_via`` (which sees the buyer's request on every transport).

        A product the scenario did not score is a scenario bug, not a zero: the sort
        would silently bury it and the expected order would still "pass" for the wrong
        reason, so it raises.

        Args:
            scores: product_id -> relevance_score (0.0-1.0), covering every product the
                request will return.
        """
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        if self._tenant_ranking_prompt is None:
            raise RuntimeError(
                "set_ranking_scores() requires set_tenant_ai_ranking() to have run first: the "
                "seller's product_ranking_prompt is what the stub grades `custom_prompt` against, "
                "and without it the scenario would score products while grading nothing about "
                "which text production sent as which argument."
            )
        expected_prompt = self._tenant_ranking_prompt

        async def _scripted_ranking(agent: Any, custom_prompt: str, brief: str, products: list) -> Any:
            expected_brief = self._dispatched_brief
            if expected_brief is None:
                raise AssertionError(
                    "set_ranking_scores() was reached without a recorded brief. Dispatch through "
                    "env.call_via (dispatch_request does), which is where the buyer's brief is "
                    "recorded; a direct call_impl() bypasses it and would grade nothing."
                )
            if (custom_prompt, brief) != (expected_prompt, expected_brief):
                raise AssertionError(
                    "rank_products_async got the seller's prompt and the buyer's brief in the "
                    f"wrong roles.\n"
                    f"  custom_prompt: got {custom_prompt!r}, expected {expected_prompt!r} "
                    "(tenant.product_ranking_prompt)\n"
                    f"  brief:         got {brief!r}, expected {expected_brief!r} "
                    "(the buyer's request brief)"
                )
            unscored = sorted(p.product_id for p in products if p.product_id not in scores)
            if unscored:
                raise AssertionError(
                    f"set_ranking_scores() has no score for {unscored}; every product the request "
                    "returns must be scored, or the expected order grades an unstated default."
                )
            return ProductRankingResult(
                rankings=[
                    ProductRanking(product_id=pid, relevance_score=score, reason="scripted by set_ranking_scores")
                    for pid, score in scores.items()
                ]
            )

        patcher = patch("src.services.ai.agents.ranking_agent.rank_products_async", new=_scripted_ranking)
        patcher.start()
        self._guard("patch:rank_products_async", patcher.stop)

    def call_impl(self, **kwargs: Any) -> Any:  # type: ignore[override]
        """Call _get_products_impl — async-aware sync bridge.

        ProductMixin.call_impl is async. This bridge detects the calling context:
        - Async (``await env.call_impl(...)``): returns the coroutine for awaiting
        - Sync (BDD steps, ImplDispatcher): uses ``asyncio.run()``
        """
        coro = super().call_impl(**kwargs)
        try:
            asyncio.get_running_loop()
            # Already in async context (e.g., @pytest.mark.asyncio test)
            # Return the coroutine so ``await`` works
            return coro
        except RuntimeError:
            # No running loop — safe to block with asyncio.run
            return asyncio.run(coro)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Everything the caller sent, on the wire, verbatim.

        Deliberately NOT an allow-list. This used to forward only
        ``(brief, brand, filters, adcp_version)`` — the fields
        ``GetProductsBody`` happens to declare — which made the REST leg
        structurally incapable of grading request-field acceptance: a test that
        sent `account` or `time_budget` had it dropped HERE, inside the harness,
        so REST always looked like it accepted every field cleanly no matter what
        production did. That is the same "a per-transport allow-list decides
        which fields exist" defect the acceptance seam exists to remove, one
        layer out, in the tests that are supposed to catch it.

        The allow-list also went stale in the other direction: it silently
        dropped ``property_list`` when the route gained it, so
        a REST case could send the field, have the harness discard it, and pass
        — grading nothing. Reading the field list off ``GetProductsBody`` would
        have fixed that one case; forwarding verbatim fixes both, because the
        harness no longer holds an opinion about which fields exist.

        The seam is the authority: the middleware publishes the wire body and
        `@accepts_spec_request_fields` carries it to the tool, which honors or
        refuses each field. The harness's only job is to put the buyer's bytes on
        the wire unaltered.
        """
        return {k: v for k, v in kwargs.items() if v is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetProductsResponse:
        """Parse REST JSON response into GetProductsResponse."""
        return GetProductsResponse(**data)


class RealResolverProductEnv(EgressHatchMixin, ProductEnv):
    """``ProductEnv`` with the property-list resolver left UNPATCHED.

    ``ProductEnv`` mocks ``resolve_property_list`` so ordinary product tests
    never reach the network. This variant drops exactly that one patch and
    changes nothing else, so ``get_products`` runs the real resolver and the
    real egress seam — which is the point: the refusal under test has to be
    produced by production code, or the wire envelope proves nothing.

    TRAP: because the mock is gone, ``self.mock["resolve_property_list"]`` does
    not exist after ``__enter__`` — the stand-in below is deleted as soon as
    ``ProductMixin``'s happy-path wiring has finished with it. Any Given step
    calling ``ProductMixin.set_property_list()`` on this env will ``KeyError``.
    A scenario that needs a SUCCESSFUL property-list fetch wants plain
    ``ProductEnv`` (mocked resolver) or a real local origin, not this class.
    """

    EXTERNAL_PATCHES = {
        name: target for name, target in ProductEnv.EXTERNAL_PATCHES.items() if name != "resolve_property_list"
    }
    ASYNC_PATCHES = ProductEnv.ASYNC_PATCHES - {"resolve_property_list"}

    def _configure_mocks(self) -> None:
        # ProductMixin's happy-path wiring pokes ``self.mock["resolve_property_list"]``.
        # A throwaway stand-in keeps that one line harmless without forking the
        # rest of the wiring, which this env does want.
        self.mock["resolve_property_list"] = MagicMock()
        try:
            super()._configure_mocks()
        finally:
            del self.mock["resolve_property_list"]
