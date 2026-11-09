"""The in-process peer creative agent: one per worker process, one set of tool answers per key.

The e2e stack hosts exactly one peer creative agent, the ``webhook-capture`` service,
and a scenario programs it under a key of its own (``POST /control/peer/<key>/<tool>``).
This is the same service started once in THIS process, over TLS on loopback, so an
in-process scenario's peer and the live server's peer are one implementation addressed
the same way. Each xdist worker is its own process and so gets its own port and store;
keys are minted per call, so two scenarios never read each other's answers.

Started lazily on first use and shut down at interpreter exit: a server per scenario
would add a TLS listener and a thread per scenario for no isolation the key does not
already give.

The documents a peer serves are built here too, because the service itself is
stdlib-only (it runs on a bare image in the compose stack) and cannot import the SDK
that states their pinned shape.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer

    from src.core.schemas import Format

_server: ThreadingHTTPServer | None = None


def _shared_peer_agent() -> ThreadingHTTPServer:
    """This process's peer agent, started on first call."""
    global _server
    if _server is None:
        from tests.helpers.tls_material import start_tls_capture_service

        server = start_tls_capture_service()

        def close() -> None:
            server.shutdown()
            server.server_close()

        atexit.register(close)
        _server = server
    return _server


def refuse_the_public_creative_agent(guard: Callable[[str, Callable[[], None]], None]) -> None:
    """No in-process env reaches the live public creative agent; an attempt fails the env on exit.

    The live host rate-limits, drifts and goes down, so it must never be a test
    dependency. Refused one layer below the seller, at the resolver the egress gate asks,
    which is a system the seller does not own: its host does not resolve, so nothing is
    ever sent there. Every attempt is recorded, and the env's exit raises on any, because
    a refused dial can be absorbed by its call site and leave the scenario green.

    *guard* is the env's ``_guard``; both the patch and the verdict are released through it.
    """
    import socket
    from unittest.mock import patch
    from urllib.parse import urlsplit

    from src.core.creative_agent_registry import PUBLIC_DEFAULT_AGENT_URL

    host = urlsplit(PUBLIC_DEFAULT_AGENT_URL).hostname
    resolve = socket.getaddrinfo
    attempts: list[str] = []

    def getaddrinfo(name: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(name, str) and name.lower().rstrip(".") == host:
            attempts.append(f"{name}:{port}")
            raise socket.gaierror(socket.EAI_NONAME, f"in-process tests never resolve {name}")
        return resolve(name, port, *args, **kwargs)

    def verdict() -> None:
        if attempts:
            raise AssertionError(
                f"production dialled the live public creative agent {attempts} from an in-process env. "
                "The operator creative agent is dialled at CREATIVE_AGENT_URL: compose PeerCreativeAgentMixin "
                "(tests/harness/peer_agent.py), whose stand-in answers for it, or host the format on the "
                "tenant's own peer agent (host_reference_format)."
            )

    patcher = patch.object(socket, "getaddrinfo", getaddrinfo)
    patcher.start()
    guard("public creative agent refusal", patcher.stop)
    guard("public creative agent verdict", verdict)


def new_peer_key() -> str:
    """A fresh key: the URL a catalog is served under changes with every catalog."""
    return f"peer-{uuid4().hex}"


def peer_agent_url(key: str) -> str:
    """The agent URL a seller dials for the peer served under *key*."""
    from tests.e2e.webhook_capture_service import peer_path

    return f"https://127.0.0.1:{_shared_peer_agent().server_address[1]}{peer_path(key)}"


def serve_peer_tool(key: str, tool: str, document: dict) -> str:
    """Serve *document* as the answer to *tool* under *key*; return the agent URL a seller dials."""
    _shared_peer_agent().store.serve_peer_tool(key, tool, document)
    return peer_agent_url(key)


def peer_tool_calls(key: str, tool: str) -> list[dict]:
    """The arguments of every *tool* call this process's peer received under *key*, oldest first."""
    from tests.e2e.webhook_capture_service import tool_calls

    return tool_calls(_shared_peer_agent().store.get(key), tool)


def catalog_entry(fmt: Format, agent_url: str | None = None) -> dict[str, Any]:
    """*fmt* as a peer's ``list_creative_formats`` entry, re-homed at *agent_url* when given.

    Serialized here because the document is what a real agent puts on the wire; the
    seller's own parse (``PeerFormat``) is what reads it back. With *agent_url*, the entry
    is unchanged except for ``format_id.agent_url``: an agent states its own URL there for a
    format it publishes, and a seller dials whatever URL a format's identity names.
    """
    entry = fmt.model_dump(mode="json", exclude_none=True)
    if agent_url is not None:
        entry["format_id"]["agent_url"] = agent_url
    return entry


def rehomed_reference_format(format_id: str, agent_url: str) -> dict[str, Any]:
    """The reference capture's *format_id*, published by the agent at *agent_url* as its own.

    Raises ``ValueError`` for an id the capture does not carry.
    """
    from src.core.format_cache import load_reference_formats

    (fmt,) = [f for f in load_reference_formats() if f.format_id.id == format_id]
    return catalog_entry(fmt, agent_url)


def single_preview_document(key: str) -> dict[str, Any]:
    """A ``preview_creative`` answer for the peer under *key*: one variant, one URL render.

    The single-request arm of creative/preview-creative-response.json (AdCP 3.2.7), built
    through the SDK's model of that arm so the document has the pinned shape, the
    protocol envelope's ``status`` included. It names no dimensions, so a seller back-fills
    no width or height from it: one document answers every preview under the key, and a
    size that fitted one format would misstate another.
    """
    from adcp.types import LegacyPreviewCreativeSingleResponse as PreviewCreativeSingleResponse

    return PreviewCreativeSingleResponse.model_validate(
        {
            "response_type": "single",
            "previews": [
                {
                    "preview_id": f"{key}-preview",
                    "renders": [
                        {
                            "render_id": f"{key}-render",
                            "output_format": "url",
                            "preview_url": f"https://preview.example.com/{key}.html",
                            "role": "primary",
                        }
                    ],
                    "input": {"name": "Default"},
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
