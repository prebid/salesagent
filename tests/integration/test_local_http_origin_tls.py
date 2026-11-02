"""The webhook-capture / LocalOrigin TLS front, driven against the real production seam.

#1757 (steps 1, 3, 4). The Core Invariant: every new TLS front the
e2e/bdd_e2e stack and its in-process fixtures gain must reuse the SAME
generated CA/leaf material and the SAME trust mechanism (``SSL_CERT_FILE``),
never a second parallel TLS mechanism, and no verification step may ever be
silently bypassed anywhere the fronts are added.

Two things are unproven in the tree today, and this file proves both against
REAL sockets, a REAL TLS handshake and REAL certificate verification — nothing
here is mocked:

1. ``tests/helpers/local_http_origin.py``'s ``serve_in_thread()`` cannot serve
   TLS at all: it has no ``ssl_context`` parameter, so nothing in the
   ``LocalOrigin``/``run_local_origin`` family (the fixture the webhook
   capture and five integration suites already depend on) can stand behind
   HTTPS. Design step 3/4 adds ``ssl_context`` so a caller can
   ``ssl_context.wrap_socket(server.socket, server_side=True)`` before the
   serving thread starts, reusing the SAME generated leaf the rest of the
   suite already writes to ``.test-tls/`` (``scripts/dev/gen_test_tls.py``).

2. Nothing in ``src/`` reads ``SSL_CERT_FILE`` today, and the dialing side
   (``src.core.security.outbound_http`` -> ``adcp.signing.build_ip_pinned_transport``
   -> ``ssl.create_default_context()``) trusts only the process's default CA
   bundle. Design step 1 makes ``SSL_CERT_FILE`` the trust anchor for the
   generated private CA. This file proves that knob actually flips a real
   handshake from failed to verified — not that the code merely reads the
   variable.

The tests below call ``run_local_origin(..., ssl_context=...)`` directly, so
the first assertion that fails today is a real ``TypeError`` naming the
missing parameter — not a collection error, not an import error, and not a
stand-in like ``xfail``.

Positive case: a real HTTPS round trip through ``outbound_http.send()`` (the
exact seam production code calls) succeeds once ``SSL_CERT_FILE`` points at
the generated CA, with the scheme hatch (``ADCP_OUTBOUND_ALLOW_INSECURE``)
held OFF throughout — the whole point of standing this front up is that the
insecure hatch is no longer needed for this origin.

Mutation case: pointing ``SSL_CERT_FILE`` at an empty file (0 CA certs loaded,
verified directly against ``ssl.create_default_context()`` before this test
was written) makes the identical dial fail with a genuine
``ssl.SSLCertVerificationError`` / ``CERTIFICATE_VERIFY_FAILED`` — reachable
through the real exception chain, not asserted by name only — both at the raw
``adcp.signing`` transport level and through the production seam itself
(``OutboundDeliveryFailed``, closed rather than silently falling back to an
unverified connection). ``origin.hits == 0`` in both mutation cases: the
request was never delivered, because the handshake never completed.

Not exercised here (needs the full e2e/bdd_e2e Docker stack, which this atom
does not stand up — that is the implement atom's job): the actual
``docker-compose.e2e.yml`` service wiring for the creative-agent TLS front,
the ``tests.adcp.test``/``creative-agent.adcp.test`` network-alias DNS
resolution over the real compose network, and ``run_all_tests_host.sh``'s
host-run path. Those require production infrastructure changes this atom is
not allowed to make.
"""

from __future__ import annotations

import ssl

import httpx
import pytest
from adcp.signing import build_ip_pinned_transport

from src.core.security.outbound_http import OutboundDeliveryFailed, send
from tests.helpers.local_http_origin import run_local_origin
from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context
from tests.integration.test_outbound_http import set_flags

pytestmark = [pytest.mark.integration]


def _ssl_verification_cause(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """Walk *exc*'s cause chain for the real ``ssl.SSLCertVerificationError``.

    httpx wraps httpcore's ``ConnectError``, which wraps the raw
    ``ssl.SSLCertVerificationError`` — three layers deep, verified directly
    against this exact stack before this test was written. A caught-and-
    restated string match would prove only that some code raised some
    exception with the right words in it; walking the real chain proves the
    real TLS library actually refused the handshake.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        current = current.__cause__ or current.__context__
    return None


class TestLocalOriginTLSFront:
    """The primitive design steps 1, 3 and 4 create: a TLS-terminated ``LocalOrigin``.

    Every case opens the private-range hatch (loopback is a reserved address).
    There is no scheme hatch to open or close anymore — it was deleted
    entirely, exactly because this front existing made it unnecessary.
    """

    @pytest.mark.parametrize(
        "cert_attr",
        ["CA_CERT", "COMBINED_CERT"],
        ids=["ca-alone", "combined-ca-plus-system-roots"],
    )
    def test_https_round_trip_succeeds_through_the_real_outbound_seam(self, monkeypatch, cert_attr):
        """A real HTTPS delivery through ``outbound_http.send()`` succeeds once
        ``SSL_CERT_FILE`` trusts the generated CA — verification ON throughout,
        never ``verify=False`` anywhere in the call chain.

        Parametrized over both files ``gen_test_tls`` produces:
        the CA alone (``CA_CERT``) and the COMBINED bundle (``COMBINED_CERT``) —
        the literal file every dialing service's ``SSL_CERT_FILE`` actually points
        at in ``docker-compose.e2e.yml``. Proving only the CA-alone variant would
        leave the real production wiring unproven: ``SSL_CERT_FILE`` REPLACES the
        process's entire default cafile, so a regression that shipped the CA alone
        again would silently break every OTHER real-HTTPS call the same process
        makes (this broke ``uv sync`` against pypi.org once already).
        """
        gen_test_tls = load_gen_test_tls()
        gen_test_tls.ensure_test_tls()  # also (re)writes COMBINED_CERT as a side effect
        cert_path = getattr(gen_test_tls, cert_attr)
        if cert_attr == "COMBINED_CERT":
            # Without this the two branches are indistinguishable: _refresh_combined_cert
            # returns silently when no public-root bundle is found, leaving COMBINED_CERT
            # byte-identical to CA_CERT, and this branch then re-proves the ca-alone case
            # while claiming to prove the outage in the docstring.
            assert cert_path.read_bytes().count(b"-----BEGIN CERTIFICATE-----") > 1, (
                f"{cert_path} holds only our private CA — the public roots half of the "
                "bundle is missing, so SSL_CERT_FILE would again replace the process's "
                "default cafile with a single-CA file"
            )
        server_ctx = server_ssl_context(gen_test_tls)

        set_flags(monkeypatch, private=True)
        monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))

        with run_local_origin(listen_host="localhost", ssl_context=server_ctx) as origin:
            origin.respond_with(200, body=b'{"received": true}')

            result = send(
                f"https://localhost:{origin.port}/webhook",
                json={"hello": "world"},
                max_attempts=1,
            )

        assert result.http_status == 200
        assert origin.hits == 1, "the origin must have actually been dialed over the real TLS handshake"
        assert origin.last_request.json() == {"hello": "world"}, (
            "the exact bytes sent through the seam must be what the origin received"
        )
        assert result.json() == {"received": True}

    def test_broken_trust_raises_a_genuine_certificate_verify_failed_at_the_transport(self, monkeypatch, tmp_path):
        """The SAME production transport constructor (``build_ip_pinned_transport``)
        fails a real handshake, chain and all, when the trust anchor cannot
        validate the leaf — proving the green case above depends on real
        verification, not on verification being silently disabled somewhere.
        """
        gen_test_tls = load_gen_test_tls()
        gen_test_tls.ensure_test_tls()
        server_ctx = server_ssl_context(gen_test_tls)

        empty_ca = tmp_path / "empty_ca.pem"
        empty_ca.write_text("")
        monkeypatch.setenv("SSL_CERT_FILE", str(empty_ca))

        with run_local_origin(listen_host="localhost", ssl_context=server_ctx) as origin:
            origin.respond_with(200)
            url = f"https://localhost:{origin.port}/webhook"

            transport = build_ip_pinned_transport(url, allow_private=True)
            with httpx.Client(transport=transport, timeout=5.0) as client, pytest.raises(httpx.ConnectError) as excinfo:
                client.get(url)

            assert origin.hits == 0, "no request may reach the origin before the handshake completes"

        cause = _ssl_verification_cause(excinfo.value)
        assert cause is not None, (
            f"expected a real ssl.SSLCertVerificationError reachable through the exception chain; got {excinfo.value!r}"
        )
        assert "CERTIFICATE_VERIFY_FAILED" in str(cause)

    def test_broken_trust_makes_the_production_seam_fail_closed_not_silently_insecure(self, monkeypatch, tmp_path):
        """``outbound_http.send()`` itself — not just the raw transport — must
        refuse to deliver rather than silently falling back to an unverified
        connection when the trust anchor is broken.
        """
        gen_test_tls = load_gen_test_tls()
        gen_test_tls.ensure_test_tls()
        server_ctx = server_ssl_context(gen_test_tls)

        empty_ca = tmp_path / "empty_ca.pem"
        empty_ca.write_text("")
        monkeypatch.setenv("SSL_CERT_FILE", str(empty_ca))
        set_flags(monkeypatch, private=True)

        with run_local_origin(listen_host="localhost", ssl_context=server_ctx) as origin:
            origin.respond_with(200)

            with pytest.raises(OutboundDeliveryFailed) as excinfo:
                send(
                    f"https://localhost:{origin.port}/webhook",
                    json={"hello": "world"},
                    max_attempts=1,
                )

            assert origin.hits == 0, "no request may reach the origin before the handshake completes"

        assert excinfo.value.attempts == 1
        assert excinfo.value.http_status is None


class TestListenBacklog:
    """The shared origin binds a backlog large enough for xdist bursts.

    ``request_queue_size`` is the listen backlog, and socketserver reads it at
    BIND time — so it has to be a class attribute before construction, which is
    why ``serve_in_thread`` builds its own server class rather than applying it
    through ``server_attrs`` (setattr, post-construct, too late).

    This is graded rather than trusted because its failure mode is invisible on
    a quiet machine: at socketserver's default backlog of 5, a burst of ~20
    concurrent writers produces ``ConnectionResetError`` on a many-core box and
    passes everywhere else. The value was introduced to stop exactly that, and
    without an assertion it can be deleted without anything going red.
    """

    def test_serve_in_thread_binds_a_128_deep_backlog_by_default(self):
        from http.server import BaseHTTPRequestHandler

        from tests.helpers.local_http_origin import serve_in_thread

        class _Silent(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D102 - silence the default stderr logging
                pass

        with serve_in_thread(_Silent) as server:
            assert server.request_queue_size == 128, (
                f"the shared origin bound a listen backlog of {server.request_queue_size}; at "
                "socketserver's default of 5 a burst of concurrent writers produces "
                "ConnectionResetError on a busy box and passes on a quiet one"
            )

        with serve_in_thread(_Silent, request_queue_size=7) as server:
            assert server.request_queue_size == 7, (
                f"an explicit backlog was not honoured (got {server.request_queue_size}) — a caller "
                "with a different burst profile cannot express it"
            )
