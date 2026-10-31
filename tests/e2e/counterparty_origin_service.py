"""Static counterparty identity origin for the e2e signing suite (salesagent-mp53.8).

The live server's INBOUND verifier resolves an unknown counterparty by WALKING
its published trust root — ``adcp.signing.agent_resolver.async_resolve_agent``:

    MCP get_adcp_capabilities at <agent_url>
                                        -> capabilities, whose
                                           ``identity.brand_json_url`` names ...
    GET <brand_json_url>                -> brand.json, whose ``agents[]`` entry
                                           for that url carries ``jwks_uri`` ...
    GET <jwks_uri>                      -> the JWKS the signature is verified against

This service is the other end of that walk.

HOP 1 IS A TOOL CALL, NOT A FETCH, AND THAT CHANGED UNDER THIS FIXTURE. Up to adcp
6.6.0 the SDK's ``_fetch_capabilities`` was a raw ``GET <agent_url>`` — its own
docstring said "SSRF-pinned ``GET``" — and this origin was built to answer exactly
that. The version of this docstring written then recorded that the raw GET was "an
SDK divergence from ``security.mdx``:1142, which says to invoke
``get_adcp_capabilities`` over the declared transport". adcp 9 CLOSED that
divergence: ``adcp/signing/_resolver_protocol.py`` opens an MCP session and calls the
tool. So the fixture outlived the behaviour it was shaped around, hop 1 began failing,
and every signing scenario reported ``request_signature_capabilities_unreachable`` —
which is the code AdCP 3.2.1 assigns to a failed hop 1, correctly emitted against an
origin that could not complete one (``salesagent-8qela.167``).

It still exists for the reason it always did, which the above does not retire: the
SELLER'S OWN published trust root cannot be reused as the counterparty, because the
pair below must differ in exactly one bit and the seller publishes only itself.

It is a SIBLING of ``tests/e2e/webhook_capture_service.py``, not a mode inside it.
What would be shared is ~30 lines of ``BaseHTTPRequestHandler`` — not the
duplication class worth collapsing — while the coupling would be real: that
service's other mode records raw bytes for the signing suite and its ``do_POST``
semantics must not bleed into an identity origin.

TWO agents on ONE origin, differing ONLY in which brand.json their capabilities
document points at:

* ``/agent/listed``   -> ``/.well-known/brand.json``          (lists it   -> Tier 3 PASSES)
* ``/agent/unlisted`` -> ``/.well-known/brand-unlisted.json`` (omits it   -> Tier 3 REFUSES)

That pair is what makes "resolvable via the PUBLISHED TRUST ROOT" a wire fact
rather than an inference: without the unlisted sibling, a regression that skipped
Tier 3 or routed to the config registry would produce a byte-identical 2xx AND an
identical metric increment. ``AGENT_RESOLUTION_CACHE`` is keyed on agent_url, so
the two cannot collide in the server's cache.

The JWKS is INSTALLED at runtime over ``PUT /_control/jwks`` rather than baked in,
so keys are minted fresh per run by ``keypair_for()`` and no private material is
committed. That control plane is fixture setup — scenery, like a seeded tenant
row — not a seam in anything under test.

Stdlib-only, and deliberately so: it runs on a bare ``python:3.12-slim`` compose
service with no project dependencies. Importing anything under ``tests.helpers``
would run that package's ``__init__``, which transitively pulls in ``factory-boy``.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Iterator

from tests.e2e._stdlib_json_http import JsonRequestHandler, compose_project_name, serve_forever_in_thread

#: The public origin the SERVER walks. Read from the environment so the compose
#: service, the nginx SNI map and this module cannot drift apart silently: every
#: absolute URL this origin publishes about ITSELF must be the name the server
#: dials, not the container-internal address it happens to bind.
PUBLIC_ORIGIN = os.environ.get("COUNTERPARTY_PUBLIC_ORIGIN", "https://counterparty.adcp-e2e.dev:8443")

LISTED_AGENT_PATH = "/agent/listed"
UNLISTED_AGENT_PATH = "/agent/unlisted"
BRAND_JSON_PATH = "/.well-known/brand.json"
BRAND_UNLISTED_PATH = "/.well-known/brand-unlisted.json"
JWKS_PATH = "/.well-known/jwks.json"
JWKS_CONTROL_PATH = "/_control/jwks"
HEALTH_PATH = "/health"

#: The one tool this origin implements, and the one AdCP 3.2.1 hop 1 names.
CAPABILITIES_TOOL = "get_adcp_capabilities"

#: MCP assigns a session through a RESPONSE header on ``initialize``; the client reads
#: it back and uses it to address the best-effort ``DELETE`` that ends discovery. A
#: constant rather than a generated id: this origin holds no per-session state, so a
#: unique value would only be something to look up and find nothing behind.
MCP_SESSION_HEADER = "Mcp-Session-Id"
MCP_SESSION_ID = "counterparty-origin"

#: SLOTS — the same listed-agent shape, once per caller instead of once per origin.
#:
#: The two agents above are FIXED documents shared by every consumer, which is
#: right for the accepted/unlisted PAIR (their whole point is that they differ in
#: one bit) and wrong for anyone who needs a key of their own. The server caches a
#: resolved ``AgentResolution`` — JWKS included — per ``agent_url`` for
#: ``SigningSettings.agent_resolution_ttl_seconds`` (an hour by default), in
#: ``src/core/signing/verifier.py``'s ``_resolution_for``, so a SECOND consumer of
#: ``/agent/listed`` does not merely race the first: whichever walks first pins the
#: keyset, and every key published afterwards at that url is invisible to the
#: verifier for the rest of the TTL. A slot gives each caller its own agent url,
#: hence its own cache entry, hence no interference — across xdist workers, across
#: suites, and with the fixed pair above.
#:
#: A slot is an AGENT, never a KEY LOCATION. The server reads a counterparty's keys
#: from ``<agent origin>/.well-known/jwks.json`` and nowhere else
#: (``docs/design/request-signing-subset.md``), and a well-known path is one per
#: ORIGIN — so every slot's brand.json points at that one document and the keysets
#: installed at each slot are served there together, selected by ``kid`` exactly as
#: the SDK selects within any JWKS. What the slot still isolates is what it was for:
#: the agent url, and therefore the cache entry.
SLOT_AGENT_PREFIX = "/agent/slot/"
SLOT_BRAND_PREFIX = "/.well-known/brand-slot/"
SLOT_CONTROL_PREFIX = "/_control/jwks/"


def slot_agent_url(slot: str) -> str:
    """The absolute ``agent_url`` a caller records on its Principal row."""
    return f"{PUBLIC_ORIGIN}{SLOT_AGENT_PREFIX}{slot}"


def slot_control_path(slot: str) -> str:
    """Where a caller PUTs the keyset this origin serves for *slot*."""
    return f"{SLOT_CONTROL_PREFIX}{slot}"


class _JwksStore:
    """The keysets this origin publishes, installed at runtime and read concurrently.

    Keyed by SLOT for BOOKKEEPING — ``has(slot)`` is how an agent document answers 404
    for a slot nobody installed — while :meth:`published` is the single document every
    walk fetches: the union of every installed keyset, because this origin serves one
    JWKS at one well-known path and callers are told apart by ``kid``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jwks: dict[str, dict] = {"": {"keys": []}}

    def install(self, jwks: dict, slot: str = "") -> None:
        with self._lock:
            self._jwks[slot] = jwks

    def has(self, slot: str) -> bool:
        with self._lock:
            return slot in self._jwks

    def published(self) -> dict:
        """Every installed key, in one keyset.

        A caller's key is visible to any walk that happens after it installed, which is
        the same guarantee the per-slot documents gave: the server's cache pins whatever
        this answers at the moment that agent url is first walked.
        """
        with self._lock:
            return {"keys": [key for jwks in self._jwks.values() for key in jwks.get("keys", [])]}


def _capabilities(brand_json_url: str) -> dict:
    """The capabilities document for one agent.

    ``identity.brand_json_url`` is what hop 1 exists to deliver
    (``_extract_brand_json_url``). Key discovery goes through brand.json — never
    through an ``adagents.json``, which is a different mechanism the resolver
    does not consult here.

    ``identity.key_origins`` is published because a conformant counterparty may, and
    keeping it here is what makes the server's indifference to it observable: it reads
    keys from ``<agent origin>/.well-known/jwks.json`` and consults no map
    (``docs/design/request-signing-subset.md``). A run that started depending on this
    field again would keep passing here and fail against a counterparty that omits it,
    which is the regression the presence of an IGNORED value catches.
    """
    return {
        "adcp_version": "3.1.1",
        "identity": {
            "brand_json_url": brand_json_url,
            "key_origins": {"request_signing": PUBLIC_ORIGIN},
        },
    }


def _brand_json(listed_agent_url: str | None) -> dict:
    """A brand.json listing zero or one agent.

    ``jwks_uri`` on the agent entry is what hop 3 fetches, and it is the well-known
    path at this origin for every agent here — the server reads keys from nowhere
    else. When *listed_agent_url* is ``None`` the document is well-formed and simply
    does not list the caller — which is the point of the unlisted sibling: the refusal
    must come from the AUTHORIZATION decision, not from a malformed or missing
    document (those would fail earlier, in a different hop, and prove something
    else).
    """
    agents = []
    if listed_agent_url is not None:
        agents.append({"url": listed_agent_url, "jwks_uri": f"{PUBLIC_ORIGIN}{JWKS_PATH}", "type": "buying"})
    return {"agents": agents}


class _CounterpartyHandler(JsonRequestHandler):
    """Answer hop 1 over MCP and hops 2-3 as plain GETs, plus the JWKS control plane."""

    def _wants_event_stream(self) -> bool:
        """Whether the request is asking for an SSE stream rather than a document."""
        return "text/event-stream" in (self.headers.get("Accept") or "")

    def _agent_capabilities(self, store: _JwksStore, path: str) -> tuple[dict | None, dict | None]:
        """``(document, refusal)`` for an agent path, ``(None, None)`` when *path* is not one.

        ONE mapping from agent path to capabilities document, because hop 1 is now
        answered on two methods — ``POST`` for the MCP tool call the SDK makes, and
        ``GET`` for the raw fetch kept beside it — and a second copy of
        "which brand.json does this agent point at" is the drift this repo treats as a
        defect.

        The slot refusal travels with it for the same reason ``_slot_document`` gives:
        an uninstalled slot answering a well-formed document lets the walk SUCCEED and
        fail later as an unresolvable keyid, blaming the verifier for a caller that
        installed nothing. Refusing at hop 1 names the slot, on either method.
        """
        if path == LISTED_AGENT_PATH:
            return _capabilities(f"{PUBLIC_ORIGIN}{BRAND_JSON_PATH}"), None
        if path == UNLISTED_AGENT_PATH:
            return _capabilities(f"{PUBLIC_ORIGIN}{BRAND_UNLISTED_PATH}"), None
        if path.startswith(SLOT_AGENT_PREFIX):
            slot = path[len(SLOT_AGENT_PREFIX) :]
            if not store.has(slot):
                return None, {"error": f"no keyset installed for slot {slot!r}"}
            return _capabilities(f"{PUBLIC_ORIGIN}{SLOT_BRAND_PREFIX}{slot}.json"), None
        return None, None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        """Answer hop 1 as the MCP tool call AdCP 3.2.1 specifies.

        ``L1/security.mdx`` § "Discovering an agent's signing keys via
        ``brand_json_url``" step 1 is a ``get_adcp_capabilities`` fetch, and adcp 9
        makes it over the agent's declared transport
        (``adcp/signing/_resolver_protocol.py``) rather than the raw ``GET`` adcp 6.6.0
        used. This origin answered only the raw GET, so hop 1 died and every signing
        scenario reported ``request_signature_capabilities_unreachable`` — the pin's
        correct code for a failed hop 1, against a fixture that could not complete it
        (``salesagent-8qela.167``).

        MCP over plain ``application/json``, not SSE: the client accepts either for a
        POST reply (``mcp/client/streamable_http.py`` branches on the response
        content-type) and JSON is what this stdlib origin can frame with the
        ``Content-Length`` the rest of the module already relies on.
        """
        store: _JwksStore = self.server.jwks_store  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        document, refusal = self._agent_capabilities(store, path)
        if document is None:
            self._write_json(404, refusal or {"error": f"no MCP endpoint at {self.path!r}"})
            return

        raw = self._read_raw_body()
        try:
            message = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"MCP body is not valid JSON: {exc}"})
            return
        self._answer_mcp(message, document)

    def _answer_mcp(self, message: dict, document: dict) -> None:
        """The three JSON-RPC exchanges the SDK's discovery client makes, and nothing else.

        Deliberately not a general MCP server: ``initialize``, the
        ``notifications/initialized`` that follows it, and one ``tools/call``. Anything
        else answers METHOD_NOT_FOUND, which is the honest reply from an origin that
        publishes identity documents and implements no other tool.
        """
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if request_id is None:
            # A notification. The client sends `notifications/initialized` and, per the
            # MCP spec, MUST NOT be answered with a response — it does not even read
            # this body (streamable_http.py gates the read on JSONRPCRequest).
            self._write_json(202, {})
            return

        if method == "initialize":
            # ECHO the client's requested version rather than naming one. The client
            # asks with a value from its own accepted handshake list and then validates
            # the reply against that same list, so echoing cannot disagree with it —
            # whereas a literal here would be a second copy of `mcp`'s version tuple,
            # in a module that cannot import `mcp` to check it.
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "counterparty-origin", "version": "1"},
                    },
                },
                extra_headers={MCP_SESSION_HEADER: MCP_SESSION_ID},
            )
            return

        if method == "tools/call" and params.get("name") == CAPABILITIES_TOOL:
            self._write_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(document)}],
                        "isError": False,
                        "resultType": "complete",
                    },
                },
            )
            return

        self._write_json(
            200,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method!r}"},
            },
        )

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler name
        """Terminate a discovery session.

        The SDK sends this best-effort after a SUCCESSFUL capabilities call
        (``_resolver_protocol.py``) and ignores any HTTP error, so this exists to keep
        the exchange the real client makes from ending on a 501 — the session is a
        single constant here and there is nothing to release.
        """
        self._write_json(200, {"terminated": True})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        store: _JwksStore = self.server.jwks_store  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        agent_document, agent_refusal = self._agent_capabilities(store, path)
        if (agent_document is not None or agent_refusal is not None) and self._wants_event_stream():
            # The streamable-HTTP client opens a SECOND, server->client SSE stream
            # with a GET at this same url (``mcp/client/streamable_http.py``:639
            # ``start_get_stream`` -> :224 ``handle_get_stream``). Nothing here
            # pushes server-initiated messages, so that stream is declined — and 405
            # is the one status ``DiscoveryTransport`` passes through on a GET
            # instead of turning into ``capabilities_unreachable``
            # (``_resolver_protocol.py``:120), which is the SDK saying this is the
            # expected answer from a server without one.
            #
            # Keyed on ``Accept`` and NOT on the method, because the same GET at the
            # same url is how ``tests/harness/signing_capability.py`` reads this
            # document back, and that read must keep getting its 200. TODAY'S WALK
            # DOES NOT REACH THIS ARM -- ``mcp_capabilities_post`` issues no
            # ``Mcp-Session-Id`` and ``handle_get_stream`` returns at :232 without
            # one -- but a request that asked only for an event stream must not be
            # answered with a JSON document, and this is the answer that stays
            # correct if a session id is ever issued.
            #
            # PRESERVED ACROSS THE ASSEMBLY MERGE (salesagent-8qela.173). Both
            # integration lines built an MCP face for this origin independently;
            # this branch existed only on the sdk9 line and the incoming version
            # has no Accept-keyed arm and no 405 anywhere, so its agent-path GET
            # would answer an SSE request with a JSON document. Ordered BEFORE the
            # agent-document branch below, which is what keeps every non-SSE GET
            # answering exactly as the incoming version does.
            self.send_response(405)
            self.end_headers()
            return
        if agent_document is not None or agent_refusal is not None:
            # Hop 1 as a raw GET, kept beside the MCP face. adcp 9 no longer fetches it
            # this way, but a capabilities document is a legitimate thing for this origin
            # to publish, the slot refusal is identical on both methods, and keeping it
            # costs one branch. Nothing in the suite reads it today.
            self._write_json(200 if agent_document is not None else 404, agent_document or agent_refusal or {})
            return

        if path == HEALTH_PATH:
            self._write_json(200, {"compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]
        elif path == BRAND_JSON_PATH:
            self._write_json(200, _brand_json(f"{PUBLIC_ORIGIN}{LISTED_AGENT_PATH}"))
        elif path == BRAND_UNLISTED_PATH:
            self._write_json(200, _brand_json(None))
        elif path == JWKS_PATH:
            self._write_json(200, store.published())
        elif path.startswith(SLOT_BRAND_PREFIX) and path.endswith(".json"):
            slot = path[len(SLOT_BRAND_PREFIX) : -len(".json")]
            self._slot_document(store, slot, _brand_json(slot_agent_url(slot)))
        else:
            self._write_json(404, {"error": f"no counterparty document at {self.path!r}"})

    def _slot_document(self, store: _JwksStore, slot: str, document: dict) -> None:
        """Answer one of a slot's two documents, or 404 if nothing is installed for it.

        The 404 is where the per-slot JWKS used to answer it, and it is why the check is
        worth keeping: an unknown slot answering a well-formed document would let the walk
        SUCCEED and the signature then fail as an unresolvable keyid — blaming the verifier
        for a caller that never installed anything. Refusing at hop 1 (and hop 2) names the
        slot instead.
        """
        if not store.has(slot):
            self._write_json(404, {"error": f"no keyset installed for slot {slot!r}"})
            return
        self._write_json(200, document)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler name
        path = self.path.split("?", 1)[0].rstrip("/")
        slot = ""
        if path.startswith(SLOT_CONTROL_PREFIX):
            slot = path[len(SLOT_CONTROL_PREFIX) :]
        elif path != JWKS_CONTROL_PATH:
            self._write_json(404, {"error": f"no control endpoint at {self.path!r}"})
            return
        raw = self._read_raw_body()
        try:
            jwks = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            # Answering 400 rather than a quiet 200: a test that installed a
            # malformed keyset and got a 200 would fail three assertions later as
            # an unresolvable key, naming the verifier for a fixture's mistake.
            self._write_json(400, {"error": f"JWKS body is not valid JSON: {exc}"})
            return
        store: _JwksStore = self.server.jwks_store  # type: ignore[attr-defined]
        store.install(jwks, slot)
        self._write_json(200, {"installed": len(jwks.get("keys", [])), "slot": slot})


@contextlib.contextmanager
def run_counterparty_origin(*, host: str = "0.0.0.0", port: int = 8080) -> Iterator[str]:
    """Run the counterparty origin, yielding its base URL (``http://host:port``).

    Plaintext by design: TLS is terminated by the shared ``tls-proxy`` front,
    which routes ``counterparty.adcp-e2e.dev`` here by SNI. The origin never holds a
    certificate — one terminator, one leaf, one place to keep in step.
    """
    server = serve_forever_in_thread(
        _CounterpartyHandler,
        host=host,
        port=port,
        server_attrs={"jwks_store": _JwksStore(), "compose_project_name": compose_project_name()},
    )
    try:
        yield f"http://{host}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    """Entry point for ``python -m tests.e2e.counterparty_origin_service``."""
    port = int(os.environ.get("PORT", "8080"))
    with run_counterparty_origin(host="0.0.0.0", port=port):
        threading.Event().wait()


if __name__ == "__main__":
    main()
