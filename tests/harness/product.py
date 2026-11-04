"""ProductEnv — integration test environment for _get_products_impl.

Two envs live here: ``ProductEnv`` (everything external mocked) and
``RealResolverProductEnv`` (identical, minus the ``resolve_property_list``
patch) for the tests that must reach the real property-list resolver and the
real egress seam.

Patches: PolicyCheckService, generate_variants_for_brief,
         get_factory (ranking), resolve_property_list.
Real: ProductUoW, convert_product_model_to_schema,
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
    call_a2a(**kw)           -- dispatch get_products through the A2A handler
    call_mcp(**kw)           -- get_products via the registered MCP client
    build_rest_body(**kw)    -- POST /api/v1/products body
    parse_rest_response(d)   -- JSON -> GetProductsResponse
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

from src.core.schemas import GetProductsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import ProductMixin
from tests.harness._realize import realize_e2e
from tests.harness.database_faults import DatabaseFaultMixin
from tests.harness.egress import EgressHatchMixin


class ProductEnv(DatabaseFaultMixin, ProductMixin, IntegrationEnv):
    """Integration test environment for _get_products_impl.

    Only mocks external services (policy, dynamic variants,
    AI ranking, property list resolution). Everything else is real:
    - Real ProductUoW -> real DB queries
    - The principal comes off the identity; nothing looks one up
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

    Plus the AI-ranking setters defined below (the seller's own AI configuration is
    written with ``configure_tenant_field``, which every transport reads):
        set_tenant_ai_ranking(prompt)  -- the seller's ranking prompt, platform key removed
        set_ranking_scores(scores)     -- deterministic relevance scores
        set_ranking_unavailable()      -- the ranking model fails

    Over e2e the scripted answers come from the compose ``scripted-llm`` origin
    (``tests/e2e/scripted_llm_origin_service.py``), keyed by ``scripted_model_key``.
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

    #: Dotted path to the skill's _impl function, for inject_untyped_exception().

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The two texts ``rank_products_async`` takes, recorded where each one is
        # actually decided, so ``set_ranking_scores``'s stub can grade WHICH argument
        # got which. See ``set_ranking_scores`` for why that needs grading at all.
        self._tenant_ranking_prompt: str | None = None
        self._dispatched_brief: str | None = None
        # The scripted ranking model's answer: scores by product_id, or the failure it raises.
        self._scripted_scores: dict[str, float] = {}
        self._scripted_ranking_failure: Exception | None = None
        self._ranking_patch_started = False

    def _configure_mocks(self) -> None:
        self._configure_product_mocks()

    def call_via(self, transport: Any, **kwargs: Any) -> Any:
        """Record the buyer's brief on the way through, then dispatch normally.

        Every transport (mcp, a2a, rest) reaches production through this one frame, so
        it is the only place that sees the brief for all of them.
        """
        if "brief" in kwargs:
            self._dispatched_brief = kwargs["brief"]
        return super().call_via(transport, **kwargs)

    def _write_ranking_prompt(self, ranking_prompt: str) -> None:
        """Write ``tenant.product_ranking_prompt`` and remember it for the scripted model.

        The scripted model grades which text reached which slot of the ranking prompt,
        against the prompt recorded here; see ``set_ranking_scores``.
        """
        self.configure_tenant_field("product_ranking_prompt", ranking_prompt)
        self._tenant_ranking_prompt = ranking_prompt

    def _e2e_set_tenant_ai_ranking(self, ranking_prompt: str) -> None:
        """E2E realization of :meth:`set_tenant_ai_ranking`: the prompt column, and nothing else.

        The live server's platform key (``GEMINI_API_KEY`` in its own environment) stays
        where it is, and that does not make the scenario vacuous here the way it would
        in-process: the scripted model answers only the API key the SELLER configured
        (``scripted_model_key``), and refuses any other key as the real API refuses an
        invalid one. A server that ranked on the platform key instead of the seller's
        would be refused, fall back to catalog order, and fail the scenario.
        """
        self._write_ranking_prompt(ranking_prompt)

    @realize_e2e(_e2e_set_tenant_ai_ranking)
    def set_tenant_ai_ranking(self, ranking_prompt: str) -> None:
        """Turn on AI product ranking with ``ranking_prompt``, with no platform AI key present.

        The seller's own AI configuration (``ai_config`` / ``gemini_api_key``) is written by
        the caller through ``configure_tenant_field``; this method owns the two things a
        column write cannot say: the ranking prompt the scripted model grades against, and
        that the seller's configuration is the ONLY one in play.

        The platform key is REMOVED here, and that is the half the scenarios depend on.
        ``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test in the suite, and
        ``AIServiceFactory.is_ai_enabled`` returns True on the platform key ALONE. The
        in-process scripted model (``set_ranking_scores``) does not look at keys, so with the
        platform key left in place, ranking runs whatever the seller has configured and a
        scenario asserting "ranking happened" passes with the production fix reverted.
        The removal is then VERIFIED through production's own predicate rather than
        assumed: if any other provider/key combination still enables AI (say
        ``PYDANTIC_AI_PROVIDER=openai`` with an ``OPENAI_API_KEY`` in the environment),
        this raises instead of handing back a vacuous scenario.

        ``get_factory`` stays patched, but its return value becomes a REAL
        ``AIServiceFactory`` built against the key-free environment: the whole point is
        to grade production's "is this seller's AI usable?" rule, and a MagicMock
        answering ``is_ai_enabled`` would be the test grading its own stub.
        """
        from src.services.ai.factory import AIServiceFactory
        from tests.helpers.settings_injection import PlatformAICredential

        self._write_ranking_prompt(ranking_prompt)

        credential = PlatformAICredential()
        credential.start()
        self._guard("platform_ai_credential", credential.stop)

        factory = AIServiceFactory()
        if factory.is_ai_enabled(None):
            raise RuntimeError(
                "set_tenant_ai_ranking() removed the platform AI credential but production still reports "
                "AI enabled with no tenant configuration, so the tenant's own config would not be what "
                "makes ranking run and the scenario would pass with the fix reverted. Check "
                "PYDANTIC_AI_PROVIDER and the other provider key environment variables."
            )
        self.mock["ranking_factory"].return_value = factory

    def _scripted_prompt_prefix(self) -> str:
        """The ranking prompt up to the buyer's brief, as production builds it for this seller.

        Built by production's own ``build_ranking_prompt`` with the seller's prompt in the
        seller's slot, so the scripted model can tell whether the call it receives put the
        seller's prompt there. Exchanging ``custom_prompt`` and ``brief`` at the call site in
        ``products.py`` moves the BRIEF into that slot, and the prefix no longer matches.
        """
        from src.services.ai.agents.ranking_agent import build_ranking_prompt

        if self._tenant_ranking_prompt is None:
            raise RuntimeError(
                "set_ranking_scores() / set_ranking_unavailable() require set_tenant_ai_ranking() first: "
                "the seller's product_ranking_prompt is what the scripted model grades the prompt against."
            )
        brief_marker = "\x00brief\x00"
        return build_ranking_prompt(self._tenant_ranking_prompt, brief_marker, []).split(brief_marker)[0]

    def _e2e_script_ranking(self, entry: dict[str, Any]) -> None:
        """Script the live server's ranking call, for the seller's own API key."""
        from tests.e2e._scripted_llm import install_script

        install_script(self.scripted_model_key, ranking={**entry, "require_text": self._scripted_prompt_prefix()})

    def _e2e_set_ranking_scores(self, scores: Mapping[str, float]) -> None:
        """E2E realization of :meth:`set_ranking_scores`: the scores, served by the scripted origin.

        The origin answers only when the prompt carries the seller's ranking prompt in the
        seller's slot (``require_text``), which is how the e2e leg grades the argument roles
        the in-process stub grades directly. A refused call falls back to catalog order.
        """
        self._scripted_scores.update(scores)
        rankings = [
            {"product_id": product_id, "relevance_score": score, "reason": "scripted by set_ranking_scores"}
            for product_id, score in self._scripted_scores.items()
        ]
        self._e2e_script_ranking({"output": {"rankings": rankings}})

    @realize_e2e(_e2e_set_ranking_scores)
    def set_ranking_scores(self, scores: Mapping[str, float]) -> None:
        """Score each product deterministically, by product_id, instead of calling a model.

        Cumulative: a second call adds scores to the first, so a scenario can score its
        products one Given at a time.

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
        two keyword arguments at the ``rank_products_async`` call in ``products.py`` left
        every ranking test green. Over e2e the scripted origin grades the same swap on the
        prompt it receives (``_e2e_set_ranking_scores``).

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
        self._scripted_prompt_prefix()  # refuses before the When, not inside production
        self._scripted_scores.update(scores)
        self._scripted_ranking_failure = None
        self._install_scripted_ranking()

    def _e2e_set_ranking_unavailable(self) -> None:
        """E2E realization of :meth:`set_ranking_unavailable`: the origin answers 503."""
        self._e2e_script_ranking({"fail_status": 503})

    @realize_e2e(_e2e_set_ranking_unavailable)
    def set_ranking_unavailable(self) -> None:
        """The ranking model is down: the call fails as pydantic-ai fails on an HTTP 503.

        ``ModelHTTPError`` is what ``GoogleModel`` raises when the API answers a 5xx, so the
        in-process failure is the exception production meets over e2e, where the scripted
        origin really answers 503.
        """
        from pydantic_ai.exceptions import ModelHTTPError

        self._scripted_prompt_prefix()
        self._scripted_ranking_failure = ModelHTTPError(status_code=503, model_name="scripted", body="unavailable")
        self._install_scripted_ranking()

    def _install_scripted_ranking(self) -> None:
        """Replace ``rank_products_async`` with the scripted model, once per env."""
        if self._ranking_patch_started:
            return
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        expected_prompt = self._tenant_ranking_prompt

        async def _scripted_ranking(agent: Any, custom_prompt: str, brief: str, products: list) -> Any:
            if self._scripted_ranking_failure is not None:
                raise self._scripted_ranking_failure
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
            unscored = sorted(p.product_id for p in products if p.product_id not in self._scripted_scores)
            if unscored:
                raise AssertionError(
                    f"set_ranking_scores() has no score for {unscored}; every product the request "
                    "returns must be scored, or the expected order grades an unstated default."
                )
            return ProductRankingResult(
                rankings=[
                    ProductRanking(product_id=pid, relevance_score=score, reason="scripted by set_ranking_scores")
                    for pid, score in self._scripted_scores.items()
                ]
            )

        patcher = patch("src.services.ai.agents.ranking_agent.rank_products_async", new=_scripted_ranking)
        patcher.start()
        self._guard("patch:rank_products_async", patcher.stop)
        self._ranking_patch_started = True

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

    # parse_rest_response: the base's, which revives RESPONSE_MODEL. The one-line override
    # here read GetProductsResponse(**data), which the served document's boundary-stamped
    # `context` makes impossible to construct.


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
