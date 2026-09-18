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
import os
from unittest.mock import patch

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.harness.client import AdCPTestClient
from tests.harness.transport import NO_IDENTITY_OVERRIDE, E2EConfig, Transport


def _assert_success_equivalent(via, client_result) -> None:
    """Shared success-path equivalence assertion ``env.call_via`` and ``AdCPTestClient(env).call`` must agree on
    success, transport tag, and the exact wire-visible response body.
    Module-level so both ``TestEnvVsClientEquivalence`` (in-process) and
    ``TestEnvVsClientEquivalenceE2E`` (live-stack) share one assertion
    instead of duplicating it (CLAUDE.md DRY invariant).
    """
    assert via.is_success, via.error
    assert client_result.is_success, client_result.error
    assert via.envelope.get("transport") == client_result.envelope.get("transport")
    assert via.wire_response == client_result.wire_response


def _assert_error_equivalent(via, client_result, code: str, *, recovery: str) -> None:
    """Shared error-path equivalence assertion — see ``_assert_success_equivalent``.

    ``recovery`` is passed EXPLICITLY rather than left to ``assert_wire_error``'s
    CODE_TABLE default. The default is derived from the same table production
    emits from, so a drift in that table would move the expectation and the
    emission together and this assertion would stay green through it; the literal
    is the pinned spec's classification, checked independently.
    """
    from src.core.errors.codes import CODE_TABLE

    assert via.is_error, "env.call_via unexpectedly succeeded"
    assert client_result.is_error, "AdCPTestClient.call unexpectedly succeeded"
    via.assert_wire_error(code, recovery=recovery)
    client_result.assert_wire_error(code, recovery=recovery)
    # ADR-010: the buyer-facing sentence is a read-only CODE_TABLE property, so it
    # is asserted by identity with the table rather than against a literal.
    assert via.wire_error_envelope["adcp_error"]["message"] == CODE_TABLE[code].message, (
        f"adcp_error.message={via.wire_error_envelope['adcp_error']['message']!r}, "
        f"expected the first-party sentence {CODE_TABLE[code].message!r}"
    )
    assert via.wire_error_envelope == client_result.wire_error_envelope
    # The transport envelope too, not just the wire body: the two paths held
    # SEPARATE copies of each transport's error unwrap, which is how the derived
    # `status` came to exist on the dispatcher path and not on the client path
    # while every wire_error_envelope assertion stayed green. Comparing the
    # envelope makes that divergence observable here (wire-grading remediation).
    assert via.envelope == client_result.envelope, (
        f"env.call_via and AdCPTestClient.call disagree on the error envelope: "
        f"{via.envelope!r} vs {client_result.envelope!r}"
    )


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


def _live_e2e_config() -> E2EConfig | None:
    """Detect a live Docker e2e stack (nginx + adcp-server + postgres) started via
    ``make test-stack-up`` / ``scripts/test-stack.sh`` / ``./run_all_tests.sh``.

    Reads ``ADCP_SALES_PORT`` / ``POSTGRES_PORT`` — the host-published ports
    ``scripts/test-stack.sh`` writes to ``.test-stack.env`` — and health-checks
    the nginx proxy before trusting it, mirroring ``tests/bdd/conftest.py``'s
    ``e2e_stack()`` fixture. Returns ``None`` (never raises/skips here) when no
    stack is detected or it fails its health check; the caller decides whether
    that means "skip this class" (this module) or "hard error" (BDD's
    explicitly-requested ``e2e_rest`` transport).

    The live server's OWN database is named ``adcp`` (``docker-compose.e2e.yml``
    ``adcp-server`` service ``DATABASE_URL``) — NOT ``adcp_test``, the separate
    database ``scripts/test-stack.sh`` provisions on the SAME Postgres purely for
    host-run ``tox -e integration``/``bdd`` suites (see that script's ``cmd_up``
    and the ``tox.ini`` ``[testenv:e2e]`` comment "E2E talks to the SERVER's
    database (/adcp), not the suite DB (/adcp_test)"). Pointing factory writes at
    the wrong database would make them invisible to the live server this test
    dispatches HTTP requests to.
    """
    import httpx

    mcp_port = os.environ.get("ADCP_SALES_PORT")
    pg_port = os.environ.get("POSTGRES_PORT")
    if not mcp_port or not pg_port:
        return None
    base_url = f"http://localhost:{mcp_port}"
    try:
        resp = httpx.get(f"{base_url}/health", timeout=5)
        resp.raise_for_status()
    except Exception:
        return None
    postgres_url = f"postgresql://adcp_user:secure_password_change_me@localhost:{pg_port}/adcp"
    return E2EConfig(base_url=base_url, postgres_url=postgres_url)


def _reset_principal_token_sequence(suffix: str) -> None:
    """Reset ``PrincipalFactory``'s ``access_token`` sequence before entering an
    e2e-mode env, so every token this process mints for *this test* is
    collision-proof against a prior run's leftover rows.

    ``PrincipalFactory.access_token``'s default (``Sequence(lambda n: f"token_{n:08d}")``)
    is a per-PROCESS counter starting at 0 — harmless against the per-test,
    always-fresh ``integration_db``, but the live e2e stack's ``adcp``
    database is NOT wiped between separate pytest invocations, so a fresh
    process's counter restarting at 0 collides with an EARLIER run's
    committed ``token_00000000`` row on ``principals_access_token_key`` — a
    collision that happens INSIDE ``BaseTestEnv.__enter__``'s e2e auto-seed
    (``_seed_identity``), before any test-body code gets a chance to
    intervene. *suffix* is already unique per test (the same one used for
    tenant_id), so reseeding the counter from it makes the collision
    astronomically unlikely without a destructive reset of the live
    server's database.
    """
    from tests.factories import PrincipalFactory

    PrincipalFactory.reset_sequence(int(suffix, 16) % 10_000_000, force=True)


@pytest.fixture(scope="module")
def e2e_live_config() -> E2EConfig:
    """Live E2EConfig for ``TestEnvVsClientEquivalenceE2E``, or a clean skip.

    Skipping (not failing) keeps ``tox -e integration``'s "no Docker app stack
    needed" contract intact for every OTHER test in this file/env — only this
    one class requires the full stack, and only when it actually runs.
    """
    config = _live_e2e_config()
    if config is None:
        pytest.skip(
            "No live e2e stack detected (ADCP_SALES_PORT/POSTGRES_PORT unset, or "
            f"http://localhost:{os.environ.get('ADCP_SALES_PORT')}/health unreachable) — "
            "start one with `make test-stack-up` (or `./run_all_tests.sh`) to run "
            "TestEnvVsClientEquivalenceE2E."
        )
    return config


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
            client = AdCPTestClient(env)
            result = client.call("list_accounts", {}, Transport.REST, credential={})

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
            via = env.call_via(Transport.MCP, credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.MCP, credential={})

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
            via = env.call_via(Transport.A2A, credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.A2A, credential={})

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
            via = env.call_via(Transport.REST, credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.REST, credential={})

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


@pytest.mark.integration
@pytest.mark.requires_db
class TestEnvVsClientEquivalenceE2E:
    """The remaining half of "all six transport families" (split off this file which proved MCP/A2A/REST — the three
    IN-PROCESS families — only): the three LIVE families, E2E_REST/E2E_MCP/
    E2E_A2A, dispatched over real HTTP through nginx to a Docker stack.

    Same contract as ``TestEnvVsClientEquivalence`` above — ``env.call_via(T,
    ...)`` and ``AdCPTestClient(env).call(..., T)`` must dispatch the same
    production code and agree byte-for-byte on the wire — but every hop is
    real: real ``httpx``/``fastmcp.Client``/A2A JSON-RPC request, a real
    resolver reading the request headers, a real live-server response. Before
    this, ``Transport.E2E_MCP``/``Transport.E2E_A2A`` had ZERO callers
    anywhere against a live stack (confirmed: every existing reference is
    either the in-process E2E_REST-only BDD ledger or a mocked-httpx unit
    test in ``tests/harness/test_client.py`` — see
    ``McpE2EDispatcher``/``A2AE2EDispatcher``'s own docstrings in
    ``tests/harness/dispatchers.py``), so this is new live-wire coverage, not
    a duplicate of the in-process class.

    Honest caveat on what "equivalence" proves per transport, read from
    ``tests/harness/dispatchers.py``: ``RestE2EDispatcher`` (the
    ``env.call_via`` path) hand-rolls WRAP via each env's own
    ``build_rest_body``/``REST_ENDPOINT`` — genuinely independent of the
    generic client's address-table-driven ``_wrap_rest``, so
    ``test_e2e_rest_success_and_error_equivalence`` is a real two-
    independent-paths proof, the same kind ``TestEnvVsClientEquivalence``
    does in-process. ``McpE2EDispatcher``/``A2AE2EDispatcher``, by contrast,
    both delegate straight to the SAME ``_dispatch_core`` function
    ``AdCPTestClient.call()`` calls (their docstrings say so explicitly —
    a deliberate DRY choice, not an oversight), so their "equivalence" is
    guaranteed by construction, not proven per-test. Their real value here
    is different: this is the first time either DELIVER path
    (``_deliver_e2e_mcp``/``_deliver_e2e_a2a``) has run against a live
    server at all, and ``test_e2e_a2a_success_and_error_equivalence``
    caught a genuine bug on its first run — ``_deliver_e2e_a2a`` never sent
    the ``A2A-Version`` header the real JSON-RPC route requires
    (``a2a.utils.version_validator.validate_version``), so every live
    E2E_A2A call 400'd with ``VersionNotSupportedError`` before reaching
    ``AdCPRequestHandler`` (fixed alongside this test, same commit).

    Requires the ``e2e_live_config`` fixture (module-scoped, health-checked)
    — skips this whole class when no live stack is detected, so
    ``tox -e integration``'s no-Docker-app-stack contract is unaffected for
    every other test in this env; start a stack with `make test-stack-up` (or
    `./run_all_tests.sh`, which runs the full suite including this class) to
    exercise it.

    Tenant/product identifiers are ``uuid``-suffixed (unlike the in-process
    class's fixed literals) because ``e2e_live_config`` points factories at
    the live server's persistent ``adcp`` database — NOT the per-test
    ``integration_db`` these tests also request (kept only for the
    ``DATABASE_URL``-requires-Postgres skip semantics other tests in this
    file rely on) — so a second run against the SAME stack must not collide
    on a previous run's rows.
    """

    def test_e2e_rest_success_and_error_equivalence(self, integration_db, e2e_live_config):
        import uuid

        from tests.factories import PricingOptionFactory, ProductFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        suffix = uuid.uuid4().hex[:8]
        _reset_principal_token_sequence(suffix)
        with ProductEnv(tenant_id=f"ev-e2erest-s-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            tenant, _principal = env.setup_default_data()
            product = ProductFactory(tenant=tenant, product_id=f"prod_ev_e2erest_{suffix}")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.E2E_REST, brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.E2E_REST)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id=f"ev-e2erest-e-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            via = env.call_via(Transport.E2E_REST, credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.E2E_REST, credential={})

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")

    def test_e2e_mcp_success_and_error_equivalence(self, integration_db, e2e_live_config):
        import uuid

        from tests.factories import PricingOptionFactory, ProductFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        suffix = uuid.uuid4().hex[:8]
        _reset_principal_token_sequence(suffix)
        with ProductEnv(tenant_id=f"ev-e2emcp-s-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            tenant, _principal = env.setup_default_data()
            product = ProductFactory(tenant=tenant, product_id=f"prod_ev_e2emcp_{suffix}")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.E2E_MCP, tool_name="get_products", brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.E2E_MCP)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id=f"ev-e2emcp-e-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            via = env.call_via(Transport.E2E_MCP, tool_name="list_accounts", credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.E2E_MCP, credential={})

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")

    def test_e2e_a2a_success_and_error_equivalence(self, integration_db, e2e_live_config):
        import uuid

        from tests.factories import PricingOptionFactory, ProductFactory
        from tests.harness.account_list import AccountListEnv
        from tests.harness.product import ProductEnv

        suffix = uuid.uuid4().hex[:8]
        _reset_principal_token_sequence(suffix)
        with ProductEnv(tenant_id=f"ev-e2ea2a-s-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            tenant, _principal = env.setup_default_data()
            product = ProductFactory(tenant=tenant, product_id=f"prod_ev_e2ea2a_{suffix}")
            PricingOptionFactory(product=product)

            via = env.call_via(Transport.E2E_A2A, tool_name="get_products", brief="video ads")
            client_result = AdCPTestClient(env).call("get_products", {"brief": "video ads"}, Transport.E2E_A2A)

        _assert_success_equivalent(via, client_result)

        with AccountListEnv(tenant_id=f"ev-e2ea2a-e-{suffix}", principal_id="p1", e2e_config=e2e_live_config) as env:
            via = env.call_via(Transport.E2E_A2A, tool_name="list_accounts", credential={})
            client_result = AdCPTestClient(env).call("list_accounts", {}, Transport.E2E_A2A, credential={})

        _assert_error_equivalent(via, client_result, "AUTH_MISSING", recovery="correctable")
