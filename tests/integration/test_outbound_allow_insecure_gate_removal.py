"""Regression pin for GH #1802's Core Invariant: once
``ADCP_OUTBOUND_ALLOW_INSECURE`` is deleted, NOTHING can make either scheme
gate accept a plaintext ``http://`` URL -- not the send seam
(``src.core.security.outbound_http._require_tls``), and not the webhook
registration gate (``src.core.webhook_validator.WebhookURLValidator._require_https``).

Both gates currently read ``ADCP_OUTBOUND_ALLOW_INSECURE`` and relax when it is
``"true"`` (``outbound_http._require_tls``, ``webhook_validator._require_https``
-- the latter imports the former's env name and helper directly "so the two
gates cannot drift"). GH #1802 deletes both read sites: ``_require_tls``
becomes an unconditional scheme check, and ``_require_https()`` returns ``True``
unconditionally. This test is the TDD-red pin for that step (step 8 of
GH #1802's design) -- it asserts the POST-deletion behavior and is
expected to FAIL today, because right now setting the flag "true" really does
open both gates.

``ADCP_OUTBOUND_ALLOW_PRIVATE`` is explicitly OUT OF SCOPE for e6h0 (the Core
Invariant: "ADCP_OUTBOUND_ALLOW_PRIVATE must be left completely untouched
throughout") and stays load-bearing for every private-range refusal test in the
tree -- both cases below set it "true" too, so a failure here can only be
about the SCHEME gate, never about address policy being closed.

Spec grounding: AdCP 3.1.1, ``building/by-layer/L1/security.mdx``, "Webhook URL
validation (SSRF)", point 1 -- "reject non-HTTPS" -- is unconditional in the
spec text; it names no operator escape hatch. Conformance storyboard: ungraded
(L1 security obligation, not a wire contract) -- same as the seam's own suite
(``tests/integration/test_outbound_http.py``).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.helpers.egress_hatches import ALLOW_INSECURE_ENV, ALLOW_PRIVATE_ENV


def _seam():
    from src.core.security import outbound_http

    return outbound_http


def _open_both_hatches(monkeypatch) -> None:
    """Set BOTH escape hatches to "true", explicitly.

    Proves the test is not relying on ambient absence of the insecure flag --
    it is set "true" on purpose, and the assertion is that it still does not
    help. ``ADCP_OUTBOUND_ALLOW_PRIVATE`` is set "true" too so a refusal here
    can only be attributed to the scheme gate.
    """
    monkeypatch.setenv(ALLOW_INSECURE_ENV, "true")
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")


class TestAllowInsecureCanNeverRelaxTheSchemeGate:
    """Covers: GH #1802's Core Invariant (no plaintext-http success path
    survives the flag's deletion, at either the send seam or the webhook
    registration gate)."""

    def test_send_through_the_seam_still_refuses_plain_http_with_the_flag_set_true(self, monkeypatch, local_origin):
        """A real plain-http dial through ``send()`` to a real local origin is
        refused even with ``ADCP_OUTBOUND_ALLOW_INSECURE=true``.

        Drives the REAL seam against a REAL local origin -- nothing mocked. The
        origin is programmed to answer 200 so that, if the seam DID relax (as
        it does today), the request would actually be delivered and the
        ``hits == 0`` assertion below would catch it, not just the raised-type
        assertion.
        """
        _open_both_hatches(monkeypatch)
        local_origin.respond_with(200, body=b'{"ok": true}')

        seam = _seam()
        with pytest.raises(seam.OutboundRequestBlocked):
            seam.send(f"{local_origin.base_url}/webhook", json={"hello": "world"})

        assert local_origin.hits == 0, (
            "seam connected to a plain-http origin despite ADCP_OUTBOUND_ALLOW_INSECURE=true "
            "-- the flag must never be able to relax the scheme gate"
        )

    def test_asend_through_the_seam_still_refuses_plain_http_with_the_flag_set_true(self, monkeypatch, local_origin):
        """Async twin of the above -- ``asend`` is a separate code path through
        the same ``_require_tls`` gate and must be pinned independently."""
        _open_both_hatches(monkeypatch)
        local_origin.respond_with(200, body=b'{"ok": true}')

        seam = _seam()

        async def _dial():
            await seam.asend(f"{local_origin.base_url}/webhook", json={"hello": "world"})

        with pytest.raises(seam.OutboundRequestBlocked):
            asyncio.run(_dial())

        assert local_origin.hits == 0, (
            "seam connected to a plain-http origin despite ADCP_OUTBOUND_ALLOW_INSECURE=true "
            "-- the flag must never be able to relax the scheme gate"
        )

    def test_webhook_registration_still_rejects_plain_http_with_the_flag_set_true(self, monkeypatch):
        """``validate_webhook_url_registration`` -- the ingest-side twin of the
        seam's own scheme gate -- rejects a plain-http webhook URL even with
        ``ADCP_OUTBOUND_ALLOW_INSECURE=true``.

        No network dial here (registration is a validate-only, no-DNS check);
        the assertion is on the (is_valid, error) tuple the gate returns.
        """
        _open_both_hatches(monkeypatch)

        from src.core.webhook_validator import WebhookURLValidator

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("http://example.com/webhook")

        assert is_valid is False, (
            "webhook registration admitted a plain-http URL despite ADCP_OUTBOUND_ALLOW_INSECURE=true "
            "-- the ingest-side scheme gate must never be relaxable either"
        )
