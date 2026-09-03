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
``_impl``), so this surfaces as a transport *error* envelope (a replayed cached
success, by contrast, surfaces as a success envelope) — asserted via
``assert_rejected``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from tests.harness.assertions import assert_rejected, assert_wire_omits_unset
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestAccountWirePassthrough:
    """``account`` reaches enrich_identity_with_account through every wrapper."""

    # Valid create params shared by all three transports. `packages` carries a real
    # entry rather than an empty list: the request model enforces the pin's MinLen(1),
    # and REQUEST VALIDATION FIRES BEFORE ACCOUNT RESOLUTION, so an empty array is
    # rejected as VALIDATION_ERROR and this test never reaches the account check it
    # grades. An earlier comment here reasoned the other way round — that account
    # resolution runs first so packages could be empty — which was true only while the
    # local model had dropped the parent's bound.
    _CREATE_KWARGS = {
        "brand": {"domain": "account-wire.example.com"},
        "packages": [{"product_id": "prod_1", "budget": 1000, "pricing_option_id": "po_1"}],
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


class TestAccountReferenceRoutesTheAdapter:
    """The mode an account reference RESOLVES TO decides which adapter is built.

    The class above proves ``account`` crosses the wire. This proves the resolved
    mode is then acted on — the identity-keyed forwarding site, which had no oracle.

    ``test_sandbox_production_paths`` grades the BUY-keyed paths, where the mode comes
    from the buy's own account. This is the other half: a request that CARRIES an
    account reference, where the mode comes from ``identity.sandbox`` as set by
    ``enrich_identity_with_account``. Forcing ``sandbox=False`` at the create sites
    previously left the entire unit suite byte-identical, because nothing anywhere
    drove ``identity.sandbox`` true.
    """

    @staticmethod
    def _adapter_modes(*, transport: Transport, account_sandbox: bool) -> MagicMock:
        from tests.factories import AccountFactory, AgentAccountAccessFactory

        with MediaBuyCreateEnv() as env:
            tenant, principal, product, _pricing = env.setup_media_buy_data()
            AccountFactory(tenant=tenant, account_id="acc_route", sandbox=account_sandbox)
            # Resolution enforces agent access; without the grant the request is
            # rejected before any adapter is constructed.
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                account_id="acc_route",
            )
            now = datetime.now(UTC)
            result = env.call_via(
                transport,
                # The wire shape a buyer sends: a constructed model does not survive
                # A2A/REST serialization, and the boundary resolves the dict identically.
                account={"account_id": "acc_route"},
                brand={"domain": "account-route.example.com"},
                packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                po_number="ACCOUNT-ROUTE-1",
            )
            assert not result.is_error, f"[{transport.value}] dispatch failed: {result.error!r}"
            return env.mock["adapter"]

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_sandbox_account_reference_builds_the_sandbox_adapter(self, integration_db, transport):
        assert_all_sandbox(
            self._adapter_modes(transport=transport, account_sandbox=True), context="create_media_buy (account ref)"
        )

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_live_account_reference_builds_the_live_adapter(self, integration_db, transport):
        """Negative control — 'always sandbox' would disable real bookings and still pass above."""
        assert_all_live(
            self._adapter_modes(transport=transport, account_sandbox=False), context="create_media_buy (account ref)"
        )


_CREATE_MEDIA_BUY_SCHEMA = "media-buy/create-media-buy-response.json"


def _create_and_read_marker(transport: Transport, *, account_sandbox: bool, dry_run: bool = False):
    """Create against an account of the given mode; return the real TransportResult.

    One body for both marker classes. The live path and the dry-run path build their
    response objects at different sites in ``_create_media_buy_impl`` — the dry-run branch
    returns early, so it cannot pick up a marker attached later — and each needs its own
    oracle. Sharing the setup keeps the only difference between them the thing under test.

    Returns the ``TransportResult``, not a bare value: callers assert presence via a
    direct wire-dict key check and absence via
    ``tests.harness.assertions.assert_wire_omits_unset``, which additionally validates
    the full response against the pinned schema — catching an explicit ``"sandbox":
    null`` as a TYPE violation (the schema declares the field boolean, no null variant),
    not just a hand-listed key absence.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    with MediaBuyCreateEnv(dry_run=dry_run) as env:
        tenant, principal, product, _pricing = env.setup_media_buy_data()
        AccountFactory(tenant=tenant, account_id="acc_marker", sandbox=account_sandbox)
        AgentAccountAccessFactory(
            tenant_id=tenant.tenant_id, principal_id=principal.principal_id, account_id="acc_marker"
        )
        now = datetime.now(UTC)
        result = env.call_via(
            transport,
            account={"account_id": "acc_marker"},
            brand={"domain": "marker.example.com"},
            packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            po_number=f"MARKER-{'DRY-' if dry_run else ''}{transport.value}",
        )
        assert not result.is_error, f"dispatch failed: {result.error!r}"
        assert result.wire_response is not None, "no wire response captured — this assertion would grade nothing"
        return result


class TestSandboxMarkerReachesTheBuyer:
    """``sandbox: true`` on the create response — the half the buyer actually reads.

    Routing a sandbox request to the mock keeps the buyer's money safe; the marker is
    what lets them tell a simulated booking from a real one. Without it a create against
    a sandbox account is indistinguishable on the wire from one that moved real budget.

    This had no oracle at all: deleting ``response.sandbox = True`` left every
    sandbox-selected test green, while the BDD scenarios that specify the marker are
    dormant. For a change whose standard is that both halves are mutation-graded, the
    ungraded half was the one facing the buyer.

    Asserted on ``wire_response`` — the serialized bytes — not the typed payload:
    grading the model would only prove it round-trips, and a transport that dropped the
    field on serialization would still look correct.

    AdCP 3.1.1 ``sandbox.mdx``: "Sellers SHOULD include ``sandbox: true`` in success
    responses when processing a sandbox account request." Absent (not ``false``) is the
    correct encoding for a live response — the obligation is to include it when true.
    """

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_sandbox_create_is_marked_on_the_wire(self, integration_db, transport):
        result = _create_and_read_marker(transport, account_sandbox=True)
        # Symmetric with the negative arm, which schema-validates via
        # assert_wire_omits_unset: a marker on an otherwise schema-invalid response
        # is not a passing wire.
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(_CREATE_MEDIA_BUY_SCHEMA, result.wire_response)
        assert result.wire_response.get("sandbox") is True, (
            f"[{transport.value}] a create against a sandbox account carried no sandbox marker; "
            "the buyer cannot distinguish a simulated booking from one that moved real budget"
        )

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_live_create_is_not_marked(self, integration_db, transport):
        """Negative control — 'always mark' would pass above and mislabel real bookings."""
        result = _create_and_read_marker(transport, account_sandbox=False)
        # assert_wire_omits_unset also validates the full response against the pinned
        # schema, which types sandbox boolean with no null variant — so an explicit
        # "sandbox": null (which .get() cannot distinguish from absence) fails as a
        # schema violation, not just a hand-listed key check.
        assert_wire_omits_unset(result, schema=_CREATE_MEDIA_BUY_SCHEMA, absent_paths=["sandbox"], transport=transport)


class TestSandboxMarkerOnTheDryRunBranch:
    """The dry-run early return is a THIRD response-construction site, and had no oracle.

    ``_create_media_buy_impl`` builds its success response at three places: the adapter
    path, the value the adapter path carries forward, and the dry-run branch, which
    returns immediately and so cannot inherit a marker attached downstream. The class
    above grades the first two. Replacing the dry-run branch's
    ``sandbox=True if identity.sandbox else None`` with a bare ``None`` left
    ``tests/unit -k "dry_run or sandbox"`` at 86 passed — no test combined a sandbox
    account with dry_run, so the branch this change added was ungraded.

    Dry run and sandbox are different axes and the spec says so (``sandbox.mdx``
    §"Sandbox vs dry run"): dry run validates without booking, sandbox is a persistent
    simulated environment. A buyer may use both at once, and when they do the response
    still has to say which one it is.

    REST ONLY, deliberately. The sibling class above runs all three transports; this one
    cannot, because the harness has no way to put a request into dry-run mode over
    in-process MCP or A2A. ``x-dry-run`` is injected by the e2e REST dispatcher alone
    (``tests/harness/dispatchers.py``), and ``_run_mcp_client`` builds its header dict
    with only ``x-adcp-auth``/``x-adcp-tenant`` and patches ``get_http_headers`` in two
    modules that do not include ``testing_hooks`` — the seam that reads the flag into
    ``testing_ctx``. So ``MediaBuyCreateEnv(dry_run=True)`` dispatched over MCP or A2A
    takes the ordinary adapter path and picks the marker up from a DIFFERENT site.
    Parametrizing those two transports here was tried and passes vacuously: mutating this
    branch to ``sandbox=None`` reddens ``[rest]`` and leaves ``[mcp]``/``[a2a]`` green.
    A test that cannot fail is worse than an absent one, so they are not listed. Closing
    the harness gap is tracked separately — it is shared infrastructure and would change
    what every dry-run test in the repo actually exercises.
    """

    def test_dry_run_against_a_sandbox_account_is_still_marked(self, integration_db):
        result = _create_and_read_marker(Transport.REST, account_sandbox=True, dry_run=True)
        # Symmetric with the negative arm below, which schema-validates via
        # assert_wire_omits_unset. The dry-run branch builds its own response, so the
        # rest of that response needs grading here too, not just the marker.
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(_CREATE_MEDIA_BUY_SCHEMA, result.wire_response)
        assert result.wire_response.get("sandbox") is True, (
            "a dry-run create against a sandbox account carried no sandbox marker; the "
            "early-return branch builds its own response and must set it there"
        )

    def test_dry_run_against_a_live_account_is_not_marked(self, integration_db):
        """Negative control — marking every dry run would pass above and misreport the mode."""
        result = _create_and_read_marker(Transport.REST, account_sandbox=False, dry_run=True)
        # Schema-validated: an explicit "sandbox": null fails as a type violation
        # (boolean, no null variant), not just a hand-listed absence check.
        assert_wire_omits_unset(
            result, schema=_CREATE_MEDIA_BUY_SCHEMA, absent_paths=["sandbox"], transport=Transport.REST
        )
