"""Test-side client for the compose ``scripted-llm`` service (``scripted_llm_origin_service``).

The live e2e server calls the scripted origin for its LLM answers (``GOOGLE_GEMINI_BASE_URL``
in ``docker-compose.e2e.yml``); the test process programs those answers here, over the
origin's plain-HTTP control plane, by the API key the seller's configuration carries.

In-network the origin is the compose service name. ``SCRIPTED_LLM_ORIGIN`` overrides it for a
runner outside the compose network that reaches the service on a published port.
"""

from __future__ import annotations

import os

import httpx

from tests.e2e.scripted_llm_origin_service import SCRIPT_CONTROL_PREFIX

_CONTROL_TIMEOUT_SECONDS = 10.0

#: The platform's own Gemini key on every e2e server (``GEMINI_API_KEY`` in
#: ``docker-compose.e2e.yml``). Fixed, so a scenario that declares "the platform has an AI
#: credential of its own" can script it like a seller's key; unscripted, the origin refuses it.
E2E_PLATFORM_AI_KEY = "e2e-platform-gemini-key"


def scripted_llm_origin() -> str:
    """Where the TEST reaches the origin's control plane."""
    return os.environ.get("SCRIPTED_LLM_ORIGIN", "http://scripted-llm:8080")


def install_script(api_key: str, **entries: dict) -> None:
    """Script the answers the origin gives a call carrying ``api_key``, one entry per question.

    ``entries`` is ``ranking=`` and/or ``policy=``, each an entry as the service module
    documents it. Raises when the origin does not accept the script: a script that silently
    failed to install would surface as a catalog-order mismatch, blaming production.
    """
    url = f"{scripted_llm_origin()}{SCRIPT_CONTROL_PREFIX}{api_key}"
    try:
        response = httpx.put(url, json=entries, timeout=_CONTROL_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"scripted-llm control PUT {url} failed: {exc!r}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"scripted-llm control PUT {url} answered {response.status_code}: {response.text}")


def ranking_prompt_prefix(seller_prompt: str) -> str:
    """The ranking prompt up to the buyer's brief, as production builds it for ``seller_prompt``.

    Built by production's own ``build_ranking_prompt`` with the seller's prompt in the seller's
    slot, so a ranking script whose ``require_text`` is this prefix answers only a call that
    put the seller's prompt there. Exchanging ``custom_prompt`` and ``brief`` at the call site
    moves the BRIEF into that slot, and the prefix no longer matches.
    """
    from src.services.ai.agents.ranking_agent import build_ranking_prompt

    brief_marker = "\x00brief\x00"
    return build_ranking_prompt(seller_prompt, brief_marker, []).split(brief_marker)[0]
