"""Contract: production's own AI agents, pointed at the scripted origin, get the scripted answers.

The e2e stack points the live server's Gemini client at ``scripted-llm``
(``GOOGLE_GEMINI_BASE_URL``) so get_products' ranking and policy check get scripted answers
over e2e. That only works if three things hold, and none of them is visible from a BDD
scenario that fails as "catalog order":

* ``google-genai`` honours ``GOOGLE_GEMINI_BASE_URL`` for the model production builds
  (``AIServiceFactory.build`` -> ``GoogleModel(provider=GoogleProvider(api_key=...))``);
* pydantic-ai accepts the origin's response as the agent's structured output, for both
  output models (``ProductRankingResult`` and ``PolicyAnalysis``);
* every refusal the origin answers (an unscripted key, a scripted failure, a prompt that
  does not carry the scripted text) and a dropped connection reach production's
  ``run_agent`` as a provider failure, which raises ``AIProviderError``: the one type
  get_products' ranking and policy check catch to fail open.

So this runs the origin IN-PROCESS on loopback and drives production's real agents and
real ``PolicyCheckService`` against it: no Docker, no compose network. The stack wiring is
``tests/unit/test_architecture_e2e_scripted_llm_wiring.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest

from src.services.ai import AIServiceFactory, TenantAIConfig
from src.services.ai.agents.ranking_agent import create_ranking_agent, rank_products_async
from src.services.ai.factory import AIProviderError
from src.services.policy_check_service import PolicyCheckService, PolicyStatus
from tests.e2e._scripted_llm import install_script, ranking_prompt_prefix
from tests.e2e.scripted_llm_origin_service import run_scripted_llm_origin

_SELLER_KEY = "seller-key-under-test"
_PROMPT = "Prefer video inventory"
_BRIEF = "video campaign for a sports launch"


@pytest.fixture
def origin() -> Iterator[str]:
    """The origin on a loopback port, with the Gemini client and the control client both aimed at it."""
    with run_scripted_llm_origin(host="127.0.0.1", port=0) as base_url:
        aimed = {"GOOGLE_GEMINI_BASE_URL": base_url, "ANTHROPIC_BASE_URL": base_url, "SCRIPTED_LLM_ORIGIN": base_url}
        with patch.dict(os.environ, aimed):
            yield base_url


def _seller_model(api_key: str = _SELLER_KEY):
    """The model production builds for a seller who configured Gemini in the Admin UI."""
    return AIServiceFactory().build(TenantAIConfig(provider="gemini", model="gemini-2.0-flash", api_key=api_key))


def _anthropic_model(api_key: str = _SELLER_KEY):
    """The model production builds for a seller who configured Anthropic."""
    return AIServiceFactory().build(TenantAIConfig(provider="anthropic", model="claude-scripted", api_key=api_key))


async def _rank(model, custom_prompt: str = _PROMPT, brief: str = _BRIEF):
    return await rank_products_async(create_ranking_agent(model), custom_prompt=custom_prompt, brief=brief, products=[])


def _prompt_prefix() -> str:
    return ranking_prompt_prefix(_PROMPT)


async def test_ranking_agent_returns_the_scripted_scores(origin: str) -> None:
    rankings = [{"product_id": "prod_b_video", "relevance_score": 0.9, "reason": "scripted"}]
    install_script(_SELLER_KEY, ranking={"output": {"rankings": rankings}, "require_text": _prompt_prefix()})

    result = await _rank(_seller_model())

    assert [(r.product_id, r.relevance_score) for r in result.rankings] == [("prod_b_video", 0.9)]


async def test_policy_service_returns_the_scripted_verdict(origin: str) -> None:
    install_script(_SELLER_KEY, policy={"output": {"status": "blocked", "reason": "scripted block"}})

    result = await PolicyCheckService(model=_seller_model()).check_brief_compliance(brief=_BRIEF)

    assert (result.status, result.reason) == (PolicyStatus.BLOCKED, "scripted block")


async def test_a_call_with_an_unscripted_key_raises_what_ranking_catches(origin: str) -> None:
    install_script(_SELLER_KEY, ranking={"output": {"rankings": []}})

    with pytest.raises(AIProviderError, match="API key not valid"):
        await _rank(_seller_model(api_key="the-platform-key"))


async def test_a_scripted_failure_raises_what_ranking_catches(origin: str) -> None:
    install_script(_SELLER_KEY, ranking={"fail_status": 503})

    with pytest.raises(AIProviderError, match="503"):
        await _rank(_seller_model())


async def test_a_dropped_connection_raises_what_ranking_catches(origin: str) -> None:
    """google-genai raises it unwrapped, and it is no OSError; run_agent still classifies it."""
    install_script(_SELLER_KEY, ranking={"drop_connection": True})

    with pytest.raises(AIProviderError) as excinfo:
        await _rank(_seller_model())

    assert isinstance(excinfo.value.__cause__, httpx.RemoteProtocolError)


async def test_a_scripted_policy_failure_raises_what_get_products_fails_open_on(origin: str) -> None:
    """What BR-RULE-002 INV-4 meets over e2e: the real service raises AIProviderError."""
    install_script(_SELLER_KEY, policy={"fail_status": 503})

    with pytest.raises(AIProviderError, match="503"):
        await PolicyCheckService(model=_seller_model()).check_brief_compliance(brief=_BRIEF)


async def test_the_seller_prompt_in_the_brief_slot_is_refused(origin: str) -> None:
    """The swap the in-process stub grades directly: the brief where the seller's prompt goes."""
    install_script(_SELLER_KEY, ranking={"output": {"rankings": []}, "require_text": _prompt_prefix()})

    with pytest.raises(AIProviderError, match="does not contain the scripted text"):
        await _rank(_seller_model(), custom_prompt=_BRIEF, brief=_PROMPT)


async def test_an_anthropic_model_is_answered_from_the_same_script(origin: str) -> None:
    """ANTHROPIC_BASE_URL: a built Anthropic model reaches the origin and gets the seller's answer.

    This is what makes the "refuses to build" Examples grade the refusal on e2e: a model
    built anyway is answered, and ranking happens, instead of failing at the real vendor.
    """
    rankings = [{"product_id": "prod_a", "relevance_score": 0.4, "reason": "scripted"}]
    install_script(_SELLER_KEY, ranking={"output": {"rankings": rankings}, "require_text": _prompt_prefix()})
    install_script(_SELLER_KEY, policy={"output": {"status": "blocked", "reason": "scripted block"}})

    ranked = await _rank(_anthropic_model())
    verdict = await PolicyCheckService(model=_anthropic_model()).check_brief_compliance(brief=_BRIEF)

    assert [(r.product_id, r.relevance_score) for r in ranked.rankings] == [("prod_a", 0.4)]
    assert (verdict.status, verdict.reason) == (PolicyStatus.BLOCKED, "scripted block")


async def test_entries_on_one_key_are_chosen_by_their_required_text(origin: str) -> None:
    """Two scenarios scripting the shared platform key at once: each prompt gets its own entry."""
    for nonce, score in (("[scenario-a]", 0.1), ("[scenario-b]", 0.2)):
        rankings = [{"product_id": "prod", "relevance_score": score, "reason": nonce}]
        install_script(_SELLER_KEY, ranking={"output": {"rankings": rankings}, "require_text": nonce})

    answers = {
        nonce: (await _rank(_seller_model(), custom_prompt=f"{_PROMPT} {nonce}")).rankings[0].relevance_score
        for nonce in ("[scenario-a]", "[scenario-b]")
    }

    assert answers == {"[scenario-a]": 0.1, "[scenario-b]": 0.2}


async def test_the_latest_matching_entry_answers(origin: str) -> None:
    for score in (0.1, 0.7):
        rankings = [{"product_id": "prod", "relevance_score": score, "reason": "scripted"}]
        install_script(_SELLER_KEY, ranking={"output": {"rankings": rankings}, "require_text": "[scenario-a]"})

    result = await _rank(_seller_model(), custom_prompt=f"{_PROMPT} [scenario-a]")

    assert [r.relevance_score for r in result.rankings] == [0.7]


async def test_an_entry_for_another_scenario_answers_nothing(origin: str) -> None:
    """The leak the nonce closes: another scenario's entry on the shared key must not answer this call."""
    install_script(_SELLER_KEY, ranking={"output": {"rankings": []}, "require_text": "[scenario-a]"})

    with pytest.raises(AIProviderError, match="does not contain the scripted text"):
        await _rank(_seller_model(), custom_prompt=f"{_PROMPT} [scenario-b]")


def _naming_request():
    from decimal import Decimal

    from src.adapters.base import AdapterCreateRequest

    return AdapterCreateRequest(brand={"domain": "naming.example.com"}, total_budget=Decimal("1000.00"))


_NAMING_CONFIGS = pytest.mark.parametrize(
    "config",
    [
        TenantAIConfig(provider="gemini", model="gemini-2.0-flash", api_key=_SELLER_KEY),
        TenantAIConfig(provider="anthropic", model="claude-scripted", api_key=_SELLER_KEY),
    ],
    ids=["gemini", "anthropic"],
)


def _three_names(config: TenantAIConfig) -> list[str]:
    from datetime import UTC, datetime

    from src.core.utils.naming import generate_auto_name

    install_script(_SELLER_KEY, naming={"output": {"name": "Scripted Order Name"}})
    start, end = datetime(2026, 12, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)
    return [generate_auto_name(_naming_request(), [], start, end, tenant_ai_config=config) for _ in range(3)]


@_NAMING_CONFIGS
async def test_order_naming_runs_from_a_running_loop_more_than_once(origin: str, config: TenantAIConfig) -> None:
    """The write path calls the naming model through run_async_in_sync_context, on a fresh loop each time.

    From a running loop the bridge runs the coroutine on a new loop in a worker thread. An
    exception that is not a provider failure (a client bound to a closed loop raises
    RuntimeError) is not caught any more, so it would fail create_media_buy; the second and
    third calls are where such a client would surface.
    """
    assert _three_names(config) == ["Scripted Order Name"] * 3


@_NAMING_CONFIGS
def test_order_naming_runs_from_sync_code_more_than_once(origin: str, config: TenantAIConfig) -> None:
    """The same from sync code (the mock adapter's own path), where the bridge runs the loop itself."""
    assert _three_names(config) == ["Scripted Order Name"] * 3


async def test_a_naming_provider_failure_falls_back_to_the_brand_name(origin: str) -> None:
    from datetime import UTC, datetime

    from src.core.utils.naming import generate_auto_name

    install_script(_SELLER_KEY, naming={"fail_status": 503})
    config = TenantAIConfig(provider="gemini", model="gemini-2.0-flash", api_key=_SELLER_KEY)

    name = generate_auto_name(
        _naming_request(),
        [],
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2026, 12, 31, tzinfo=UTC),
        tenant_ai_config=config,
    )

    assert name == "naming.example.com"
