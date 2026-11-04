"""Contract: production's own AI agents, pointed at the scripted origin, get the scripted answers.

The e2e stack points the live server's Gemini client at ``scripted-llm``
(``GOOGLE_GEMINI_BASE_URL``) so get_products' ranking and policy check get scripted answers
over e2e. That only works if three things hold, and none of them is visible from a BDD
scenario that fails as "catalog order":

* ``google-genai`` honours ``GOOGLE_GEMINI_BASE_URL`` for the model production builds
  (``AIServiceFactory.create_model`` -> ``GoogleModel(provider=GoogleProvider(api_key=...))``);
* pydantic-ai accepts the origin's response as the agent's structured output, for both
  output models (``ProductRankingResult`` and ``PolicyAnalysis``);
* every refusal the origin answers (an unscripted key, a scripted failure, a prompt that
  does not carry the scripted text) raises a ``RuntimeError``, which is what the ranking
  path in ``products.py`` catches to fall back to catalog order.

So this runs the origin IN-PROCESS on loopback and drives production's real agents and
real ``PolicyCheckService`` against it: no Docker, no compose network. The stack wiring is
``tests/unit/test_architecture_e2e_scripted_llm_wiring.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.services.ai import AIServiceFactory, TenantAIConfig
from src.services.ai.agents.ranking_agent import build_ranking_prompt, create_ranking_agent, rank_products_async
from src.services.policy_check_service import PolicyCheckService, PolicyStatus
from tests.e2e._scripted_llm import install_script
from tests.e2e.scripted_llm_origin_service import run_scripted_llm_origin

_SELLER_KEY = "seller-key-under-test"
_PROMPT = "Prefer video inventory"
_BRIEF = "video campaign for a sports launch"


@pytest.fixture
def origin() -> Iterator[str]:
    """The origin on a loopback port, with the Gemini client and the control client both aimed at it."""
    with run_scripted_llm_origin(host="127.0.0.1", port=0) as base_url:
        with patch.dict(os.environ, {"GOOGLE_GEMINI_BASE_URL": base_url, "SCRIPTED_LLM_ORIGIN": base_url}):
            yield base_url


def _seller_model(api_key: str = _SELLER_KEY):
    """The model production builds for a seller who configured Gemini in the Admin UI."""
    config = TenantAIConfig.coerce({"provider": "gemini", "model": "gemini-2.0-flash", "api_key": api_key})
    return AIServiceFactory().create_model(config)


async def _rank(model, custom_prompt: str = _PROMPT, brief: str = _BRIEF):
    return await rank_products_async(create_ranking_agent(model), custom_prompt=custom_prompt, brief=brief, products=[])


def _prompt_prefix() -> str:
    marker = "\x00brief\x00"
    return build_ranking_prompt(_PROMPT, marker, []).split(marker)[0]


async def test_ranking_agent_returns_the_scripted_scores(origin: str) -> None:
    rankings = [{"product_id": "prod_b_video", "relevance_score": 0.9, "reason": "scripted"}]
    install_script(_SELLER_KEY, ranking={"output": {"rankings": rankings}, "require_text": _prompt_prefix()})

    result = await _rank(_seller_model())

    assert [(r.product_id, r.relevance_score) for r in result.rankings] == [("prod_b_video", 0.9)]


async def test_policy_service_returns_the_scripted_verdict(origin: str) -> None:
    install_script(_SELLER_KEY, policy={"output": {"status": "blocked", "reason": "scripted block"}})
    config = TenantAIConfig.coerce({"provider": "gemini", "model": "gemini-2.0-flash", "api_key": _SELLER_KEY})

    result = await PolicyCheckService(tenant_ai_config=config).check_brief_compliance(brief=_BRIEF)

    assert (result.status, result.reason) == (PolicyStatus.BLOCKED, "scripted block")


async def test_a_call_with_an_unscripted_key_raises_what_ranking_catches(origin: str) -> None:
    install_script(_SELLER_KEY, ranking={"output": {"rankings": []}})

    with pytest.raises(RuntimeError, match="API key not valid"):
        await _rank(_seller_model(api_key="the-platform-key"))


async def test_a_scripted_failure_raises_what_ranking_catches(origin: str) -> None:
    install_script(_SELLER_KEY, ranking={"fail_status": 503})

    with pytest.raises(RuntimeError, match="503"):
        await _rank(_seller_model())


async def test_the_seller_prompt_in_the_brief_slot_is_refused(origin: str) -> None:
    """The swap the in-process stub grades directly: the brief where the seller's prompt goes."""
    install_script(_SELLER_KEY, ranking={"output": {"rankings": []}, "require_text": _prompt_prefix()})

    with pytest.raises(RuntimeError, match="does not contain the scripted text"):
        await _rank(_seller_model(), custom_prompt=_BRIEF, brief=_PROMPT)
