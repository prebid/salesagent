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
