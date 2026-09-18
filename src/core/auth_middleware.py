"""The one renderer of a refused credential as HTTP: ``AuthChallengeResponder``.

It reads nothing off the request and resolves nothing. Each transport hands its request
headers to the boundary, whose resolver is the one reader of a credential; this module
lifts the AdCP auth code out of a FINISHED JSON body onto the status line and attaches the
``WWW-Authenticate`` challenge beside it.

A pure ASGI class, not ``BaseHTTPMiddleware``, which loses ContextVar writes across the
request (Starlette issue #1729).
"""

from __future__ import annotations

import json
import logging
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.errors.signature_codes import SignatureErrorCode, challenge_for

logger = logging.getLogger(__name__)

#: The wire strings of the request-signature taxonomy, as a set for the membership test below.
_SIGNATURE_CODE_VALUES: Final[frozenset[str]] = frozenset(code.value for code in SignatureErrorCode)

#: The challenge a 401 carries, per code.
#:
#: RFC 6750 §3: a bearer challenge names the scheme and, when a credential WAS presented and
#: rejected, the ``invalid_token`` error code. Both credentials this seller accepts are
#: carried in ``Authorization: Bearer`` -- the only header this seller reads a credential
#: from -- so ``Bearer`` is the scheme to name. No ``realm``: AdCP defines none for this,
#: and RFC 7235
#: permits a challenge carrying the scheme alone.
#:
#: The ``Signature`` family from L1/security.mdx's "Transport error taxonomy" is the OTHER
#: scheme this function answers, and it is not in this table: it has 27 codes, the challenge
#: is a mechanical function of the code (``Signature error="<code>"``), and both the codes and
#: the function are generated from the SDK's own taxonomy in
#: :mod:`src.core.errors.signature_codes`. A 27-row copy here would be a second source for a
#: string the pinned compliance vectors grade BYTE-FOR-BYTE.
_CHALLENGE_BY_CODE: Final[dict[str, str]] = {
    "AUTH_MISSING": "Bearer",
    "AUTH_INVALID": 'Bearer error="invalid_token"',
}


def _challenge_for_code(code: str | None) -> str | None:
    """The ``WWW-Authenticate`` value for *code*, or None if it is not an auth refusal.

    PRIVATE, and that is the point. ``_flush`` is the only caller: asking this question has
    no purpose except writing the header, so a second caller would be a second renderer.
    Three transports each answered it once and disagreed, and the module-private name is
    what keeps a fourth from starting -- there is nothing importable to build one from.

    TWO SCHEMES, one renderer. A bearer refusal answers ``Bearer`` per RFC 6750 §3; a request
    -signature refusal answers ``Signature error="<code>"`` per security.mdx @ v3.1.1
    § "``WWW-Authenticate`` format", which additionally requires no ``realm`` and no other
    parameters. They are different mechanisms with different codes, and the reason ONE
    function answers both is that both now arrive here the same way: as an AdCP code on a
    finished body, because #1291's verifier raises its refusal inside the resolver instead of
    sending its own bodyless 401 from an ASGI middleware. That is what makes the SPECIFIC
    signature code — not a generic AUTH_INVALID — the thing this reads.
    """
    if code in _SIGNATURE_CODE_VALUES:
        # ``code`` is the wire string off a finished body; the membership test against the
        # generated vocabulary is what keeps an arbitrary body from minting a challenge.
        return challenge_for(code or "")
    return _CHALLENGE_BY_CODE.get(code or "")


def _envelope_in_body(body: dict) -> dict | None:
    """Shape 1: the envelope is the whole body -- REST."""
    envelope = body.get("adcp_error")
    return envelope if isinstance(envelope, dict) else None


def _envelope_in_jsonrpc_error(body: dict) -> dict | None:
    """Shape 2: nested under a JSON-RPC error's ``data``.

    ``error`` is not always an object -- a bare JSON-RPC failure can carry a STRING there, and
    the obvious ``(body.get("error") or {}).get("data")`` blows up on it, because a non-empty
    string is truthy so the ``or {}`` never fires.
    """
    error = body.get("error")
    data = error.get("data") if isinstance(error, dict) else None
    envelope = data.get("adcp_error") if isinstance(data, dict) else None
    return envelope if isinstance(envelope, dict) else None


def _envelope_in_mcp_result(body: dict) -> dict | None:
    """Shape 3: inside an MCP tool RESULT.

    MCP does not report a tool failure as a JSON-RPC error: it answers ``result.content[].text``
    with ``isError`` set, and that text is the envelope re-encoded as a JSON STRING. So the code
    is two decodes deep, which is why a reader that knew only shapes 1 and 2 found nothing here.
    """
    result = body.get("result")
    content = result.get("content") if isinstance(result, dict) else None
    for part in content or []:
        text = part.get("text") if isinstance(part, dict) else None
        if not isinstance(text, str):
            continue
        try:
            inner = json.loads(text)
        except (ValueError, TypeError):
            continue
        candidate = inner.get("adcp_error") if isinstance(inner, dict) else None
        if isinstance(candidate, dict):
            return candidate
    return None


def _envelope_in_a2a_task(body: dict) -> dict | None:
    """Shape 4: inside a FAILED A2A Task's artifact.

    A2A reports a tool failure the way it reports a success -- a Task whose artifact DataPart
    is the response body, with the Task state set to FAILED -- so the envelope sits at
    ``result.task.artifacts[].parts[].data`` (native 1.0 wraps the Task under ``result.task``;
    the unwrapped shape puts ``artifacts`` directly under ``result``). A refusal is an outcome
    like any other, so this reader looks where every outcome lives.
    """
    result = body.get("result")
    task = result.get("task") if isinstance(result, dict) else None
    holder = task if isinstance(task, dict) else result
    artifacts = holder.get("artifacts") if isinstance(holder, dict) else None
    for artifact in artifacts or []:
        parts = artifact.get("parts") if isinstance(artifact, dict) else None
        for part in parts or []:
            data = part.get("data") if isinstance(part, dict) else None
            candidate = data.get("adcp_error") if isinstance(data, dict) else None
            if isinstance(candidate, dict):
                return candidate
    return None


def adcp_error_code_in(body: object) -> str | None:
    """The AdCP error code inside a two-layer envelope, or None if there isn't one.

    Finds it whether the envelope is the whole body (REST), nested under a JSON-RPC
    ``error.data`` (A2A's transport-level errors), re-encoded inside an MCP tool result, or
    carried by a failed A2A Task's artifact. One reader per container, tried in order, so a
    new container is one more reader rather than another branch in a growing function.
    """
    if not isinstance(body, dict):
        return None
    for read in (_envelope_in_body, _envelope_in_jsonrpc_error, _envelope_in_mcp_result, _envelope_in_a2a_task):
        envelope = read(body)
        if envelope is not None:
            code = envelope.get("code")
            return code if isinstance(code, str) else None
    return None


def _is_json_response(message: Message) -> bool:
    """Whether an ``http.response.start`` announces a JSON body.

    The gate on buffering. An AdCP envelope is always JSON, so a non-JSON response can never
    carry a code worth lifting -- and MUST be streamed through untouched, because the admin
    UI serves an ``text/event-stream`` activity feed that buffering would hold open forever.
    """
    for name, value in message.get("headers", []):
        if name.lower() == b"content-type":
            return b"application/json" in value.lower()
    return False


class AuthChallengeResponder:
    """Lift a refused credential out of a JSON body and onto the HTTP status.

    THE renderer. Every transport's 401 is written here and nowhere else, because when each
    wrote its own they disagreed: MCP was wrapped in this, A2A hand-rolled the identical lift
    inside its integer-restoration decorator, and REST set the header in its exception
    handler. Three copies of "read the AdCP code, set 401, attach the challenge" is three
    places to edit and two places to forget.

    It is mounted app-wide rather than around one transport, so a NEW transport gets the
    same 401 for free and cannot render its own.

    It buffers, and it must: the ASGI ``http.response.start`` message carries the status and
    arrives BEFORE the body, so the status has to be held until the body has been seen. That
    is only sound for a finite response, which is why it buffers JSON alone (see
    ``_is_json_response``) and why the MCP app is built with ``json_response=True``. Under
    SSE this cannot work at all, and pretending otherwise is the mistake an earlier attempt
    made.

    It knows nothing about tools, and that is the point. Which tool was called, and whether
    that tool requires a caller, is decided where the name is actually available -- the
    boundary, which reads ``ToolSpec.requires_credential()`` and hands it to the resolver.
    A middleware cannot know it (the name is in the body) and must not guess it: a version
    of this that parsed the JSON-RPC body to find out was re-implementing the transport's
    own parsing and reading the registry a second time, which is the drift
    building-tools.md says the single declaration exists to prevent. This reads a CODE off
    a finished response; nothing more.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        chunks: list[bytes] = []
        buffering = True

        async def buffer(message: Message) -> None:
            nonlocal start, buffering
            if message["type"] == "http.response.start":
                if _is_json_response(message):
                    start = message  # hold it: the body may change the status
                else:
                    buffering = False
                    await send(message)
                return
            if message["type"] != "http.response.body" or not buffering:
                await send(message)
                return
            chunks.append(message.get("body", b"") or b"")
            if message.get("more_body", False):
                return
            await _flush(send, start, b"".join(chunks))

        await self.app(scope, receive, buffer)


async def _flush(send: Send, start: Message | None, body: bytes) -> None:
    """Emit the held response, upgrading it to 401 when it carries an auth refusal.

    The inbound ``WWW-Authenticate`` is stripped UNCONDITIONALLY, then re-derived from the
    body. Not a tidy-up: it is what makes a second renderer pointless rather than merely
    discouraged. Anything further in that writes its own challenge has it overwritten here,
    so the only way to change what a buyer receives is to change this function -- which is
    the property an architecture guard would otherwise have to police by inspection.

    Safe because this is reached only for ``application/json`` (see ``_is_json_response``):
    the admin UI's HTML and its ``text/event-stream`` feed never pass through here, so no
    other authentication scheme's challenge can be caught by it.
    """
    if start is None:
        return
    try:
        code = adcp_error_code_in(json.loads(body)) if body else None
    except (ValueError, TypeError):
        code = None
    challenge = _challenge_for_code(code)
    start = dict(start)
    headers = [h for h in start.get("headers", []) if h[0].lower() != b"www-authenticate"]
    if challenge:
        start["status"] = 401
        headers.append((b"www-authenticate", challenge.encode("latin-1")))
    start["headers"] = headers
    await send(start)
    await send({"type": "http.response.body", "body": body})
