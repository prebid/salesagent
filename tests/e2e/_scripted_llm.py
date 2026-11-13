"""Test-side client for the compose ``scripted-llm`` service (``scripted_llm_origin_service``).

The live e2e server calls the scripted origin for its LLM answers (``GOOGLE_GEMINI_BASE_URL``
in ``docker-compose.e2e.yml``); the test process programs those answers here, over the
origin's plain-HTTP control plane, by the API key the seller's configuration carries.

In-network the origin is the compose service name. ``SCRIPTED_LLM_ORIGIN`` overrides it for a
runner outside the compose network that reaches the service on a published port.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, NamedTuple

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

    ``entries`` is ``ranking=``, ``policy=`` and/or ``naming=``, each an entry as the service module
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


class ScriptedFailure(NamedTuple):
    """One way the seller's model fails, declared once for both legs.

    ``entry`` is what the scripted origin answers over e2e; ``exception`` builds what the
    vendor client raises when the origin answers ``entry``, and :meth:`model` raises it
    in-process.
    """

    entry: dict[str, Any]
    exception: Callable[[], Exception]

    def model(self) -> Any:
        """A pydantic-ai model whose every call raises :attr:`exception`, for the in-process leg.

        Production runs its real agent on it, so ``run_agent`` classifies the failure
        in-process exactly as it does over e2e, where the vendor client raises it.
        """
        from pydantic_ai.models.function import FunctionModel

        def _fail(messages: Any, info: Any) -> Any:
            raise self.exception()

        return FunctionModel(_fail)


def _http_503() -> Exception:
    # What pydantic-ai raises when the provider answers a 5xx.
    from pydantic_ai.exceptions import ModelHTTPError

    return ModelHTTPError(status_code=503, model_name="scripted", body="unavailable")


def _connection_dropped() -> Exception:
    # What the Gemini client raises when the origin closes the connection unanswered
    # (tests/e2e/test_scripted_llm_origin_service.py pins it). No OSError.
    return httpx.RemoteProtocolError("Server disconnected without sending a response.")


#: The provider answers HTTP 503.
MODEL_UNAVAILABLE = ScriptedFailure({"fail_status": 503}, _http_503)

#: The provider's endpoint closes the connection without answering.
CONNECTION_DROPPED = ScriptedFailure({"drop_connection": True}, _connection_dropped)


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
