"""Load ``scripts/dev/gen_test_tls.py`` and build a server-side TLS context from its leaf.

Shared by every in-process TLS front — ``tests/e2e/_webhook_capture.py`` and
``tests/integration/test_local_http_origin_tls.py`` — so there is exactly one
place that knows how to reach the generator. A second copy of its SAN list
here would be exactly the drift the Core Invariant (salesagent-40qh) forbids:
every new TLS front reuses the SAME generated CA/leaf material.
"""

from __future__ import annotations

import importlib.util
import os
import ssl
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from tests.helpers.local_http_origin import guarded_origin, run_local_origin

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer

    from tests.helpers.local_http_origin import LocalOrigin
    from tests.helpers.local_mcp_origin import MCPOrigin

Guard = Callable[[str, Callable[[], None]], None]

# tests/helpers/tls_material.py -> tests/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_gen_test_tls() -> ModuleType:
    """Import ``scripts/dev/gen_test_tls.py`` directly — it lives outside any package."""
    spec = importlib.util.spec_from_file_location(
        "gen_test_tls_shared", REPO_ROOT / "scripts" / "dev" / "gen_test_tls.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def server_ssl_context(gen_test_tls: ModuleType) -> ssl.SSLContext:
    """A server-side context serving the generated leaf — the material every new front reuses."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(gen_test_tls.SERVER_CERT), keyfile=str(gen_test_tls.SERVER_KEY))
    return ctx


def ready_test_tls() -> ModuleType:
    """Prepare the shared leaf and CA once, using the common generator."""
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    return gen_test_tls


def trust_test_ca(guard: Guard) -> None:
    """Trust the shared test CA in this process, released through *guard*.

    ``SSL_CERT_FILE`` points at the COMBINED bundle (system roots plus the private CA),
    so other outbound work in the same scope keeps trusting real public roots. It is read
    whenever a TLS client context is built, which is why patching the environment
    reaches the egress seam's client.
    """
    patcher = patch.dict(os.environ, {"SSL_CERT_FILE": str(load_gen_test_tls().COMBINED_CERT)})
    patcher.start()
    guard("ssl_cert_file", patcher.stop)


def start_tls_capture_service() -> ThreadingHTTPServer:
    """Serve the e2e webhook-capture service in THIS process over TLS, on loopback; the caller owns its shutdown.

    The same service the compose stack runs, so what an in-process scenario's peer
    answers and what the e2e server's peer answers come from one implementation.
    """
    from tests.e2e.webhook_capture_service import start_capture_service

    return start_capture_service(host="127.0.0.1", port=0, ssl_context=server_ssl_context(ready_test_tls()))


def start_tls_origin(
    guard: Guard,
    label: str,
    *,
    listen_host: str = "127.0.0.1",
    public_host: str | None = None,
) -> LocalOrigin:
    """Start one HTTP origin using the shared TLS leaf and the harness lifetime."""
    return guarded_origin(
        guard,
        label,
        run_local_origin(
            listen_host=listen_host,
            public_host=public_host or listen_host,
            ssl_context=server_ssl_context(ready_test_tls()),
        ),
    )


def start_tls_mcp_origin(
    guard: Guard,
    label: str,
    *,
    tools: Mapping[str, Callable[..., Any]],
    listen_host: str = "127.0.0.1",
    public_host: str | None = None,
) -> MCPOrigin:
    """Start one MCP origin using the shared TLS leaf and the harness lifetime."""
    from tests.helpers.local_mcp_origin import run_mcp_origin

    tls = ready_test_tls()
    return guarded_origin(
        guard,
        label,
        run_mcp_origin(
            tools=tools,
            certfile=tls.SERVER_CERT,
            keyfile=tls.SERVER_KEY,
            listen_host=listen_host,
            public_host=public_host or listen_host,
        ),
    )
