#!/usr/bin/env python3
"""
A2A Standard Endpoints Test - ACTUALLY WORKING VERSION

This replaces the skipped test_a2a_standard_endpoints.py with a version that actually runs.
The original was skipped because it tried to use python_a2a library, but we use a2a-sdk.

This test validates the actual HTTP endpoints that our A2A server exposes.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
import requests
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskNotFoundError
from adcp import get_adcp_spec_version

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.app import _AGENT_CARD_PATHS  # noqa: E402  (after the sys.path bootstrap above)
from src.core.tools.registry import TOOLS  # noqa: E402  (after the sys.path bootstrap above)
from tests.e2e.conftest import e2e_ca_bundle  # noqa: E402  (after the sys.path bootstrap)
from tests.e2e.utils import declare_tenant_front  # noqa: E402
from tests.helpers.credentials import credential_headers

# Read the declared set from production: a path added to (or dropped from)
# `_AGENT_CARD_PATHS` must change what these tests grade. Sorted for a
# deterministic parametrization order.
AGENT_CARD_PATHS = sorted(_AGENT_CARD_PATHS)

# The one path the a2a-sdk factory mounts today — the regression guard.
CANONICAL_AGENT_CARD_PATH = "/.well-known/agent-card.json"


def card_origin(live_server) -> str:
    """The origin to fetch an agent card from, and NO tenant header goes with it.

    A card is discovery: a buyer has a hostname and nothing else, so the tenant has to be
    resolvable from the HOST. Prefers the stack's named TLS front.
    """
    return live_server.get("tls") or live_server["a2a"]


class TestA2AEndpointsActual:
    """Test actual A2A endpoints that we implement.

    The base URL arrives as the ``live_server`` fixture VALUE, not out of
    ``ADCP_SALES_PORT``. A process-global carries no sender (the fixture, the
    compose file and scripts/test-stack.sh all write that variable), no lifetime
    and no multiplicity, and its ``"8080"`` default silently aimed these tests at
    whatever happened to be listening there. Taking ``live_server`` also means the
    stack is guaranteed up, so a connection failure is a real failure instead of a
    skip — same rule as ``test_unknown_task_id_returns_task_not_found_code_on_the_wire``
    below.

    The front is declared here for the same reason the discovery-path class declares it: a
    card fetch is discovery, so the tenant has to be resolvable from the Host alone, and a
    request naming a host no tenant declares is refused rather than answered with a card.
    """

    @pytest.fixture(autouse=True)
    def _front_declared(self, live_server):
        """This stack's tenant declares the host these tests fetch the card from.

        Released after each test: there is one TLS origin and ``virtual_host`` is unique, so
        holding it would take it from the signing suite.
        """
        with declare_tenant_front(live_server, card_origin(live_server)):
            yield

    @pytest.mark.integration
    def test_well_known_agent_json_endpoint_live(self, live_server):
        """Test /.well-known/agent-card.json endpoint against live server."""
        # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
        response = requests.get(
            f"{card_origin(live_server)}/.well-known/agent-card.json", verify=e2e_ca_bundle(), timeout=5
        )

        assert response.status_code == 200, (
            f"the declared front returned {response.status_code} for the canonical card path: {response.text[:300]!r}"
        )
        assert response.headers["content-type"].startswith("application/json")

        data = response.json()
        assert "name" in data
        assert "description" in data
        assert "version" in data
        assert "skills" in data

        # a2a-sdk 1.0 (protobuf): URL is in supportedInterfaces, not top-level
        assert "supportedInterfaces" in data, "Agent card must have supportedInterfaces"
        interfaces = data["supportedInterfaces"]
        assert len(interfaces) > 0
        url = interfaces[0]["url"]

        # Critical regression test: URL should not have trailing slash
        assert not url.endswith("/"), f"Agent card URL should not have trailing slash: {url}"
        assert url.endswith("/a2a"), f"Agent card URL should end with '/a2a': {url}"

        # Should be Prebid Sales Agent
        assert data["name"] == "Prebid Sales Agent"

        # Should have skills
        assert "skills" in data
        assert len(data["skills"]) > 0

        # AdCP 2.5: Should have AdCP extension in capabilities
        assert "capabilities" in data
        assert "extensions" in data["capabilities"]
        extensions = data["capabilities"]["extensions"]
        assert len(extensions) > 0

        # Find AdCP extension
        adcp_ext = None
        for ext in extensions:
            if "adcp-extension" in ext.get("uri", ""):
                adcp_ext = ext
                break

        assert adcp_ext is not None, "AdCP extension not found in live agent card"
        assert adcp_ext["params"]["adcp_version"] == get_adcp_spec_version()
        assert "media_buy" in adcp_ext["params"]["protocols_supported"]

    @pytest.mark.integration
    @pytest.mark.parametrize("retired", ["/agent.json", "/.well-known/agent.json"])
    def test_a_retired_card_path_is_not_served(self, live_server, retired):
        """The paths this seller stopped serving answer 404, and keep answering 404.

        The card is declared on one path (``AGENT_CARD_PATH``). These two were served as
        well: ``/.well-known/agent.json`` is the path AdCP's guide names, and ``/agent.json``
        is a bare spelling no specification names. Serving one document at three addresses
        lets a URL-keyed cache hold three copies of one agent's card.

        Asserted rather than dropped, because "retired" is a claim that can regress: a route
        re-added here would not fail any other test, and this is what makes the single
        declaration enforceable. A conforming client is unaffected — ``@adcp/sdk``'s
        ``buildCardUrls`` tries both well-known paths and breaks on the first success.
        """
        response = requests.get(f"{card_origin(live_server)}{retired}", verify=e2e_ca_bundle(), timeout=5)

        assert response.status_code == 404, (
            f"{retired} answered {response.status_code}; this seller serves the card at "
            f"{CANONICAL_AGENT_CARD_PATH} alone, and a second address for one document is "
            f"a second document as far as any cache keyed on the URL is concerned"
        )

    @pytest.mark.integration
    def test_a2a_endpoint_accessible(self, live_server):
        """Test that /a2a endpoint is accessible (may require auth)."""
        # Test both /a2a and /a2a/ paths
        for path in ["/a2a", "/a2a/"]:
            response = requests.post(f"{live_server['a2a']}{path}", json={"test": "data"}, timeout=2)

            # Should not be 404 (endpoint exists)
            assert response.status_code != 404, f"Endpoint {path} should exist"

    @pytest.mark.integration
    def test_cors_headers_present(self, live_server):
        """Test that CORS headers are present for browser compatibility."""
        # CORS headers are only returned when the Origin matches an allowed origin.
        # Default ALLOWED_ORIGINS is "http://localhost:8000" — use that as Origin.
        allowed_origin = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")[0].strip()

        # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
        response = requests.get(
            f"{card_origin(live_server)}/.well-known/agent-card.json",
            headers={"Origin": allowed_origin},
            verify=e2e_ca_bundle(),
            timeout=5,
        )

        assert response.status_code == 200, (
            f"the declared front returned {response.status_code} for the card: {response.text[:300]!r}"
        )
        # Should have CORS headers for an allowed origin
        assert "Access-Control-Allow-Origin" in response.headers, "Missing CORS headers"

    @pytest.mark.integration
    def test_options_preflight_support(self, live_server):
        """Test that OPTIONS requests work for CORS preflight.

        Declares the front for the same reason the discovery-path tests do: the card
        describes a resolved tenant, and the host this stack is reached at is decided per
        session, so the test is what states it.
        """
        # The tenant declares the front this request names, because a card fetch carries
        # no tenant header — a request naming a host no tenant declares is refused before
        # the route's OPTIONS handling is ever reached.
        with declare_tenant_front(live_server, card_origin(live_server)):
            # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
            response = requests.options(
                f"{card_origin(live_server)}/.well-known/agent-card.json", verify=e2e_ca_bundle(), timeout=5
            )

        # Should handle OPTIONS requests
        assert response.status_code in [200, 204], "OPTIONS request should be handled"


class TestAgentCardDiscoveryPathsLive:
    """Every declared agent-card path serves the same card on a LIVE server (#1440).

    The live server runs under lifespan, where `_install_admin_mounts()`
    re-appends the Flask catch-all `Mount("/")` last. That is the surface the
    in-process TestClient probe in
    tests/unit/test_a2a_transport_contract.py cannot reach: here a 200 also
    proves the card routes are matched BEFORE the catch-all, not swallowed by it.

    NO TENANT HEADER, and that is the point. A card fetch is discovery: a client has a
    hostname and nothing else. So the tenant has to be resolvable from the HOST, which
    means this stack's tenant must declare the front it is served at — a per-session fact
    (a compose service name in-network, a dynamic TLS port on the host path) that the test
    knows and states through ``declare_tenant_front``. A request naming a host no tenant
    declares is refused, which the last test here grades.
    """

    @pytest.fixture(autouse=True)
    def _front_declared(self, live_server):
        """This stack's tenant declares the host these tests fetch the card from.

        Released afterwards: there is one TLS origin and ``virtual_host`` is unique, so
        holding it past these tests takes it from the signing suite.
        """
        with declare_tenant_front(live_server, card_origin(live_server)):
            yield

    @pytest.mark.integration
    @pytest.mark.parametrize("path", AGENT_CARD_PATHS)
    def test_declared_card_path_is_served_live(self, live_server, path):
        """GET on every path in _AGENT_CARD_PATHS returns 200 from the live server."""
        response = requests.get(f"{card_origin(live_server)}{path}", verify=e2e_ca_bundle(), timeout=5)

        assert response.status_code == 200, (
            f"{path} is declared in _AGENT_CARD_PATHS but the live server returned "
            f"{response.status_code}; every declared discovery path must be served"
        )
        assert response.headers["content-type"].startswith("application/json")

    @pytest.mark.integration
    def test_a_request_naming_no_tenant_is_refused_as_a_misconfiguration(self, live_server):
        """No tenant, no card — refused with the seller-side code, not a card.

        The complement of the tests above: the card publishes a tenant's stored identity,
        so with no tenant there is nothing truthful to publish. The refusal names what was
        rejected, which is the operator's lever; what it must never do is publish the
        caller's Host as the AGENT'S OWN advertised URL, which is what it did before #1440
        and what let an attacker-supplied `Host: evil.example.com` come back as
        `supportedInterfaces[0].url`.
        """
        response = requests.get(
            f"{card_origin(live_server)}{CANONICAL_AGENT_CARD_PATH}",
            headers={"Host": "unclaimed.example"},
            verify=e2e_ca_bundle(),
            timeout=5,
        )

        assert response.status_code == 421, (
            f"a Host no tenant claims returned {response.status_code}; 421 Misdirected Request "
            f"is the status for a request this server cannot answer authoritatively for"
        )
        body = response.json()
        assert body["adcp_error"]["code"] == "TENANT_UNDEFINED", body
        assert body["adcp_error"]["recovery"] == "terminal", body
        # The host the caller named belongs in the SERVER's record, not the envelope: this
        # code declares no details shape, and echoing caller-controlled text back is the
        # shape this route exists to refuse.
        assert "unclaimed.example" not in response.text, (
            f"the refusal echoed the caller's own Host back to it: {response.text[:300]!r}"
        )
        assert "supportedInterfaces" not in response.text, (
            "the refusal published an agent URL built from the caller's own Host"
        )

    @pytest.mark.integration
    def test_the_card_is_declared_on_the_canonical_path_alone(self, live_server):
        """ONE declared path, and it is the one A2A fixes.

        This compared every declared path's raw bytes against the canonical one's, which was
        the right test while three paths were served. One path cannot disagree with itself,
        so what is graded now is the DECLARATION: a non-canonical path added back here would
        reintroduce the divergence the single declaration exists to prevent, and a buyer
        reaching the card at two addresses can cache two documents for one agent.

        ``/.well-known/agent.json`` (AdCP's guide) and ``/agent.json`` were both served.
        Dropping them is safe for discovery because a conforming client falls back --
        ``@adcp/sdk``'s ``buildCardUrls`` tries both well-known paths and breaks on the
        first success.
        """
        assert AGENT_CARD_PATHS == [CANONICAL_AGENT_CARD_PATH], (
            f"the card is declared on {AGENT_CARD_PATHS}; A2A fixes "
            f"{CANONICAL_AGENT_CARD_PATH} (§8.2, §14.3) and this seller serves that one"
        )

        response = requests.get(
            f"{card_origin(live_server)}{CANONICAL_AGENT_CARD_PATH}", verify=e2e_ca_bundle(), timeout=5
        )
        assert response.status_code == 200, f"{CANONICAL_AGENT_CARD_PATH} returned {response.status_code}"
        assert response.json()["supportedInterfaces"], "the card declares no interface for a client to select"


class TestA2AAgentCardCreation:
    """Test agent card creation functions directly (no HTTP required)."""


class TestA2ARequestHandler:
    """Test the A2A request handler directly."""

    def setup_method(self):
        """Set up test fixtures."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        self.handler = AdCPRequestHandler()

    def test_handler_initialization(self):
        """Test that handler initializes correctly."""
        assert self.handler is not None
        assert hasattr(self.handler, "tasks")
        assert isinstance(self.handler.tasks, dict)

    def test_handler_has_required_methods(self):
        """Test that handler has all required A2A methods."""
        required_methods = [
            "on_message_send",
            "on_message_send_stream",
            "on_get_task",
            "on_cancel_task",
        ]

        for method_name in required_methods:
            assert hasattr(self.handler, method_name), f"Handler missing method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Method {method_name} is not callable"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "request_cls, method_name",
        [(GetTaskRequest, "on_get_task"), (CancelTaskRequest, "on_cancel_task")],
    )
    async def test_unknown_task_id_raises_task_not_found(self, request_cls, method_name):
        """An unknown task id raises TaskNotFoundError, not the generic internal
        error a bare None return produces — cancel is the same not-found condition
        as get, and both route through the shared ``_get_task_or_raise``.

        Fast smoke check on the raise only. It does NOT prove the wire code: the
        exception carries no code, and the client actually sees -32603 — see
        ``_get_task_or_raise`` (src/a2a_server/adcp_a2a_server.py) and #1670 for
        why, plus the xfail'd live-server test in TestA2AServerIntegration that
        grades the code on the wire. Assert on str(exc), not exc.code — there is
        none.

        Parametrized over both entry points so the shared assertion cannot drift
        between two byte-identical copies.

        Both halves of the raise are pinned: the human-readable message AND the
        structured ``data`` payload clients actually parse. Asserting the message
        alone would let ``data={"task_id": ...}`` be deleted with the suite still
        green, since the id appears in the message either way.
        """
        with pytest.raises(TaskNotFoundError) as exc:
            await getattr(self.handler, method_name)(request_cls(id="task_does_not_exist"), MagicMock())
        assert "task_does_not_exist" in str(exc.value)  # the requested id is surfaced
        assert exc.value.data == {"task_id": "task_does_not_exist"}  # ...and machine-readable

    def test_core_skills_are_dispatchable_over_a2a(self):
        """The core skills are dispatchable over A2A, per the registry.

        Dispatch is the single derived ``_dispatch_skill``, and a row is dispatchable
        because ``TOOLS[name].a2a`` is True — not because a ``_handle_<tool>_skill`` method
        exists. ``hasattr``-based selection overrides the registry, which is how a card
        comes to advertise a skill that answers MethodNotFoundError.
        """
        assert callable(self.handler._dispatch_skill), "A2A's one dispatch method is missing"

        # Note: get_signals removed - should come from dedicated signals agents
        for tool_name in ("get_products", "create_media_buy", "sync_creatives", "list_creatives"):
            assert TOOLS[tool_name].a2a is True, f"{tool_name} is not dispatchable over A2A"


class TestA2AServerIntegration:
    """Integration tests for complete A2A server setup."""

    @pytest.fixture(autouse=True)
    def _front_declared(self, live_server):
        """Declared for the card fetch in ``test_server_discovery_flow``.

        A card fetch carries no tenant header, so the tenant has to be resolvable from the
        Host alone or the request is refused.
        """
        with declare_tenant_front(live_server, card_origin(live_server)):
            yield

    @pytest.mark.integration
    @pytest.mark.parametrize("method", ["GetTask", "CancelTask"])
    def test_unknown_task_id_returns_task_not_found_code_on_the_wire(self, method, live_server):
        """The deliverable of the TaskNotFoundError change is what an A2A client
        SEES: JSON-RPC error code -32001. That code is not carried by the
        exception — it is synthesized downstream — so the direct-call test in
        TestA2ARequestHandler cannot prove it. This POSTs the real request to the
        running /a2a endpoint and grades the code on the wire.

        Uses the ``live_server`` fixture so the transport is guaranteed up: the
        base URL comes from ``live_server['a2a']`` and the assertion runs
        deterministically instead of skipping when nothing happens to be listening
        on the ad-hoc port — a skip under strict xfail is neither XFAIL nor XPASS,
        so the sole on-the-wire grade must never be allowed to no-op.

        Parametrized over both methods. `CancelTask` of an unknown id went from a silent
        None to an error, so it needs the same wire tripwire as `GetTask` — otherwise
        only half the contract is locked in.

        GRADUATED from a strict xfail against #1670. The code WAS -32603, because the
        v0.3 compat adapter ended in a bare `except Exception -> CoreInternalError` with
        no `A2AError -> code` mapping, flattening every raised error. With that adapter
        removed, requests dispatch through the SDK's own dispatcher, which performs the
        mapping: both methods now answer the spec's -32001. Measured before the xfail was
        deleted, not assumed from the marker going green.
        """
        response = requests.post(
            f"{live_server['a2a']}/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": "task_does_not_exist"}},
            headers={"A2A-Version": "1.0"},
            timeout=5,
        )

        assert response.status_code == 200, f"JSON-RPC errors ride a 200 envelope: {response.status_code}"
        data = response.json()
        assert "error" in data, f"unknown task id must produce a JSON-RPC error: {data}"
        assert data["error"]["code"] == -32001, (
            f"A2A spec defines -32001 (TaskNotFoundError) for an unknown task id, got {data['error']['code']}"
        )

    @pytest.mark.integration
    def test_server_discovery_flow(self, live_server):
        """Test complete A2A client discovery flow.

        The stack is guaranteed up by ``live_server`` and the fixture above declares the
        front this fetches, so a non-200 here is a failure and never a skip.
        """
        # Step 1: Client discovers agent (a2a-sdk 1.0 canonical path)
        response = requests.get(
            f"{card_origin(live_server)}/.well-known/agent-card.json", verify=e2e_ca_bundle(), timeout=5
        )

        assert response.status_code == 200, (
            f"the declared front returned {response.status_code} for the card a client discovers "
            f"the agent with: {response.text[:300]!r}"
        )
        agent_card = response.json()

        # Step 2: Validate agent card has what client needs
        assert "skills" in agent_card
        # a2a-sdk 1.0 (protobuf): URL is in supportedInterfaces, not top-level
        assert "supportedInterfaces" in agent_card

        # Step 3: Validate URL format for messaging
        url = agent_card["supportedInterfaces"][0]["url"]
        assert not url.endswith("/"), "URL should not have trailing slash (causes redirects)"

        # Step 4: Test that messaging endpoint exists
        messaging_url = url if url.endswith("/a2a") else f"{url}/a2a"

        # Try to connect (will fail with auth error, but should not be 404). The URL comes
        # off the card, so it is the declared front's https origin and needs the test CA.
        response = requests.post(messaging_url, json={"test": "message"}, verify=e2e_ca_bundle(), timeout=5)
        assert response.status_code != 404, "Messaging endpoint should exist"

    @pytest.mark.integration
    def test_authentication_flow(self, live_server):
        """Test authentication requirements."""
        # Should require Bearer token for messaging
        response = requests.post(
            f"{live_server['a2a']}/a2a",
            headers=credential_headers(token="invalid-token"),
            json={"method": "SendMessage", "params": {}},
            timeout=2,
        )

        # Should reject invalid token (401) not be 404
        assert response.status_code != 404, "Endpoint should exist"

        # Missing auth should also not be 404
        response = requests.post(f"{live_server['a2a']}/a2a", json={"method": "SendMessage", "params": {}}, timeout=2)
        assert response.status_code != 404, "Endpoint should exist even without auth"
