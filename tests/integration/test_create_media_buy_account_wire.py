"""Wire-path tests: the ``account`` reference survives the create_media_buy wrappers.

``create_media_buy`` consumes ``req.account`` at the transport boundary via
``enrich_identity_with_account`` (scopes the buy to a managed sub-account). Like
``idempotency_key``, ``account`` only reaches ``_impl`` if every wrapper declares
and forwards it: FastMCP's TypeAdapter strips undeclared MCP params, and the A2A
skill / REST body forward it explicitly. If a wrapper drops ``account``, enrich
becomes a silent no-op and account-scoped buys break end-to-end with no error —
exactly the failure mode that left ``account`` unreachable before this change.

Each test sends a reference to a *nonexistent* account through one transport. The
boundary resolves it against the tenant's accounts and rejects with
ACCOUNT_NOT_FOUND — which can only happen if ``account`` crossed the wire. If a
wrapper drops the field, enrich is a no-op, no account lookup runs, and the
ACCOUNT_NOT_FOUND assertion fails, reddening the matching transport's test.

Account resolution raises ``AdCPAccountNotFoundError`` in the wrapper (before
``_impl``), so this surfaces as a transport *error* envelope (unlike a replayed
rejection, which is a success envelope) — asserted via ``assert_rejected``.
"""

import uuid

import pytest

from tests.harness.assertions import assert_rejected
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestAccountWirePassthrough:
    """``account`` reaches enrich_identity_with_account through every wrapper."""

    # Valid create params shared by all three transports. Account resolution
    # runs at the boundary before _impl validates packages, so packages can be
    # empty — the ACCOUNT_NOT_FOUND rejection fires first.
    _CREATE_KWARGS = {
        "brand": {"domain": "account-wire.example.com"},
        "packages": [],
        "start_time": "2026-06-01T00:00:00Z",
        "end_time": "2026-06-30T00:00:00Z",
        "po_number": "ACCOUNT-WIRE-1",
    }

    def _run_account_wire(self, transport: Transport) -> None:
        """Send a reference to a nonexistent account through *transport*, assert reject.

        Single body for all three transports — the only variable is the
        ``Transport`` enum, which ``MediaBuyCreateEnv.call_via`` routes to the
        matching real pipeline (real auth chain included).
        """
        bogus_account = {"account_id": f"no-such-account-{uuid.uuid4().hex[:8]}"}

        with MediaBuyCreateEnv() as env:
            env.setup_default_data()  # tenant + principal (real auth token) in DB
            result = env.call_via(transport, account=bogus_account, **self._CREATE_KWARGS)

        assert_rejected(result, code="ACCOUNT_NOT_FOUND")

    def test_mcp_wire_forwards_account(self, integration_db):
        """MCP wrapper declares + forwards ``account`` → boundary resolves it.

        Regression guard: if the ``create_media_buy`` MCP wrapper stops declaring
        ``account``, FastMCP's TypeAdapter strips it before the wrapper runs,
        enrich_identity_with_account sees None, no account lookup happens, and
        this test fails (no ACCOUNT_NOT_FOUND).
        """
        self._run_account_wire(Transport.MCP)

    def test_a2a_wire_forwards_account(self, integration_db):
        """A2A skill forwards ``account=params.get("account")`` → boundary resolves it.

        Regression guard: if ``_handle_create_media_buy_skill`` stops forwarding
        ``account`` to ``create_media_buy_raw``, the reference never reaches enrich
        and this test fails. Dispatch drives the real ``on_message_send`` boundary.
        """
        self._run_account_wire(Transport.A2A)

    def test_rest_wire_forwards_account(self, integration_db):
        """REST ``CreateMediaBuyBody.account`` + route passthrough → boundary resolves it.

        Regression guard: if ``CreateMediaBuyBody`` drops ``account`` (or the
        ``/api/v1/media-buys`` route stops passing it through), the reference never
        reaches enrich and this test fails.
        """
        self._run_account_wire(Transport.REST)
