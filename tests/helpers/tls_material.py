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
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import patch

from tests.helpers.local_http_origin import run_local_origin

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer

    from tests.helpers.local_http_origin import LocalOrigin

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


def _ready_server_context() -> ssl.SSLContext:
    """The shared leaf, generated or refreshed if needed, as a server-side context."""
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    return server_ssl_context(gen_test_tls)


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

    return start_capture_service(host="127.0.0.1", port=0, ssl_context=_ready_server_context())


def start_tls_origin(guard: Guard, label: str, *, listen_host: str = "127.0.0.1") -> LocalOrigin:
    """Start a local origin serving the shared test leaf over TLS, released through *guard*.

    *guard* is a harness env's ``_guard``: the origin is registered on the line it starts,
    so whichever path releases the env -- a clean exit or a failed enter -- closes it.
    Bind ``0.0.0.0`` when the client dialing the origin is another container.
    """
    context = run_local_origin(listen_host=listen_host, ssl_context=_ready_server_context())
    origin = context.__enter__()

    def close() -> None:
        # A cleanup returns nothing: __exit__'s verdict would read as "suppressed".
        context.__exit__(None, None, None)

    guard(label, close)
    return origin
