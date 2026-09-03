"""Wire-path tests: ``account`` reaches get_products' adapter dispatch.

The ``account`` reference is enriched onto ``identity`` at the MCP/A2A/REST boundary via
``enrich_identity_with_account`` (mirroring ``get_media_buy_delivery``), and it is
``identity.sandbox`` — not ``req.account`` — that decides the ``sandbox=`` mode passed to
``get_adapter`` when annotating pricing options. On the ``req.account`` half of that
split, ``TestGetProductsRequestAccountFieldWiring`` below is authoritative; this
docstring does not restate it. Before this wiring, no transport declared ``account`` on
``get_products`` even though the pinned 3.1.1 ``get-products-request.json`` declares the
field — so a sandbox account always built the tenant's real adapter (``sandbox=False``
was hard-wired). Like ``test_create_media_buy_account_wire.py``, this proves two separate
things:

1. ``account`` crosses the wire on every transport that declares it (a bogus account
   is rejected with ``ACCOUNT_NOT_FOUND``, which can only happen if the reference
   reached ``enrich_identity_with_account``).
2. The resolved mode reaches ``get_adapter``'s ``sandbox=`` keyword — the identity-keyed
   forwarding site this PR's structural guard now checks
   (``tests/unit/test_architecture_get_adapter_sandbox.py::IDENTITY_KEYED_SITES``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from tests.factories import (
    AccountFactory,
    AgentAccountAccessFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    TenantFactory,
)
from tests.harness.assertions import assert_rejected, assert_wire_omits_unset
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport, TransportResult
from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox

if TYPE_CHECKING:
    # Type-only: keeps the production types off this test module's import path
    # while still letting mypy check the interception signatures below.
    from adcp import GetProductsRequest, GetProductsResponse
    from adcp.types import AccountReference

    from src.core.resolved_identity import ResolvedIdentity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_GET_PRODUCTS_SCHEMA = "media-buy/get-products-response.json"

# Mirrors test_creative_sync_transport.py's ALL_TRANSPORTS — IMPL is in-process by
# definition and never carries a wire (see ImplDispatcher's docstring in
# tests/harness/dispatchers.py); A2A/REST/MCP all capture the real wire for ProductEnv.
ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]


class TestGetProductsAccountWirePassthrough:
    """``account`` reaches enrich_identity_with_account through every wrapper."""

    def _run_account_wire(self, transport: Transport) -> None:
        with ProductEnv() as env:
            tenant = TenantFactory(tenant_id=f"t_gp_wire_{transport.value}")
            PrincipalFactory(tenant=tenant, principal_id=f"p_gp_wire_{transport.value}")
            env.set_policy_approved()
            env.set_ranking_disabled()

            result = env.call_via(
                transport,
                brief="video ads",
                account={"account_id": f"no-such-account-{transport.value}"},
            )

        assert_rejected(result, code="ACCOUNT_NOT_FOUND")

    def test_mcp_wire_forwards_account(self, integration_db):
        """MCP wrapper declares + forwards ``account`` -> boundary resolves it."""
        self._run_account_wire(Transport.MCP)

    def test_a2a_wire_forwards_account(self, integration_db):
        """A2A raw function forwards ``account`` -> boundary resolves it."""
        self._run_account_wire(Transport.A2A)

    def test_rest_wire_forwards_account(self, integration_db):
        """REST ``GetProductsBody.account`` + route passthrough -> boundary resolves it."""
        self._run_account_wire(Transport.REST)


class TestGetProductsAccountReferenceRoutesTheAdapter:
    """The mode an account reference RESOLVES TO reaches get_adapter's sandbox= kwarg.

    ``get_adapter`` is imported lazily inside ``_get_products_impl``, so the patch
    target is the source module (``adapter_helpers.get_adapter``), not ``products.py``
    — see ``tests/harness/product.py``'s harness docstring on lazy-import patch targets.
    """

    @staticmethod
    def _adapter_modes(*, transport: Transport, account_sandbox: bool) -> MagicMock:
        mode = "sbx" if account_sandbox else "live"
        suffix = transport.value
        with patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter:
            mock_get_adapter.return_value.get_supported_pricing_models.return_value = []

            with ProductEnv(tenant_id=f"t-gp-route-{mode}-{suffix}", principal_id=f"p-gp-route-{mode}-{suffix}") as env:
                tenant, principal = env.setup_default_data()
                env.set_policy_approved()
                env.set_ranking_disabled()

                product = ProductFactory(tenant=tenant, product_id=f"prod-gp-route-{mode}-{suffix}")
                PricingOptionFactory(product=product)

                AccountFactory(tenant=tenant, account_id="acc_gp_route", sandbox=account_sandbox)
                AgentAccountAccessFactory(
                    tenant_id=tenant.tenant_id,
                    principal_id=principal.principal_id,
                    account_id="acc_gp_route",
                )

                call_kwargs: dict[str, Any] = {
                    "brief": "video ads",
                    "account": {"account_id": "acc_gp_route"},
                }
                if transport is Transport.IMPL:
                    # ProductMixin.call_impl (tests/harness/_mixins.py) has no branch that
                    # replicates the boundary's account-enrichment step, so identity.sandbox
                    # would stay at its unenriched default regardless of account_sandbox.
                    # Mirrors TestGetProductsSandboxMarkerOnWire._dispatch's IMPL handling
                    # in this same file — enrich_identity_with_account is the exact
                    # production helper the real MCP/A2A/REST wrappers call before _impl
                    # runs.
                    from src.core.schema_helpers import to_account_reference
                    from src.core.transport_helpers import enrich_identity_with_account

                    call_kwargs["identity"] = enrich_identity_with_account(
                        env.identity_for(transport), to_account_reference(call_kwargs["account"])
                    )

                result = env.call_via(transport, **call_kwargs)
                assert not result.is_error, f"[{suffix}] dispatch failed: {result.error!r}"

            return mock_get_adapter

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_sandbox_account_reference_routes_to_the_sandbox_adapter(self, integration_db, transport):
        assert_all_sandbox(
            self._adapter_modes(transport=transport, account_sandbox=True), context="get_products (account ref)"
        )

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_live_account_reference_routes_to_the_live_adapter(self, integration_db, transport):
        """Negative control — 'always sandbox' would disable real credential reads and still pass above."""
        assert_all_live(
            self._adapter_modes(transport=transport, account_sandbox=False), context="get_products (account ref)"
        )


class TestGetProductsSandboxMarkerOnWire:
    """``GetProductsResponse.sandbox`` echoes the resolved account's mode on the real wire.

    Mirrors the sandbox-marker pattern in ``test_creative_sync_transport.py``:
    parametrized across every transport, asserting on ``result.wire_response`` where
    a real wire is captured (A2A/REST/MCP — ProductEnv's ``call_a2a``/``call_mcp`` drive
    the real ``AdCPRequestHandler``/FastMCP pipelines, and REST goes through a real
    ``TestClient`` route, so those dispatchers stash the literal wire body), and
    falling back to the parsed ``payload`` for IMPL, which is in-process by definition
    and never has a wire (see ``ImplDispatcher``'s docstring in
    ``tests/harness/dispatchers.py``). Pairs a positive case with a negative control so
    'always sandbox: true' can't pass silently.
    """

    @staticmethod
    def _dispatch(*, transport: Transport, account_sandbox: bool) -> TransportResult:
        mode = "sbx" if account_sandbox else "live"
        suffix = transport.value
        with ProductEnv(tenant_id=f"t-gp-marker-{mode}-{suffix}", principal_id=f"p-gp-marker-{mode}-{suffix}") as env:
            tenant, principal = env.setup_default_data()
            env.set_policy_approved()
            env.set_ranking_disabled()

            product = ProductFactory(tenant=tenant, product_id=f"prod-gp-marker-{mode}-{suffix}")
            PricingOptionFactory(product=product)

            AccountFactory(tenant=tenant, account_id="acc_gp_marker", sandbox=account_sandbox)
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                account_id="acc_gp_marker",
            )

            call_kwargs: dict[str, Any] = {
                "brief": "video ads",
                "account": {"account_id": "acc_gp_marker"},
            }
            if transport is Transport.IMPL:
                # IMPL calls _get_products_impl directly (ProductMixin.call_impl in
                # tests/harness/_mixins.py), which — unlike CreativeSyncEnv.call_impl —
                # has no branch that replicates the boundary's account-enrichment step,
                # so identity.sandbox would stay at its unenriched default regardless of
                # account_sandbox. enrich_identity_with_account is the exact production
                # helper the real MCP/A2A/REST wrappers call before _impl runs; invoking
                # it here (rather than reimplementing its logic) makes IMPL exercise the
                # same identity.sandbox _impl actually reads, matching the enrichment
                # CreativeSyncEnv.call_impl already performs for its own IMPL path.
                from src.core.schema_helpers import to_account_reference
                from src.core.transport_helpers import enrich_identity_with_account

                call_kwargs["identity"] = enrich_identity_with_account(
                    env.identity_for(transport), to_account_reference(call_kwargs["account"])
                )

            result = env.call_via(transport, **call_kwargs)
            assert not result.is_error, f"[{suffix}] dispatch failed: {result.error!r}"
            if transport in (Transport.A2A, Transport.REST, Transport.MCP):
                assert result.wire_response is not None, (
                    f"[{suffix}] {transport.value} must capture a real wire response — a regression "
                    "here would silently fall back to the weaker payload-only assertion below"
                )
            return result

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_sandbox_account_marks_the_response_on_the_wire(self, integration_db, transport):
        result = self._dispatch(transport=transport, account_sandbox=True)
        if result.wire_response is not None:
            from tests.helpers.pinned_schema import validate_against_pinned_schema

            validate_against_pinned_schema(_GET_PRODUCTS_SCHEMA, result.wire_response)
            assert result.wire_response.get("sandbox") is True, (
                f"[{transport.value}] sandbox-scoped get_products must carry sandbox: true on the wire, "
                f"got {result.wire_response.get('sandbox')!r}"
            )
        else:
            assert result.payload.sandbox is True, (
                f"[{transport.value}] sandbox-scoped get_products must carry sandbox: true, "
                f"got {result.payload.sandbox!r}"
            )

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_live_account_omits_the_marker(self, integration_db, transport):
        """Negative control — 'always sandbox: true' would pass the test above.

        Routed through ``assert_wire_omits_unset`` rather than a bare
        ``.get("sandbox") is None`` check: the pinned schema types ``sandbox`` as
        ``{"type": "boolean"}`` with no null variant, so a plain ``is None`` check
        can't distinguish an omitted key from an explicit (schema-violating) JSON
        ``null`` — the exact #1710/#1868 null-leak class this helper regression-tests.
        IMPL has no wire (see ``_dispatch``), so it falls back to the payload check —
        ``payload.sandbox is None`` is weaker (can't distinguish omission from null) but
        it is the only signal IMPL can carry.
        """
        result = self._dispatch(transport=transport, account_sandbox=False)
        if result.wire_response is not None:
            assert_wire_omits_unset(result, schema=_GET_PRODUCTS_SCHEMA, absent_paths=["sandbox"], transport=transport)
        else:
            assert result.payload.sandbox is None, (
                f"[{transport.value}] live-scoped get_products must omit the sandbox marker "
                f"(None, not False, per AdCP 3.1.1), got {result.payload.sandbox!r}"
            )


class TestGetProductsRequestAccountFieldWiring:
    """``req.account`` on the request built by ``create_get_products_request`` is
    actually SET from the input ``account`` reference at every caller.

    ``req.account`` itself has no production reader — enrichment happens from the raw
    ``account`` kwarg via ``enrich_identity_with_account``, not by reading ``req.account``
    back out (see ``create_get_products_request``'s docstring). This test does not claim
    otherwise. It pins the WIRING behind the schema-conformance comment at each
    ``create_get_products_request(...)`` call site — before this test, nothing reddened if
    ``account=account`` were silently dropped from any of them, even though the pinned
    3.1.1 ``get-products-request.json`` schema declares the field. The parametrization
    below names the transports it covers; no count is restated here, so a new call site
    cannot make this prose quietly wrong.

    Captures ``req`` by patching ``_get_products_impl`` at its SOURCE module
    (``src.core.tools.products``) with a side effect that records ``req.account`` and then
    delegates to the original — the same interception pattern already used in
    ``tests/harness/test_forward_compat_acceptance.py``'s ``TestDataPreservationE2E``.
    Transport.IMPL is intentionally excluded: ``ProductMixin.call_impl`` (in
    ``tests/harness/_mixins.py``) imports ``_get_products_impl`` via ``from ... import``
    at module load time, so patching the source module's attribute does not intercept
    that binding.
    """

    @staticmethod
    def _capture_req_account(*, transport: Transport) -> AccountReference | None:
        import src.core.tools.products as products_mod

        captured: dict[str, AccountReference | None] = {}
        original_impl = products_mod._get_products_impl

        async def capturing_impl(
            req: GetProductsRequest, identity: ResolvedIdentity | None = None
        ) -> GetProductsResponse:
            captured["account"] = req.account
            return await original_impl(req, identity)

        with ProductEnv(
            tenant_id=f"t-gp-reqacct-{transport.value}", principal_id=f"p-gp-reqacct-{transport.value}"
        ) as env:
            tenant, principal = env.setup_default_data()
            env.set_policy_approved()
            env.set_ranking_disabled()

            product = ProductFactory(tenant=tenant, product_id=f"prod-gp-reqacct-{transport.value}")
            PricingOptionFactory(product=product)

            AccountFactory(tenant=tenant, account_id="acc_gp_reqacct", sandbox=False)
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                account_id="acc_gp_reqacct",
            )

            with patch.object(products_mod, "_get_products_impl", side_effect=capturing_impl):
                result = env.call_via(
                    transport,
                    brief="video ads",
                    account={"account_id": "acc_gp_reqacct"},
                )
            assert not result.is_error, f"[{transport.value}] dispatch failed: {result.error!r}"

        return captured.get("account")

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST], ids=lambda t: t.value)
    def test_req_account_wired_from_input_account(self, integration_db, transport):
        account = self._capture_req_account(transport=transport)
        assert account is not None, f"[{transport.value}] req.account must be set from the input account reference"
        assert account.account_id == "acc_gp_reqacct", (
            f"[{transport.value}] req.account.account_id must equal the input account_id, got {account!r}"
        )
