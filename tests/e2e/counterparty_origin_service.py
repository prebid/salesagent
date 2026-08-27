"""Static counterparty identity origin for the e2e signing suite (salesagent-mp53.8).

The live server's INBOUND verifier resolves an unknown counterparty by WALKING
its published trust root — ``adcp.signing.agent_resolver.async_resolve_agent``:

    GET <agent_url>                     -> capabilities, whose
                                           ``identity.brand_json_url`` names ...
    GET <brand_json_url>                -> brand.json, whose ``agents[]`` entry
                                           for that url carries ``jwks_uri`` ...
    GET <jwks_uri>                      -> the JWKS the signature is verified against

This service is the other end of that walk. It exists because the SELLER'S OWN
published trust root cannot be reused as the counterparty: ``_fetch_capabilities``
does a raw ``GET`` with ``follow_redirects=False`` and demands a 200 carrying a
JSON object, while our ``/mcp`` answers ``GET`` with an SSE redirect (an SDK
divergence from ``security.mdx``:1142, which says to invoke
``get_adcp_capabilities`` over the declared transport). Hop 1 would die on our
own route.

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

#: SLOTS — the same listed-agent shape, once per caller instead of once per origin.
#:
#: The two agents above are FIXED documents shared by every consumer, which is
#: right for the accepted/unlisted PAIR (their whole point is that they differ in
#: one bit) and wrong for anyone who needs a key of their own. The server caches a
#: resolved ``AgentResolution`` — JWKS included — per ``agent_url`` for an hour
#: (``request_verifier_middleware._resolution_for``), so a SECOND consumer of
#: ``/agent/listed`` does not merely race the first: whichever walks first pins the
#: keyset, and every key published afterwards at that url is invisible to the
#: verifier for the rest of the TTL. A slot gives each caller its own agent url,
#: hence its own cache entry, hence no interference — across xdist workers, across
#: suites, and with the fixed pair above.
#:
#: One JWKS store, keyed; one document shape, parametrized. The slot is opaque to
#: this service: whoever PUTs a keyset at ``/_control/jwks/<slot>`` gets an agent
#: at ``/agent/slot/<slot>`` whose published brand.json lists exactly that url.
SLOT_AGENT_PREFIX = "/agent/slot/"
SLOT_BRAND_PREFIX = "/.well-known/brand-slot/"
SLOT_JWKS_PREFIX = "/.well-known/jwks-slot/"
SLOT_CONTROL_PREFIX = "/_control/jwks/"


def slot_agent_url(slot: str) -> str:
    """The absolute ``agent_url`` a caller records on its Principal row."""
    return f"{PUBLIC_ORIGIN}{SLOT_AGENT_PREFIX}{slot}"


def slot_control_path(slot: str) -> str:
    """Where a caller PUTs the keyset this origin serves for *slot*."""
    return f"{SLOT_CONTROL_PREFIX}{slot}"


class _JwksStore:
    """The keysets this origin publishes, installed at runtime and read concurrently.

    Keyed by SLOT, with ``""`` the shared slot the fixed ``/agent/listed`` pair
    serves. One store rather than two so an unknown slot is a MISS (404) rather
    than the shared keyset wearing another caller's clothes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jwks: dict[str, dict] = {"": {"keys": []}}

    def install(self, jwks: dict, slot: str = "") -> None:
        with self._lock:
            self._jwks[slot] = jwks

    def get(self, slot: str = "") -> dict | None:
        with self._lock:
            return self._jwks.get(slot)


def _capabilities(brand_json_url: str) -> dict:
    """The capabilities document for one agent.

    ``identity.brand_json_url`` is what hop 1 exists to deliver
    (``_extract_brand_json_url``). Key discovery goes through brand.json — never
    through an ``adagents.json``, which is a different mechanism the resolver
    does not consult here.

    ``identity.key_origins`` is NOT optional for a counterparty that signs.
    ``_extract_key_origins`` returns ``None`` when the map is absent, which is a
    legitimate posture for a deployment that never signs — but the verifier then
    refuses an actually-signed request with
    ``request_signature_key_origin_missing`` at step 7. Learned in-network: the
    first version of this origin published only ``brand_json_url``, and the
    tampered-signature leg came back with that code instead of
    ``request_signature_invalid``, i.e. the request never reached the signature
    check at all. An origin that omits this is not "slightly incomplete" — it
    cannot be a signing counterparty.

    The value is the scheme+host+port the trust root is served from, keyed by
    PURPOSE (``request_signing``); the verifier pins the resolved JWKS against it
    so a key discovered from one origin cannot be swapped for one from another.
    """
    return {
        "adcp_version": "3.1.1",
        "identity": {
            "brand_json_url": brand_json_url,
            "key_origins": {"request_signing": PUBLIC_ORIGIN},
        },
    }


def _brand_json(listed_agent_url: str | None, jwks_uri: str | None = None) -> dict:
    """A brand.json listing zero or one agent.

    ``jwks_uri`` on the agent entry is what hop 3 fetches. When
    *listed_agent_url* is ``None`` the document is well-formed and simply does
    not list the caller — which is the point of the unlisted sibling: the refusal
    must come from the AUTHORIZATION decision, not from a malformed or missing
    document (those would fail earlier, in a different hop, and prove something
    else).
    """
    agents = []
    if listed_agent_url is not None:
        agents.append(
            {
                "url": listed_agent_url,
                "jwks_uri": jwks_uri or f"{PUBLIC_ORIGIN}{JWKS_PATH}",
                "type": "buying",
            }
        )
    return {"agents": agents}


class _CounterpartyHandler(JsonRequestHandler):
    """Answer the three walk hops as plain GETs, plus the JWKS control plane."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        store: _JwksStore = self.server.jwks_store  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == HEALTH_PATH:
            self._write_json(200, {"compose_project_name": self.server.compose_project_name})  # type: ignore[attr-defined]
        elif path == LISTED_AGENT_PATH:
            self._write_json(200, _capabilities(f"{PUBLIC_ORIGIN}{BRAND_JSON_PATH}"))
        elif path == UNLISTED_AGENT_PATH:
            self._write_json(200, _capabilities(f"{PUBLIC_ORIGIN}{BRAND_UNLISTED_PATH}"))
        elif path == BRAND_JSON_PATH:
            self._write_json(200, _brand_json(f"{PUBLIC_ORIGIN}{LISTED_AGENT_PATH}"))
        elif path == BRAND_UNLISTED_PATH:
            self._write_json(200, _brand_json(None))
        elif path == JWKS_PATH:
            self._write_json(200, store.get() or {"keys": []})
        elif path.startswith(SLOT_AGENT_PREFIX):
            slot = path[len(SLOT_AGENT_PREFIX) :]
            self._write_json(200, _capabilities(f"{PUBLIC_ORIGIN}{SLOT_BRAND_PREFIX}{slot}.json"))
        elif path.startswith(SLOT_BRAND_PREFIX) and path.endswith(".json"):
            slot = path[len(SLOT_BRAND_PREFIX) : -len(".json")]
            self._write_json(
                200,
                _brand_json(slot_agent_url(slot), jwks_uri=f"{PUBLIC_ORIGIN}{SLOT_JWKS_PREFIX}{slot}.json"),
            )
        elif path.startswith(SLOT_JWKS_PREFIX) and path.endswith(".json"):
            slot = path[len(SLOT_JWKS_PREFIX) : -len(".json")]
            jwks = store.get(slot)
            if jwks is None:
                # 404 rather than an empty keyset: an empty ``{"keys": []}`` is a
                # WELL-FORMED answer, so the walk would succeed and the signature
                # would then fail as an unresolvable keyid — blaming the verifier
                # for a caller that never installed anything at this slot.
                self._write_json(404, {"error": f"no keyset installed for slot {slot!r}"})
            else:
                self._write_json(200, jwks)
        else:
            self._write_json(404, {"error": f"no counterparty document at {self.path!r}"})

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
