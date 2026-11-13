"""Scripted LLM origin for the e2e stack: the model the live server calls, answered from a script.

get_products asks an LLM two questions on a seller's own AI configuration: how relevant
each product is to the buyer's brief (BR-RULE-005 ranking) and whether the brief passes
the seller's advertising policy (BR-RULE-002). In-process the harness scripts both answers
(``ProductEnv.set_ranking_scores`` / ``set_policy_blocked``). The live e2e server builds a
REAL ``GoogleModel`` and makes a real HTTP call, so its answers have to come from an origin.

The server reaches this origin through settings a real deployment has: ``google-genai``
reads ``GOOGLE_GEMINI_BASE_URL`` and the Anthropic SDK ``ANTHROPIC_BASE_URL`` when no base URL
is passed, and ``GoogleProvider(api_key=...)`` / ``AnthropicProvider(api_key=...)`` pass none.
``docker-compose.e2e.yml`` sets both on the server. Nothing under ``src/`` knows this origin
exists, and no code path branches on it. Two API shapes are answered, from the same scripts:
Gemini's ``generateContent`` and Anthropic's Messages API. The second is there so a model
production must REFUSE to build (an Anthropic row with no model) fails a scenario when it is
built anyway: its call is answered like any other, instead of failing against the real
vendor and letting the consumer fail open.

SCRIPTS ARE KEYED BY THE API KEY THE CALL CARRIES (``x-goog-api-key`` / ``x-api-key``), because the key is
what the seller configured, and so what decides whether the call ran on the SELLER's
configuration at all. A call carrying any other key (the platform's ``GEMINI_API_KEY``, a
key from another scenario) is refused the way the real API refuses an invalid key: HTTP 400,
``INVALID_ARGUMENT``. That refusal is what makes the e2e leg grade the seller's own
configuration without having to remove the platform key from the server: production that
ignored the seller's key would call with the platform's, be refused, and fall back to
catalog order, which the scenario then fails on.

Which question is being asked is read off the request's output tool, the ``final_result``
tool pydantic-ai declares: a ``rankings`` property is the ranking agent's
``ProductRankingResult``, a ``status`` property the policy agent's ``PolicyAnalysis``, a
``name`` property the naming agent's ``OrderName``.

A key holds SEVERAL entries per question, told apart by their ``require_text``: the e2e
servers share one platform key, so scenarios running at once each script it, and an entry
from one scenario must neither overwrite nor answer another's. A call is answered by the
most recently installed entry whose ``require_text`` the prompt contains, so a scenario that
scripts the same question again is answered by its latest script. Each scenario puts its own nonce in
that text (``ProductEnv.scripted_nonce``), so an entry outlives its scenario harmlessly.

A script entry per question is one of:

* ``{"output": {...}}`` -- answer with a ``final_result`` call carrying exactly these
  arguments, which the agent then validates against its own output model;
* ``{"fail_status": 503}`` -- answer with that HTTP status in the API's error shape;
* ``{"drop_connection": true}`` -- close the connection without answering, as an
  unreachable or crashing provider endpoint does.

and may carry ``"require_text": "..."`` -- answer only a call whose user prompt contains that
text; a call no entry matches is refused with 400. The ranking script uses it to grade which
of the seller's prompt and the buyer's brief reached which slot of the prompt; see
``ProductEnv.set_ranking_scores``.

Stdlib-only for the same reason as its siblings (``tests/e2e/_stdlib_json_http.py``): it
runs on a bare ``python:3.12-slim`` compose service with no project dependencies.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import NamedTuple

from tests.e2e._stdlib_json_http import JsonRequestHandler, run_origin, serve_until_killed

#: The Gemini API route ``google-genai`` calls for a non-streaming generation.
GENERATE_CONTENT_RE = re.compile(r"^/v1beta/models/[^/:]+:generateContent$")

#: The Anthropic Messages API route (pydantic-ai calls it through ``client.beta.messages``).
ANTHROPIC_MESSAGES_PATH = "/v1/messages"

#: ``PUT /_control/script/<api key>`` installs or extends that key's script.
SCRIPT_CONTROL_PREFIX = "/_control/script/"

#: The output tool pydantic-ai declares for an agent with a structured ``output_type``.
OUTPUT_TOOL_NAME = "final_result"

#: Which question an output tool asks, by a property only that output model declares.
_QUESTION_BY_PROPERTY = {"rankings": "ranking", "status": "policy", "name": "naming"}


class _ScriptStore:
    """Entries by API key and question. Thread-safe: the server is threaded and xdist workers share it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, list[dict]]] = {}

    def install(self, api_key: str, script: dict) -> dict:
        """Add each question's entry for ``api_key``; the newest matching entry answers.

        Returns the key's entries by question, as the control plane's readback.
        """
        with self._lock:
            questions = self._entries.setdefault(api_key, {})
            for question, entry in script.items():
                questions.setdefault(question, []).append(entry)
            return {question: list(entries) for question, entries in questions.items()}

    def knows(self, api_key: str) -> bool:
        with self._lock:
            return api_key in self._entries

    def match(self, api_key: str, question: str, prompt: str) -> tuple[dict | None, int]:
        """The newest entry for this question whose ``require_text`` ``prompt`` contains, and how many there are."""
        with self._lock:
            entries = list(self._entries.get(api_key, {}).get(question, []))
        for entry in reversed(entries):
            required = entry.get("require_text")
            if required is None or required in prompt:
                return entry, len(entries)
        return None, len(entries)


def _gemini_error(status: int, message: str) -> dict:
    """The Gemini API's error document, so the client raises what the real API makes it raise."""
    reason = {400: "INVALID_ARGUMENT", 404: "NOT_FOUND", 503: "UNAVAILABLE"}.get(status, "INTERNAL")
    return {"error": {"code": status, "message": message, "status": reason}}


def _anthropic_error(status: int, message: str) -> dict:
    """The Anthropic API's error document."""
    kind = {400: "invalid_request_error", 404: "not_found_error", 503: "overloaded_error"}.get(status, "api_error")
    return {"type": "error", "error": {"type": kind, "message": message}}


def _question_by_schema(schema: dict) -> str | None:
    for prop in schema.get("properties") or {}:
        if prop in _QUESTION_BY_PROPERTY:
            return _QUESTION_BY_PROPERTY[prop]
    return None


def _gemini_question(request: dict) -> str | None:
    """``"ranking"`` / ``"policy"`` for the output tool a generateContent request declares, else ``None``."""
    for tool in request.get("tools") or []:
        for declaration in tool.get("functionDeclarations") or tool.get("function_declarations") or []:
            if declaration.get("name") == OUTPUT_TOOL_NAME:
                return _question_by_schema(
                    declaration.get("parameters_json_schema") or declaration.get("parametersJsonSchema") or {}
                )
    return None


def _anthropic_question(request: dict) -> str | None:
    """``"ranking"`` / ``"policy"`` for the output tool a Messages request declares, else ``None``."""
    for tool in request.get("tools") or []:
        if tool.get("name") == OUTPUT_TOOL_NAME:
            return _question_by_schema(tool.get("input_schema") or {})
    return None


def _gemini_user_text(request: dict) -> str:
    """Every text part of the user turns, joined: the prompt the agent sent."""
    return "\n".join(
        part.get("text", "")
        for content in request.get("contents") or []
        if content.get("role") == "user"
        for part in content.get("parts") or []
    )


def _anthropic_user_text(request: dict) -> str:
    """Every text block of the user messages, joined."""
    texts = []
    for message in request.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        texts.extend(block.get("text", "") for block in blocks if block.get("type") == "text")
    return "\n".join(texts)


def _gemini_answer(arguments: dict, request: dict) -> dict:
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


def _anthropic_answer(arguments: dict, request: dict) -> dict:
    """A Messages response whose one block calls the output tool with ``arguments``."""
    return {
        "id": "msg_scripted",
        "type": "message",
        "role": "assistant",
        "model": request.get("model") or "scripted",
        "content": [{"type": "tool_use", "id": "toolu_scripted", "name": OUTPUT_TOOL_NAME, "input": arguments}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class _ApiShape(NamedTuple):
    """How one vendor's API carries the key, the question and the prompt, and how it answers."""

    key_header: str
    question: Callable[[dict], str | None]
    user_text: Callable[[dict], str]
    answer: Callable[[dict, dict], dict]
    error: Callable[[int, str], dict]


_GEMINI = _ApiShape("x-goog-api-key", _gemini_question, _gemini_user_text, _gemini_answer, _gemini_error)
_ANTHROPIC = _ApiShape("x-api-key", _anthropic_question, _anthropic_user_text, _anthropic_answer, _anthropic_error)


def _api_shape(path: str) -> _ApiShape | None:
    """Which vendor API ``path`` is, or ``None`` for a path neither serves."""
    if GENERATE_CONTENT_RE.match(path):
        return _GEMINI
    if path == ANTHROPIC_MESSAGES_PATH:
        return _ANTHROPIC
    return None


class _ScriptedLlmHandler(JsonRequestHandler):
    """Answers ``generateContent`` and ``/v1/messages`` from the store; ``/_control/script`` writes the store."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self._answer_health():
            return
        self._write_json(404, {"error": f"no document at {self.path!r}"})

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler name
        path = self._bare_path
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
            self._write_json(400, {"error": f"a script maps 'ranking'/'policy'/'naming' to an entry, got {script!r}"})
            return
        store: _ScriptStore = self.server.script_store  # type: ignore[attr-defined]
        self._write_json(200, store.install(path[len(SCRIPT_CONTROL_PREFIX) :], script))

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        shape = _api_shape(self._bare_path)
        if shape is None:
            self._write_json(404, _gemini_error(404, f"no model method at {self.path!r}"))
            return
        try:
            request = json.loads(self._read_raw_body() or b"{}")
        except json.JSONDecodeError as exc:
            self._write_json(400, shape.error(400, f"request body is not valid JSON: {exc}"))
            return
        store: _ScriptStore = self.server.script_store  # type: ignore[attr-defined]
        api_key = self.headers.get(shape.key_header) or ""
        if not store.knows(api_key):
            self._write_json(400, shape.error(400, "API key not valid. Please pass a valid API key."))
            return
        question = shape.question(request)
        entry, scripted = store.match(api_key, question, shape.user_text(request)) if question else (None, 0)
        if entry is None:
            message = (
                f"the prompt does not contain the scripted text of any of the {scripted} {question!r} entries on this key"
                if scripted
                else f"nothing is scripted for the {question!r} question on this key"
            )
            self._write_json(400, shape.error(400, message))
            return
        if entry.get("drop_connection"):
            # No status line at all: the client's read ends with the connection.
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if "fail_status" in entry:
            status = int(entry["fail_status"])
            self._write_json(status, shape.error(status, "scripted failure"))
            return
        self._write_json(200, shape.answer(entry["output"], request))


def run_scripted_llm_origin(*, host: str = "0.0.0.0", port: int = 8080) -> AbstractContextManager[str]:
    """Run the origin, yielding its base URL (``http://host:port``).

    Plaintext: the caller is ``google-genai``'s own HTTP client, not this repo's egress
    seam, so the seam's https-only rule does not apply to it, and the server reaches the
    origin by compose service name with no TLS front in between.
    """
    return run_origin(_ScriptedLlmHandler, host=host, port=port, server_attrs={"script_store": _ScriptStore()})


def main() -> None:
    """Entry point for ``python -m tests.e2e.scripted_llm_origin_service``."""
    serve_until_killed(run_scripted_llm_origin)


if __name__ == "__main__":
    main()
