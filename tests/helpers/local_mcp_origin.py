"""A real, in-process MCP origin over TLS — the only honest way to grade a TOOL-level failure.

:mod:`tests.helpers.local_http_origin` answers HTTP; that is enough to grade the
egress seam's address, redirect and pinning behaviour, because those decisions
are made before a single MCP frame is exchanged. It is NOT enough to grade what
happens AFTER a successful handshake: a tool-level failure only exists once the
client has initialised a session and issued ``tools/call``, which means the
origin has to speak MCP for real.

So this origin is a genuine ``fastmcp`` server served by uvicorn over TLS on an
ephemeral loopback port, using the same generated CA/leaf every other in-process
TLS front in this repo reuses (``scripts/dev/gen_test_tls.py``) — never a second
mechanism. Callers reach it over https because the egress seam requires https
unconditionally (GH #1802), and over loopback because a test origin must
not be reachable from outside this machine.

The origin records every tool invocation it actually serves. That counter is the
grade for a retry that must re-execute its body: a client that re-dials but does
not re-issue the tool call leaves the counter at one, and no assertion about the
returned value can tell those two apart.
"""

from __future__ import annotations

import contextlib
import functools
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastmcp import FastMCP

# How long to wait for uvicorn to bind before declaring the fixture itself broken.
_STARTUP_TIMEOUT_SECONDS = 20.0


@dataclass
class MCPOrigin:
    """Control surface of a running MCP origin: where it is, and what it served."""

    base_url: str = ""
    invocations: list[str] = field(default_factory=list)

    def invocations_of(self, tool: str) -> int:
        """How many times *tool*'s body actually ran."""
        return self.invocations.count(tool)


def _recording(origin: MCPOrigin, name: str, handler: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap *handler* so the invocation is recorded BEFORE it can fail.

    Recording first is what makes a failing tool countable: a handler that
    raises still proves the call reached the origin, which is precisely the
    fact a retry test needs about attempt 1.

    ``functools.wraps`` is load-bearing, not cosmetic: fastmcp derives the
    tool's input schema from the callable's signature, and a bare ``**kwargs``
    wrapper would advertise a tool that accepts nothing.
    """

    @functools.wraps(handler)
    def recorded(**kwargs: Any) -> Any:
        origin.invocations.append(name)
        return handler(**kwargs)

    return recorded


@contextlib.contextmanager
def run_mcp_origin(
    *,
    tools: Mapping[str, Callable[..., Any]],
    certfile: Path | str,
    keyfile: Path | str,
) -> Iterator[MCPOrigin]:
    """Serve *tools* as a real MCP endpoint at ``<base_url>`` until the block exits.

    The path ends in ``/mcp`` so the seam synthesises no ``/mcp`` fallback
    candidate — one URL, one attempt budget, so an attempt count means what the
    test says it means.
    """
    origin = MCPOrigin()

    server_mcp = FastMCP(name="stub-mcp-origin")
    for name, handler in tools.items():
        server_mcp.tool(_recording(origin, name, handler), name=name)

    server = uvicorn.Server(
        uvicorn.Config(
            server_mcp.http_app(path="/mcp"),
            host="127.0.0.1",
            port=0,
            log_level="critical",
            ssl_certfile=str(certfile),
            ssl_keyfile=str(keyfile),
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            server.should_exit = True
            raise TimeoutError("the stub MCP origin never started listening")
        time.sleep(0.01)

    origin.base_url = f"https://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/mcp"
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=10)
