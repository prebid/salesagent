"""Transport parity for AdCPTestClient, against a real database.

Lives in tests/integration/ rather than beside the rest of the client's tests in
tests/harness/test_client.py because these two need PostgreSQL: they drive REST
dispatch through get_rest_client() and seed real rows via the ORM factories.

tox's `unit` env collects `tests/unit/ tests/harness/` with DATABASE_URL unset --
"no database, no server", as its own description says -- so a db-requiring test
under tests/harness/ fails at fixture setup with a connection refusal, and no
other env collects that directory to catch it. Keeping the split by directory,
the way the rest of the suite does, is what makes the marker unnecessary.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.harness.client import AdCPTestClient
from tests.harness.transport import NO_IDENTITY_OVERRIDE, Transport
from tests.helpers.transport_parity import _assert_error_equivalent, _assert_success_equivalent


def _dispatch_via_env_pinning_one_dispatch_core(env, transport: Transport, tool: str, **kwargs):
    """``env.call_via(transport, **kwargs)``, pinning that it went through the
    ONE dispatch core — the single-dispatch invariant made runtime-observable.

    ``AdCPTestClient`` must be the implementation ``call_via``/``call_mcp``/
    ``call_a2a`` delegate to, not a peer beside them (beads). Structurally that is graded by
    ``tests/unit/test_architecture_harness_single_dispatch.py``; behaviorally
    it means a dispatch entering through the legacy ``env.call_via`` door and
    a dispatch entering through ``AdCPTestClient(env).call`` reach the server
    through the SAME function — ``tests.harness.client._dispatch_core`` —
    with the same tool identity, rather than through two independently
    maintained code paths that merely happen to agree today.

    Spying on the module attribute (rather than on the env) is what makes this
    deletion-sensitive: every consumer imports ``_dispatch_core`` lazily from
    ``tests.harness.client`` at call time (``dispatchers.py``'s E2E
    dispatchers do so today, and the base ``deliver_mcp``/``deliver_a2a`` must
    do the same), so an env that re-grows its own hand-rolled MCP/A2A path
    records zero calls here.

    Applies to CONVERTED envs only — an env that keeps a justified
    ``deliver_*`` override with bespoke routing (e.g. ``MediaBuyDualEnv``,
    which picks tool and parser from request content) is out of scope for this
    helper and is graded by the structural guard's shrink-only allowlist
    instead.
    """
    import tests.harness.client as client_module

    real_dispatch_core = client_module._dispatch_core
    seen: list[tuple[Transport, str]] = []

    def _spy(env_, transport_, tool_, payload_, identity_=NO_IDENTITY_OVERRIDE):
        seen.append((transport_, tool_))
        return real_dispatch_core(env_, transport_, tool_, payload_, identity_)

    with patch.object(client_module, "_dispatch_core", _spy):
        via = env.call_via(transport, **kwargs)

    assert seen == [(transport, tool)], (
        f"{type(env).__name__}.call_via({transport}) must dispatch through the one "
        f"tests.harness.client._dispatch_core with tool {tool!r} — recorded {seen!r} instead. "
        f"Two live dispatch mechanisms in the harness is exactly what the single-dispatch invariant "
        f"removes: BaseTestEnv.deliver_mcp/deliver_a2a delegate to the client's "
        f"core, and call_mcp/call_a2a are `deliver_*(...).payload` on the base only."
    )
    return via


# ``(module, class, tool, payload, transports)`` for the converted envs
# from a hand-written call_mcp/call_a2a pair to base-class delegation. Each
# one's current override is a bare ``return self._run_mcp_client(<tool>, <Model>,
# **kwargs)`` / ``_run_a2a_handler(...)`` — no request-content routing, no
# bespoke parser — and each one's tool resolves a pinned SDK response model via
# ``spec_response_model``, so the base's default ``response_parser`` covers it.
#
# Deliberately NOT here, each for a reason that is about the tool, not the
# conversion (both verified by running the equivalence half in isolation
# against a real database while authoring this):
#
# * ``CapabilitiesEnv``/``get_adcp_capabilities`` — its response carries a
#   ``last_updated`` timestamp minted per call, so a byte-exact wire comparison
#   between two dispatches is non-deterministic by construction. Pinning
#   equivalence for it needs a clock freeze, not a looser assertion.
_NEWLY_DELEGATING_ENVS: list[tuple[str, str, str, dict, tuple[Transport, ...]]] = [
    ("tests.harness.account_list", "AccountListEnv", "list_accounts", {}, (Transport.MCP, Transport.A2A)),
    ("tests.harness.creative_list", "CreativeListEnv", "list_creatives", {}, (Transport.MCP, Transport.A2A)),
    # list_tasks is MCP-only (no A2A skill, no REST route). It joins this list because
    # #2201 made its response conformant: it previously had a deliver_mcp override, and a
    # test HERE asserting the core's pinned parse still failed. That test went RED ("DID
    # NOT RAISE") the moment the response was fixed, which is what it was built to do, so
    # the override, its allowlist row and the test itself were deleted together. This row
    # is the positive replacement: deleting a "cannot join" test without adding the "does
    # join, equivalently" one would leave the dispatch ungraded rather than graded.
    ("tests.harness.task_management", "TaskManagementEnv", "list_tasks", {}, (Transport.MCP,)),
]

_DELEGATION_CASES = [
    pytest.param(module, cls, tool, payload, transport, id=f"{cls}-{transport.value}")
    for module, cls, tool, payload, transports in _NEWLY_DELEGATING_ENVS
    for transport in transports
]


def _seed_seller(env) -> None:
    """Ensure this env's TENANT row exists, and nothing else.

    AUTH_MISSING answers "no credential was presented", which the resolver only reaches
    once it knows which seller the request addresses. With no tenant it refuses
    TENANT_UNDEFINED first, and the assertion then grades the wrong refusal.

    The tenant ALONE: ``setup_default_data`` also creates the env's default principal, and
    ``principals.token_hash`` is globally unique, so seeding one here collides with the
    principal the success leg of the same test already committed.
    """
    from sqlalchemy import select

    from src.core.database.models import Tenant
    from tests.factories import TenantFactory

    if env.get_session().scalars(select(Tenant).filter_by(tenant_id=env.tenant_id)).first() is None:
        TenantFactory(tenant_id=env.tenant_id)
        env._commit_factory_data()


@pytest.mark.integration
@pytest.mark.requires_db
class TestClientCrossTransportConsistency:
    """AdCPTestClient's own promise: the SAME call() drives MCP/A2A/REST identically
    (CLAUDE.md Pattern #7 "Transport parity") — using tests.harness.product.ProductEnv
    (the IntegrationEnv/real-DB variant) so REST dispatch (which needs
    get_rest_client()) is available alongside MCP/A2A.

    Renamed from "TestClientTransportParity" (Finding 6):
    these tests compare AdCPTestClient against ITSELF across three transports —
    real, valuable coverage, but NOT the env.call_via() vs
    AdCPTestClient(env).call() equivalence the old name implied. That
    comparison — the actual migration precondition — is
    TestEnvVsClientEquivalence below.
    """

    def test_get_products_identical_across_all_three_transports(self, integration_db):
        from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id="client-parity", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="client-parity", subdomain="client-parity")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant, product_id="prod_parity")
            PricingOptionFactory(product=product)

            client = AdCPTestClient(env)

            mcp_result = client.call("get_products", {"brief": "video ads"}, Transport.MCP)
            a2a_result = client.call("get_products", {"brief": "video ads"}, Transport.A2A)
            rest_result = client.call("get_products", {"brief": "video ads"}, Transport.REST)

        assert mcp_result.is_success, mcp_result.error
        assert a2a_result.is_success, a2a_result.error
        assert rest_result.is_success, rest_result.error

        # payload is the pinned GetProductsResponse model on every transport —
        # attribute access, not subscripting.
        mcp_ids = {p.product_id for p in mcp_result.payload.products}
        a2a_ids = {p.product_id for p in a2a_result.payload.products}
        rest_ids = {p.product_id for p in rest_result.payload.products}

        assert mcp_ids == {"prod_parity"}
        assert mcp_ids == a2a_ids == rest_ids

    def test_rest_unauthenticated_dispatch_surfaces_auth_required(self, integration_db):
        """No ``Authorization`` header at all is AUTH_MISSING, not the deprecated alias.

        AdCP 3.1.1 ``enums/error-code.json`` splits the old ``AUTH_REQUIRED``:
        AUTH_MISSING is "No credentials were presented. Sellers MUST return this
        code when no ``Authorization`` header was included in the request";
        AUTH_INVALID is credentials presented and rejected; ``AUTH_REQUIRED``
        itself is "**Deprecated** — use ``AUTH_MISSING`` … Retained as a
        backward-compatible alias during the 3.x deprecation window". This
        dispatch sends no header, so it is the MUST above.
        """
        from src.core.errors.codes import CODE_TABLE

        with BareIntegrationEnv(tenant_id="client-parity-noauth", principal_id="p1") as env:
            _seed_seller(env)
            client = AdCPTestClient(env)
            result = client.call("list_accounts", {}, Transport.REST, credential=env.credential(token=None))

        assert result.is_error
        result.assert_wire_error("AUTH_MISSING", recovery="correctable")

        # ADR-010: the buyer-facing sentence is a read-only CODE_TABLE property.
        # Asserted by identity with the table, then kept non-vacuous by pinning
        # that the OTHER side of the 3.1.1 split says something different — a
        # seller that collapsed "none presented" into "presented and rejected"
        # would satisfy an equality-only check against whichever it emitted.
        adcp_error = result.wire_error_envelope["adcp_error"]
        assert adcp_error["message"] == CODE_TABLE["AUTH_MISSING"].message, (
            f"adcp_error.message={adcp_error['message']!r}, expected the first-party sentence "
            f"{CODE_TABLE['AUTH_MISSING'].message!r}"
        )
        assert CODE_TABLE["AUTH_MISSING"].message != CODE_TABLE["AUTH_INVALID"].message, (
            "the two halves of the 3.1.1 auth split must not advise the buyer identically"
        )


@pytest.mark.integration
@pytest.mark.requires_db
class TestEnvVsClientEquivalence:
    """The migration precondition (Finding 6): prove
    ``env.call_via(T, ...)`` and ``AdCPTestClient(env).call(..., T)`` dispatch
    the same production code, on the SAME env, and produce an IDENTICAL
    wire-visible contract — for a success case AND an error case, per
    in-process transport family (MCP/A2A/REST). Until every one of these
    holds, migrating the 33 existing envs onto the client (tracked as the
    roadmap follow-up, out of scope here) is unsafe: a silent divergence
    between the two entry points is exactly the class of regression that
    migration must not introduce.

    The error direction dispatches a REAL unauthenticated request (not a
    mock) through each transport's error mapper — ``unwrap_mcp_error`` /
    ``unwrap_a2a_error`` / ``unwrap_rest_error`` in
    ``tests/harness/client.py`` — so it is deletion-sensitive: removing
    ``wire_error_envelope=`` from ``unwrap_mcp_error`` makes
    ``test_mcp_success_and_error_equivalence`` fail with "no
    wire_error_envelope was captured" (verified by hand during this change).
    """

    def test_mcp_success_and_error_equivalence(self, integration_db):
        from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id="ev-mcp-s", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="ev-mcp-s", subdomain="ev-mcp-s")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant, product_id="prod_ev_mcp")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.MCP, brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.MCP)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id="ev-mcp-e", principal_id="p1") as env:
            _seed_seller(env)
            via = env.call_via(Transport.MCP, credential=env.credential(token=None))
            client_result = AdCPTestClient(env).call(
                "list_accounts", {}, Transport.MCP, credential=env.credential(token=None)
            )

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")

    def test_a2a_success_and_error_equivalence(self, integration_db):
        from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id="ev-a2a-s", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="ev-a2a-s", subdomain="ev-a2a-s")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant, product_id="prod_ev_a2a")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.A2A, brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.A2A)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id="ev-a2a-e", principal_id="p1") as env:
            _seed_seller(env)
            via = env.call_via(Transport.A2A, credential=env.credential(token=None))
            client_result = AdCPTestClient(env).call(
                "list_accounts", {}, Transport.A2A, credential=env.credential(token=None)
            )

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")

    def test_rest_success_and_error_equivalence(self, integration_db):
        from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id="ev-rest-s", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="ev-rest-s", subdomain="ev-rest-s")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant, product_id="prod_ev_rest")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.REST, brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.REST)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id="ev-rest-e", principal_id="p1") as env:
            _seed_seller(env)
            via = env.call_via(Transport.REST, credential=env.credential(token=None))
            client_result = AdCPTestClient(env).call(
                "list_accounts", {}, Transport.REST, credential=env.credential(token=None)
            )

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")


@pytest.mark.integration
@pytest.mark.requires_db
class TestNewlyDelegatingEnvEquivalence:
    """``TestEnvVsClientEquivalence`` extended to the converted envs
    (the task change-set §5: "EXTEND its coverage to the
    newly-delegating envs").

    ``TestEnvVsClientEquivalence`` above proves the two entry points agree for
    ONE env (``ProductEnv``/``AccountListEnv``) per transport family. That is
    the precondition for migrating envs onto the client; this class is the
    grader for the migration itself, and it asserts one thing more than
    equivalence: that the two entry points are no longer two paths at all.

    Both halves matter and neither is redundant:

    * ``_dispatch_via_env_pinning_one_dispatch_core`` pins the MECHANISM —
      ``env.call_via`` must reach the server through
      ``tests.harness.client._dispatch_core``. Today it does not: the
      in-process ``McpDispatcher``/``A2ADispatcher``
      (``tests/harness/dispatchers.py``) call ``env.call_mcp``/``env.call_a2a``,
      each env's own hand-written copy, so this assertion is RED until the
      base-class delegation lands.
    * ``_assert_success_equivalent`` pins the RESULT — success, transport tag,
      and the exact wire-visible body must still match after the conversion,
      so a conversion that quietly changes what a buyer sees fails here rather
      than in whatever downstream scenario happens to notice first.
    """

    @pytest.mark.parametrize("module,cls_name,tool,payload,transport", _DELEGATION_CASES)
    def test_converted_env_dispatch_is_the_client_dispatch(
        self, integration_db, module, cls_name, tool, payload, transport
    ):
        env_cls = getattr(importlib.import_module(module), cls_name)
        # Tenant ids are per-case so two parametrized rows never share a row set
        # (``setup_default_data`` is get-or-create, keyed on tenant_id).
        tenant_id = f"nd-{tool.replace('_', '-')}-{transport.value}"

        with env_cls(tenant_id=tenant_id, principal_id="p1") as env:
            env.setup_default_data()

            via = _dispatch_via_env_pinning_one_dispatch_core(env, transport, tool, **payload)
            client_result = AdCPTestClient(env).call(tool, dict(payload), transport)

        _assert_success_equivalent(via, client_result)
