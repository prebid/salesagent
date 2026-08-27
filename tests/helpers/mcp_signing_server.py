"""A REAL local MCP counterparty for outbound request-signing tests (#1291 C3).

``salesagent-z6nr.29``'s Core Invariant is about signing OUR outbound calls to a
creative/signals agent over the ``adcp`` SDK's MCP transport
(``ADCPClient(protocol="mcp", signing=SigningConfig(...))``). The defect class
the epic's HIGH finding names (``adcontextprotocol/adcp-client-python#1017``) is
a genuine asyncio ``ContextVar`` propagation bug in the ``mcp`` package's
streamable-http transport: the SDK's own ``current_operation.set()``
(``adcp/protocols/mcp.py:560``) fires AFTER the lazy session connect has
already spawned the ``post_writer`` background task
(``mcp/client/streamable_http.py:659-667``), so the RFC 9421 signing hook
always reads ``current_operation is None`` and never signs.

That bug is a real ``asyncio.TaskGroup``/``anyio`` task-spawn timing issue, not
a wire-format detail — it can only be proven (or disproven) against the REAL
``mcp`` client package talking real streamable-HTTP to a REAL ASGI server. A
``httpx.MockTransport`` stand-in (the pattern ``tests/helpers/webhook_wire.py``
uses for plain single-POST webhook sends) would have to hand-roll the MCP
session handshake (``initialize`` / ``notifications/initialized`` /
``tools/call``) itself — reproducing exactly the protocol machinery whose
timing is under test, which risks encoding the WRONG assumptions about it.
This helper instead runs a real ``fastmcp.FastMCP`` app under a real
``uvicorn`` server on a loopback socket: the only thing "faked" is which
service the counterparty is (a stand-in creative/signals agent), never the
transport itself.

Usage::

    with LocalSigningMCPServer() as server:
        client = ADCPClient(
            agent_config=AgentConfig(
                id="counterparty",
                agent_uri=server.url,
                protocol=Protocol.MCP,
                mcp_transport="streamable_http",
            ),
            signing=SigningConfig(...),
        )
        try:
            ...
        finally:
            await client.close()

        calls = server.calls("tools/call")
        assert "signature" in calls[-1].headers
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass

import httpx
import uvicorn
from fastmcp import FastMCP


@dataclass(frozen=True)
class CapturedMCPRequest:
    """One inbound HTTP request, as the counterparty's socket actually saw it."""

    headers: dict[str, str]
    rpc_method: str | None
    body: bytes


class LocalSigningMCPServer:
    """A real FastMCP streamable-http server that records every inbound request.

    Registers ``list_creative_formats`` — the AdCP operation name production's
    creative-agent call sites use (``creative_agent_registry.py``) — accepting
    the ``adcp_version`` keyword the SDK's envelope enricher always injects
    (``ADCPClient.__init__``'s ``_inject_adcp_version``), so a real
    ``ListCreativeFormatsRequest()`` call validates cleanly server-side rather
    than surfacing an unrelated schema-validation error that would obscure the
    signing assertion.

    Also registers a REAL ``get_adcp_capabilities`` tool (the SDK's own
    ``_sign_outgoing_request`` hook calls ``fetch_capabilities()`` before
    signing any eligible request) so the capability round-trip a signing test
    needs is a genuine call to this same counterparty, not a bypass of it.
    Pass *request_signing* to control what it advertises — e.g.
    ``{"supported": True, "required_for": ["list_creative_formats"]}``. Omit
    it (the default) to advertise no signing support at all.
    """

    def __init__(self, *, request_signing: dict | None = None) -> None:
        self.requests: list[CapturedMCPRequest] = []
        self._mcp_app = FastMCP("outbound-signing-test-agent")
        self._capabilities_payload = {
            "adcp": {
                "major_versions": [3],
                "idempotency": {"supported": True, "replay_ttl_seconds": 3600},
            },
            "supported_protocols": ["creative"],
        }
        if request_signing is not None:
            self._capabilities_payload["request_signing"] = request_signing

        @self._mcp_app.tool(name="list_creative_formats")
        def _list_creative_formats(adcp_version: str | None = None) -> dict:
            return {"status": "completed", "formats": []}

        @self._mcp_app.tool(name="get_adcp_capabilities")
        def _get_adcp_capabilities(adcp_version: str | None = None) -> dict:
            return dict(self._capabilities_payload)

        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    @property
    def url(self) -> str:
        assert self.port is not None, "server has not started yet"
        return f"http://127.0.0.1:{self.port}/mcp"

    def calls(self, rpc_method: str) -> list[CapturedMCPRequest]:
        """Every captured request whose JSON-RPC ``method`` equals *rpc_method*.

        e.g. ``"tools/call"`` for the actual tool invocation, ``"initialize"``
        for the MCP session handshake.
        """
        return [r for r in self.requests if r.rpc_method == rpc_method]

    def _build_asgi_app(self):
        app = self._mcp_app.http_app(path="/mcp")
        requests_sink = self.requests

        class _RequestSpyMiddleware:
            """Bare ASGI middleware: records headers + JSON-RPC method per request."""

            def __init__(self, inner_app):
                self._inner = inner_app

            async def __call__(self, scope, receive, send):
                if scope["type"] != "http":
                    await self._inner(scope, receive, send)
                    return

                headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])
                }
                chunks: list[bytes] = []

                async def _spying_receive():
                    message = await receive()
                    if message["type"] == "http.request":
                        chunks.append(message.get("body", b""))
                    return message

                await self._inner(scope, _spying_receive, send)

                body = b"".join(chunks)
                rpc_method = None
                if body:
                    try:
                        rpc_method = json.loads(body).get("method")
                    except (json.JSONDecodeError, AttributeError):
                        rpc_method = None
                requests_sink.append(CapturedMCPRequest(headers=headers, rpc_method=rpc_method, body=body))

        app.add_middleware(_RequestSpyMiddleware)
        return app

    def _run(self) -> None:
        async def _main() -> None:
            config = uvicorn.Config(self._build_asgi_app(), host="127.0.0.1", port=0, log_level="warning")
            server = uvicorn.Server(config)
            serve_task = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.005)
            self.port = server.servers[0].sockets[0].getsockname()[1]
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.02)
            server.should_exit = True
            await serve_task

        asyncio.run(_main())

    def start(self, timeout: float = 10.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError("LocalSigningMCPServer did not start in time")
        # Confirm the socket actually accepts connections before handing back
        # control -- uvicorn's `server.started` flips slightly before accept()
        # is guaranteed to succeed on every platform.
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                httpx.get(self.url, timeout=0.2)
                return
            except httpx.ConnectError as exc:
                last_error = exc
                time.sleep(0.02)
        raise RuntimeError(f"LocalSigningMCPServer never accepted a connection: {last_error}")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def __enter__(self) -> LocalSigningMCPServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
