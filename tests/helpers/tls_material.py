"""Load ``scripts/dev/gen_test_tls.py`` and build a server-side TLS context from its leaf.

Shared by every in-process TLS front — ``tests/e2e/_webhook_capture.py`` and
``tests/integration/test_local_http_origin_tls.py`` — so there is exactly one
place that knows how to reach the generator. A second copy of its SAN list
here would be exactly the drift the Core Invariant (salesagent-40qh) forbids:
every new TLS front reuses the SAME generated CA/leaf material.
"""

from __future__ import annotations

import importlib.util
import ssl
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from tests.helpers.local_http_origin import run_local_origin

if TYPE_CHECKING:
    from tests.helpers.local_http_origin import LocalOrigin

# tests/helpers/test_tls_material.py -> tests/ -> repo root.
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


def start_tls_origin(
    guard: Callable[[str, Callable[[], None]], None], label: str, *, listen_host: str = "127.0.0.1"
) -> LocalOrigin:
    """Start a local origin serving the shared test leaf over TLS, released through *guard*.

    *guard* is a harness env's ``_guard``: the origin is registered on the line it starts,
    so whichever path releases the env -- a clean exit or a failed enter -- closes it.
    Bind ``0.0.0.0`` when the client dialing the origin is another container.
    """
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    context = run_local_origin(listen_host=listen_host, ssl_context=server_ssl_context(gen_test_tls))
    origin = context.__enter__()

    def close() -> None:
        # A cleanup returns nothing: __exit__'s verdict would read as "suppressed".
        context.__exit__(None, None, None)

    guard(label, close)
    return origin
