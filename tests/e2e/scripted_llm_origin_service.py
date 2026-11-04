"""Scripted Gemini origin for the e2e stack: the LLM the live server calls, answered from a script.

get_products asks an LLM two questions on a seller's own AI configuration: how relevant
each product is to the buyer's brief (BR-RULE-005 ranking) and whether the brief passes
the seller's advertising policy (BR-RULE-002). In-process the harness scripts both answers
(``ProductEnv.set_ranking_scores`` / ``set_policy_blocked``). The live e2e server builds a
REAL ``GoogleModel`` and makes a real HTTP call, so its answers have to come from an origin.

The server reaches this origin through a setting a real deployment has: ``google-genai``
reads ``GOOGLE_GEMINI_BASE_URL`` when no base URL is passed, and ``GoogleProvider(api_key=...)``
passes none. ``docker-compose.e2e.yml`` sets it on the server. Nothing under ``src/`` knows
this origin exists, and no code path branches on it.

SCRIPTS ARE KEYED BY THE API KEY THE CALL CARRIES (``x-goog-api-key``), because the key is
what the seller configured, and so what decides whether the call ran on the SELLER's
configuration at all. A call carrying any other key (the platform's ``GEMINI_API_KEY``, a
key from another scenario) is refused the way the real API refuses an invalid key: HTTP 400,
``INVALID_ARGUMENT``. That refusal is what makes the e2e leg grade the seller's own
configuration without having to remove the platform key from the server: production that
ignored the seller's key would call with the platform's, be refused, and fall back to
catalog order, which the scenario then fails on.

Which question is being asked is read off the request's output tool, the ``final_result``
function declaration pydantic-ai sends: a ``rankings`` property is the ranking agent's
``ProductRankingResult``, a ``status`` property the policy agent's ``PolicyAnalysis``.

A script entry per question is one of:

* ``{"output": {...}}`` -- answer with a ``final_result`` call carrying exactly these
  arguments, which the agent then validates against its own output model;
* ``{"fail_status": 503}`` -- answer with that HTTP status in the API's error shape.

and may carry ``"require_text": "..."`` -- answer only when the user prompt contains that
text, else 400. The ranking script uses it to grade which of the seller's prompt and the
buyer's brief reached which slot of the prompt; see ``ProductEnv.set_ranking_scores``.

Stdlib-only for the same reason as its siblings (``tests/e2e/_stdlib_json_http.py``): it
runs on a bare ``python:3.12-slim`` compose service with no project dependencies.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections.abc import Iterator

from tests.e2e._stdlib_json_http import JsonRequestHandler, compose_project_name, serve_forever_in_thread

#: The Gemini API route ``google-genai`` calls for a non-streaming generation.
GENERATE_CONTENT_RE = re.compile(r"^/v1beta/models/[^/:]+:generateContent$")

#: ``PUT /_control/script/<api key>`` installs or extends that key's script.
SCRIPT_CONTROL_PREFIX = "/_control/script/"
HEALTH_PATH = "/health"

#: The output tool pydantic-ai declares for an agent with a structured ``output_type``.
OUTPUT_TOOL_NAME = "final_result"

#: Which question an output tool asks, by a property only that output model declares.
_QUESTION_BY_PROPERTY = {"rankings": "ranking", "status": "policy"}


class _ScriptStore:
    """Scripts by API key. Thread-safe: the server is threaded and xdist workers share it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scripts: dict[str, dict] = {}

    def extend(self, api_key: str, script: dict) -> dict:
        """Set the questions ``script`` names for ``api_key``, keeping the others."""
        with self._lock:
            merged = {**self._scripts.get(api_key, {}), **script}
            self._scripts[api_key] = merged
            return dict(merged)

    def get(self, api_key: str) -> dict | None:
        with self._lock:
            script = self._scripts.get(api_key)
            return dict(script) if script is not None else None


def _api_error(status: int, message: str) -> dict:
    """The Gemini API's error document, so the client raises what the real API makes it raise."""
    reason = {400: "INVALID_ARGUMENT", 404: "NOT_FOUND", 503: "UNAVAILABLE"}.get(status, "INTERNAL")
    return {"error": {"code": status, "message": message, "status": reason}}


def _output_question(request: dict) -> str | None:
    """``"ranking"`` / ``"policy"`` for the output tool the request declares, else ``None``."""
    for tool in request.get("tools") or []:
        for declaration in tool.get("functionDeclarations") or tool.get("function_declarations") or []:
            if declaration.get("name") != OUTPUT_TOOL_NAME:
                continue
            schema = declaration.get("parameters_json_schema") or declaration.get("parametersJsonSchema") or {}
            for prop in schema.get("properties") or {}:
                if prop in _QUESTION_BY_PROPERTY:
                    return _QUESTION_BY_PROPERTY[prop]
    return None


def _user_text(request: dict) -> str:
    """Every text part of the user turns, joined: the prompt the agent sent."""
    return "\n".join(
        part.get("text", "")
        for content in request.get("contents") or []
        if content.get("role") == "user"
        for part in content.get("parts") or []
    )


def _function_call_response(arguments: dict) -> dict:
    """A generateContent response whose one candidate calls the output tool with ``arguments``."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": OUTPUT_TOOL_NAME, "args": arguments}}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }


class _ScriptedLlmHandler(JsonRequestHandler):
    """Answers ``generateContent`` from the store; ``/_control/script`` writes the store."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path.split("?", 1)[0] == HEALTH_PATH:
            self._write_json(200, {"status": "ok", "compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]
            return
        self._write_json(404, {"error": f"no document at {self.path!r}"})

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler name
        path = self.path.split("?", 1)[0]
        if not path.startswith(SCRIPT_CONTROL_PREFIX) or not path[len(SCRIPT_CONTROL_PREFIX) :]:
            self._write_json(404, {"error": f"no control endpoint at {self.path!r}"})
            return
        try:
            script = json.loads(self._read_raw_body() or b"{}")
        except json.JSONDecodeError as exc:
            # 400 rather than a quiet 200: a scenario that installed a malformed script
            # and got a 200 would fail later as "catalog order", blaming production.
            self._write_json(400, {"error": f"script body is not valid JSON: {exc}"})
            return
        if not isinstance(script, dict) or not set(script) <= set(_QUESTION_BY_PROPERTY.values()):
            self._write_json(400, {"error": f"a script maps 'ranking'/'policy' to an entry, got {script!r}"})
            return
        store: _ScriptStore = self.server.script_store  # type: ignore[attr-defined]
        self._write_json(200, store.extend(path[len(SCRIPT_CONTROL_PREFIX) :], script))

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        if not GENERATE_CONTENT_RE.match(self.path.split("?", 1)[0]):
            self._write_json(404, _api_error(404, f"no model method at {self.path!r}"))
            return
        try:
            request = json.loads(self._read_raw_body() or b"{}")
        except json.JSONDecodeError as exc:
            self._write_json(400, _api_error(400, f"request body is not valid JSON: {exc}"))
            return
        store: _ScriptStore = self.server.script_store  # type: ignore[attr-defined]
        script = store.get(self.headers.get("x-goog-api-key") or "")
        if script is None:
            self._write_json(400, _api_error(400, "API key not valid. Please pass a valid API key."))
            return
        question = _output_question(request)
        entry = script.get(question) if question else None
        if entry is None:
            self._write_json(400, _api_error(400, f"nothing is scripted for the {question!r} question on this key"))
            return
        if "fail_status" in entry:
            status = int(entry["fail_status"])
            self._write_json(status, _api_error(status, "scripted failure"))
            return
        required = entry.get("require_text")
        if required is not None and required not in _user_text(request):
            self._write_json(400, _api_error(400, f"the prompt does not contain the scripted text {required!r}"))
            return
        self._write_json(200, _function_call_response(entry["output"]))


@contextlib.contextmanager
def run_scripted_llm_origin(*, host: str = "0.0.0.0", port: int = 8080) -> Iterator[str]:
    """Run the origin, yielding its base URL (``http://host:port``).

    Plaintext: the caller is ``google-genai``'s own HTTP client, not this repo's egress
    seam, so the seam's https-only rule does not apply to it, and the server reaches the
    origin by compose service name with no TLS front in between.
    """
    server = serve_forever_in_thread(
        _ScriptedLlmHandler,
        host=host,
        port=port,
        server_attrs={"script_store": _ScriptStore(), "compose_project_name": compose_project_name()},
    )
    try:
        yield f"http://{host}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    """Entry point for ``python -m tests.e2e.scripted_llm_origin_service``."""
    port = int(os.environ.get("PORT", "8080"))
    with run_scripted_llm_origin(host="0.0.0.0", port=port):
        threading.Event().wait()


if __name__ == "__main__":
    main()
