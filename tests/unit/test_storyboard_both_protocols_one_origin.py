"""Both graded protocols must be dialed at ONE origin.

WHY THIS EXISTS. The storyboard runs once per protocol against the same agent, and the
only thing that buys is evidence that one deployment behaves the same whichever surface a
buyer speaks to. Dial the two axes at different origins and they stop comparing anything:
each measures a different deployment, and a green axis says only that *some* endpoint
answered. It was measured in exactly that state on run sa-0c74d963 — MCP on plaintext
``adcp-server-storyboard:8080`` passing 33 checks, A2A on a card-published ``https``
origin that spoke plaintext, passing 3 — and the gap read as a protocol defect rather
than as the setup difference it was.

Same host and port means the same ``Host`` header, hence the same tenant resolution and
the same identity the agent card publishes. The PATH is allowed to differ, because the
protocols fix it: MCP is handed its endpoint (``/mcp/``), while A2A is card-first and must
be handed the base url so the SDK's ``/.well-known/...`` append resolves.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from tests.storyboard.test_storyboard_conformance import (
    _DEFAULT_AGENT_URLS,
    _PROTOCOLS,
    _node_ca_env,
)


def test_every_graded_protocol_has_a_default_url() -> None:
    """A protocol the runner grades with no default url would fall back to nothing."""
    assert set(_DEFAULT_AGENT_URLS) == set(_PROTOCOLS), (
        f"graded protocols {_PROTOCOLS} do not match the urls declared {sorted(_DEFAULT_AGENT_URLS)}"
    )


def test_both_protocols_share_one_origin() -> None:
    """Scheme, host and port identical across protocols — only the path may differ."""
    origins = {
        protocol: urlsplit(url)._replace(path="", query="", fragment="").geturl()
        for protocol, url in _DEFAULT_AGENT_URLS.items()
    }

    assert len(set(origins.values())) == 1, (
        "the graded protocols are dialed at different origins, so the two axes no longer "
        f"measure the same deployment and cannot be compared: {origins}"
    )


def test_the_shared_origin_is_reachable_over_the_scheme_it_claims() -> None:
    """An ``https`` origin must be one the agent actually serves over TLS.

    ``storyboard.adcp.test`` is the ``tls-proxy`` alias; the agent's own service name is
    plaintext. Publishing ``https`` for the plaintext port is the defect that produced 25
    ``fetch failed`` checks, and it cannot be caught by dialing alone — the runner reports
    a failed handshake and a refused connection identically.
    """
    for protocol, url in _DEFAULT_AGENT_URLS.items():
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme == "https":
            assert host.endswith(".adcp.test"), (
                f"{protocol} is dialed https at {host!r}, which is not a tls-proxy alias — "
                "the SNI map in config/nginx/nginx-tls-test.conf.template routes only "
                "*.adcp.test, so anything else reaches a plaintext port over TLS"
            )


def test_the_ca_reaches_every_https_protocol() -> None:
    """Node trusts its own bundle only, so each https axis needs the test CA.

    Without it the handshake fails and the runner says ``fetch failed`` — the same string
    the plaintext-port bug produced, which is why this is asserted per protocol rather
    than assumed once.
    """
    for protocol, url in _DEFAULT_AGENT_URLS.items():
        if url.startswith("https://"):
            assert _node_ca_env(url).get("NODE_EXTRA_CA_CERTS"), (
                f"{protocol} is dialed over https but no CA bundle is passed to the runner"
            )
