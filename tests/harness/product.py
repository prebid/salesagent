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
    admin_request(...)       -- an operator's admin action, on the same transport
                                (AdminOperatorMixin)
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from src.core.schemas import GetProductsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import ProductMixin
from tests.harness._realize import realize_e2e
from tests.harness.admin_client import AdminOperatorMixin
from tests.harness.database_faults import DatabaseFaultMixin
from tests.harness.egress import EgressHatchMixin

if TYPE_CHECKING:
    from tests.e2e._scripted_llm import ScriptedFailure


class ProductEnv(AdminOperatorMixin, DatabaseFaultMixin, ProductMixin, IntegrationEnv):
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
        set_platform_ai_credential()       -- the platform has a Gemini key of its own
        set_tenant_ai_ranking(prompt)      -- the seller's ranking prompt
        set_ranking_scores(scores)         -- deterministic relevance scores
        set_ranking_failure(failure)       -- the ranking model fails as a ScriptedFailure declares
        set_ranking_unavailable()          -- the ranking model answers 503
        set_ranking_connection_dropped()   -- the ranking model's endpoint drops the connection

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
        # The scripted ranking model's answer: scores by product_id, or the declared failure.
        self._scripted_scores: dict[str, float] = {}
        self._ranking_failure: ScriptedFailure | None = None
        self._ranking_patch_started = False
        # The platform's own AI credential: none unless the scenario declares one.
        self._platform_credential: Any = None

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
        """Write ``tenant.product_ranking_prompt``, tagged with this env's nonce, and remember it.

        The scripted model grades which text reached which slot of the ranking prompt,
        against the prompt recorded here; see ``set_ranking_scores``. The nonce
        (``scripted_nonce``) makes the prompt this scenario's own, so over e2e a ranking
        script another scenario installed for the shared platform key never answers this
        scenario's call.
        """
        tagged = f"{ranking_prompt} [{self.scripted_nonce}]"
        self.configure_tenant_field("product_ranking_prompt", tagged)
        self._tenant_ranking_prompt = tagged

    def _stated_platform_credential(self) -> Any:
        """The platform credential this env states in-process: started once, undone with the env."""
        from tests.helpers.settings_injection import PlatformAICredential

        if self._platform_credential is None:
            self._platform_credential = PlatformAICredential(self._platform_ai_key)
            self._platform_credential.start()
            self._guard("platform_ai_credential", self._platform_credential.stop)
        return self._platform_credential

    def _e2e_set_platform_ai_credential(self) -> None:
        """E2E realization of :meth:`set_platform_ai_credential`: the server's own key, scripted.

        Every e2e server carries ``E2E_PLATFORM_AI_KEY`` (``docker-compose.e2e.yml``). The
        ranking script is installed for that key too (``_e2e_install_ranking``), so a
        server that ranked on the platform's key would get scores, reorder or filter the
        products, and fail the scenario, as the in-process stub ranks on any model.
        """
        from tests.e2e._scripted_llm import E2E_PLATFORM_AI_KEY

        self._platform_ai_key = E2E_PLATFORM_AI_KEY

    @realize_e2e(_e2e_set_platform_ai_credential)
    def set_platform_ai_credential(self) -> None:
        """The platform has an AI credential of its own: a Gemini key in its environment.

        Declared, because whether the seller's configuration is what decides depends on
        it: with a platform key present, production that fell back to it would still rank.
        In-process the key is put in the environment (``PlatformAICredential``); the
        scripted ranking model answers any model production builds, so a fallback to the
        platform's credential shows on the wire.
        """
        self._platform_ai_key = "platform-ai-key"
        self._stated_platform_credential().set(self._platform_ai_key)

    def _e2e_set_tenant_ai_ranking(self, ranking_prompt: str) -> None:
        """E2E realization of :meth:`set_tenant_ai_ranking`: the prompt column, and nothing else.

        The live server's platform key (``E2E_PLATFORM_AI_KEY``) stays where it is, and that
        does not make the scenario vacuous: the scripted model answers only the keys a
        scenario scripted, and refuses any other as the real API refuses an invalid one.
        """
        self._write_ranking_prompt(ranking_prompt)

    @realize_e2e(_e2e_set_tenant_ai_ranking)
    def set_tenant_ai_ranking(self, ranking_prompt: str) -> None:
        """Turn on AI product ranking with ``ranking_prompt``, on the platform credential the env states.

        The seller's own AI configuration (``ai_config`` / ``gemini_api_key``) is written by
        the caller through ``configure_tenant_field``; this method owns the two things a
        column write cannot say: the ranking prompt the scripted model grades against, and
        which platform credential is in play (none, unless ``set_platform_ai_credential``
        declared one).

        ``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test, and the factory falls
        back to it. The in-process scripted model (``set_ranking_scores``) does not look at
        keys, so with that key left in place, ranking runs whatever the seller has
        configured and a scenario asserting "ranking happened" passes with the production
        fix reverted. With no platform credential declared, its absence is VERIFIED through
        production's own factory rather than assumed: if any other provider/key
        combination still builds a model (say ``PYDANTIC_AI_PROVIDER=openai`` with an
        ``OPENAI_API_KEY``), this raises instead of handing back a vacuous scenario.

        ``get_factory`` stays patched, but each call now builds a REAL ``AIServiceFactory``
        against the stated environment: the point is to grade production's "can this
        seller's AI run?" rule, and a MagicMock answering it would be the test grading its
        own stub.
        """
        from src.services.ai.factory import AIServiceFactory

        self._write_ranking_prompt(ranking_prompt)
        self._stated_platform_credential()

        if self._platform_ai_key is None and AIServiceFactory().build(None) is not None:
            raise RuntimeError(
                "set_tenant_ai_ranking() removed the platform AI credential but production still builds a model "
                "with no tenant configuration, so the tenant's own config would not be what makes ranking run "
                "and the scenario would pass with the fix reverted. Check PYDANTIC_AI_PROVIDER and the other "
                "provider key environment variables."
            )
        self.mock["ranking_factory"].side_effect = AIServiceFactory

    def _scripted_prompt_prefix(self) -> str:
        """The ranking prompt up to the buyer's brief, as production builds it for this seller."""
        from tests.e2e._scripted_llm import ranking_prompt_prefix

        if self._tenant_ranking_prompt is None:
            raise RuntimeError(
                "set_ranking_scores() / the ranking failures require set_tenant_ai_ranking() first: "
                "the seller's product_ranking_prompt is what the scripted model grades the prompt against."
            )
        return ranking_prompt_prefix(self._tenant_ranking_prompt)

    def _e2e_install_ranking(self) -> None:
        """Script the live server's ranking call: the seller's key, and the platform's if declared.

        The seller's key answers the declared failure if there is one, else the scores; the
        platform's key always answers the scores, so a fallback to it is visible. Each
        answers only when the prompt carries the seller's ranking prompt in the seller's
        slot (``require_text``), which is how the e2e leg grades the argument roles the
        in-process stub grades directly. That prompt carries this env's nonce, so the entry
        answers no other scenario's call.
        """
        from tests.e2e._scripted_llm import install_script

        prefix = self._scripted_prompt_prefix()
        rankings = [
            {"product_id": product_id, "relevance_score": score, "reason": "scripted by set_ranking_scores"}
            for product_id, score in self._scripted_scores.items()
        ]
        scores = {"output": {"rankings": rankings}, "require_text": prefix}
        failure = {**self._ranking_failure.entry, "require_text": prefix} if self._ranking_failure else None
        seller_key, *platform_keys = self.scripted_api_keys()
        install_script(seller_key, ranking=failure or scores)
        for platform_key in platform_keys:
            install_script(platform_key, ranking=scores)

    def _e2e_set_ranking_scores(self, scores: Mapping[str, float]) -> None:
        """E2E realization of :meth:`set_ranking_scores`: the scores, served by the scripted origin."""
        self._scripted_scores.update(scores)
        self._e2e_install_ranking()

    @realize_e2e(_e2e_set_ranking_scores)
    def set_ranking_scores(self, scores: Mapping[str, float]) -> None:
        """Score each product deterministically, by product_id, instead of calling a model.

        Cumulative: a second call adds scores to the first, so a scenario can score its
        products one Given at a time. A declared ranking failure stands: the scores are
        what a model WOULD answer, which is what makes a fallback to another credential
        visible.

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
        prompt it receives (``_e2e_install_ranking``).

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
        self._install_scripted_ranking()

    def _e2e_set_ranking_failure(self, failure: ScriptedFailure) -> None:
        """E2E realization of :meth:`set_ranking_failure`: the origin answers the failure's entry."""
        self._ranking_failure = failure
        self._e2e_install_ranking()

    @realize_e2e(_e2e_set_ranking_failure)
    def set_ranking_failure(self, failure: ScriptedFailure) -> None:
        """The seller's ranking model fails as ``failure`` declares (``tests/e2e/_scripted_llm.py``).

        In-process production's own ranking runs on ``failure.model()``, which raises
        ``failure.exception()``; over e2e the origin answers ``failure.entry``, which makes
        the live server's client raise that same exception. Either way production's
        ``run_agent`` is what classifies it.
        """
        self._scripted_prompt_prefix()
        self._ranking_failure = failure
        self._install_scripted_ranking()

    def set_ranking_unavailable(self) -> None:
        """The ranking model is down: the provider answers HTTP 503."""
        from tests.e2e._scripted_llm import MODEL_UNAVAILABLE

        self.set_ranking_failure(MODEL_UNAVAILABLE)

    def set_ranking_connection_dropped(self) -> None:
        """The seller's provider endpoint drops the connection without answering.

        It is no ``OSError``, so it is the case a narrower ``except`` let escape.
        """
        from tests.e2e._scripted_llm import CONNECTION_DROPPED

        self.set_ranking_failure(CONNECTION_DROPPED)

    def _install_scripted_ranking(self) -> None:
        """Replace ``rank_products_async`` with the scripted model, once per env."""
        if self._ranking_patch_started:
            return
        from src.services.ai.agents import ranking_agent
        from src.services.ai.agents.ranking_agent import ProductRanking, ProductRankingResult

        expected_prompt = self._tenant_ranking_prompt
        production_ranking = ranking_agent.rank_products_async

        async def _scripted_ranking(agent: Any, custom_prompt: str, brief: str, products: list) -> Any:
            if self._ranking_failure is not None:
                # Production's ranking, on a model that fails: the prompt is built and the
                # failure classified by production, not by this stub.
                with agent.override(model=self._ranking_failure.model()):
                    return await production_ranking(
                        agent=agent, custom_prompt=custom_prompt, brief=brief, products=products
                    )
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
