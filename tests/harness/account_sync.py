"""AccountSyncEnv — integration test environment for _sync_accounts_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, all upsert/deactivate logic (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with AccountSyncEnv() as env:
            tenant, principal = env.setup_default_data()

            response = await env.call_impl_async(
                accounts=[{"brand": {"domain": "acme.com"}, "operator": "acme.com", "billing": "operator"}]
            )
            assert len(response.accounts) == 1

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import)

"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas.account import ListAccountsResponse, SyncAccountsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import AccountListDispatchMixin
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.harness.transport import DeliverResult


class AccountSyncEnv(AccountListDispatchMixin, IntegrationEnv):
    """Integration test environment for _sync_accounts_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository -> real DB writes
    - Real upsert, deactivate_missing, grant_access logic

    Both sync and async call patterns are supported:
    - call_impl() / call_a2a(): sync wrappers for BDD steps and dispatchers
      (``call_a2a``/``call_mcp`` AND the ``deliver_*`` pair beneath them are all
      the base's, defined once; this env overrides neither)
    - call_impl_async(): for @pytest.mark.asyncio tests

    Constructor accepts ``supported_billing`` to configure billing policy
    on the identity (BR-RULE-059).

    Dispatches TWO verbs: ``sync_accounts`` (primary) and ``list_accounts``,
    selected by a request-type discriminator (``AccountListDispatchMixin.
    is_list_request``). Scenarios that need to read accounts back therefore
    dispatch list over the real transport instead of calling
    ``_list_accounts_impl`` directly, which would grade ``_impl`` on all four
    transports.
    """

    # Dispatch declaration for the PRIMARY verb: the base owns call_mcp/call_a2a
    # AND deliver_mcp/deliver_a2a, which dispatch sync_accounts through the one
    # AdCPTestClient core (PR #1858). sync_accounts has no pinned SDK response
    # model, so RESPONSE_MODEL names the parser the base's response_parser() uses.
    MCP_TOOL = "sync_accounts"
    A2A_SKILL = "sync_accounts"
    RESPONSE_MODEL = SyncAccountsResponse

    #: The SECOND verb. It cannot be a class attribute like the pair above,
    #: because which verb is in flight is a property of the REQUEST, not of the
    #: env — so it is applied in _deliver_via_client() below, at the one frame
    #: that has both the request and the tool name in hand.
    LIST_TOOL = "list_accounts"

    #: Which verb the in-flight REST request is for — read by ``REST_ENDPOINT``.
    _active_list: bool = False

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.accounts.get_audit_logger",
        # The proof-of-control getter is the ONE injection seam. Patching the
        # getter (not the class) means production always constructs a REAL prover:
        # a test-scoped auto-pass prover would persist active:true without proof,
        # which is the violation the service exists to prevent.
        "notification_proof": "src.core.tools.accounts.get_notification_proof_service",
    }

    def __init__(
        self,
        supported_billing: list[str] | None = None,
        account_approval_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._supported_billing = supported_billing
        self._account_approval_mode = account_approval_mode
        # Constructor-passed policy reaches the ``call_impl`` identity and the unit-mode
        # tenant substitute through the same overrides ``configure_tenant_field`` writes;
        # ``setup_default_data`` folds them into the DB row for the wire legs.
        if supported_billing is not None:
            self._tenant_overrides["supported_billing"] = supported_billing
        if account_approval_mode is not None:
            self._tenant_overrides["account_approval_mode"] = account_approval_mode
        # Proof-of-control outcomes: a default plus per-url overrides.
        self._proof_default: bool = True
        self._proof_overrides: dict[str, bool] = {}

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for audit logger and the proof-of-control seam."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger
        # Install the in-process prover mock DIRECTLY, not through the public
        # setter: the public setter is @realize_e2e-decorated and its e2e branch
        # rejects `succeeds=True` as unrealizable. Establishing an in-process
        # DEFAULT is not a scenario intent, so it must not go through realization —
        # routing it there raised at env construction on e2e_rest and cascaded into
        # "Factory session already bound" for every later test on the worker.
        self._apply_proof_mock()

    def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]:
        """Create tenant + principal, then fold constructor billing config into the DB.

        Constructor-passed ``supported_billing`` / ``account_approval_mode`` only
        seed in-memory tenant overrides; over the real MCP/A2A/e2e auth chain the
        live server reads tenant config from its own DB. Once the tenant row
        exists, write those values through the same setters so the DB and the
        in-memory identity agree.

        ``tenant_kwargs`` are forwarded to the base ``setup_default_data`` (e.g.
        ``account_sandbox=False``) — applied on create, or written to the
        existing row when the tenant was already seeded by a prior Given.
        """
        tenant, principal = super().setup_default_data(**tenant_kwargs)
        if self._supported_billing is not None:
            self.set_billing_policy(self._supported_billing)
        if self._account_approval_mode is not None:
            self.set_approval_mode(self._account_approval_mode)
        return tenant, principal

    def _require_tenant_row(self, setter_name: str) -> Any:
        """Return the env's Tenant row, raising if it does not exist yet.

        No-Quiet-Failures: writing tenant config to a missing row silently drops
        it, and over e2e the live server then never sees the policy. Direct the
        step to create the tenant first instead of skipping the write.
        """
        from src.core.database.models import Tenant

        tenant = self._session.get(Tenant, self._tenant_id) if self._session else None
        if tenant is None:
            raise RuntimeError(
                f"{setter_name}() requires the tenant row '{self._tenant_id}' to exist. "
                "Call env.setup_default_data() (or create the tenant via a Given step) "
                "before configuring billing policy / approval mode."
            )
        return tenant

    @realize_e2e(e2e_unsupported("the live server exposes no fault-injection surface for sync_accounts"))
    def fail_the_sync_internally(self) -> None:
        """Make the seller's sync raise an unexpected error, so the BOUNDARY answers.

        The obligation is that an internal failure reaches the buyer as a well-formed
        INTERNAL_ERROR envelope. Grading that needs the seller to actually fail: the
        step used to construct an ``AdCPSalesAgentError`` in the test process and return
        without dispatching, so the scenario graded an object the test made and never
        exercised the boundary's error translation at all.

        The injection is at the repository, one layer below the impl, so everything
        between it and the wire is real -- the impl, the boundary's exception handling,
        and the per-transport envelope build.

        Over e2e there is no seam at all, which is declared rather than silently
        no-oped: requests carry no testing headers.
        """
        from unittest.mock import patch

        from src.core.exceptions import AdCPInternalError

        patcher = patch(
            "src.core.tools.accounts.AccountRepository.mint_account_id",
            side_effect=AdCPInternalError(),
        )
        patcher.start()
        self._guard("patch:sync_internal_failure", patcher.stop)

    def set_billing_policy(self, supported: list[str]) -> None:
        """Configure which billing models this seller accepts (BR-RULE-059).

        ``configure_tenant_field`` updates both the in-memory tenant overrides
        (mock identity path) and the DB tenant record (real MCP/A2A/e2e auth
        chain). The tenant row must already exist — No-Quiet-Failures (PR #1430).
        """
        self._supported_billing = supported
        if self._session:
            self._require_tenant_row("set_billing_policy")
        self.configure_tenant_field("supported_billing", supported)

    def clear_account_brand(self, account_id: str) -> None:
        """Null out a persisted account's ``brand``, leaving the row otherwise intact.

        ``accounts.brand`` is a NULLABLE JSON column (src/core/database/models.py) —
        this writes a state the schema permits and the seller's own read path
        declares "should be unreachable". It is a REAL persisted-state surface, not
        a mock: the write goes through the env session bound to the same Postgres
        the live server reads over e2e (the same mechanism ``configure_tenant_field``
        uses), so every transport observes the same row.

        No-Quiet-Failures: an account_id that does not resolve raises rather than
        leaving the scenario to grade an untouched row.
        """
        from src.core.database.models import Account

        session = self.get_session()
        account = session.get(Account, (self._tenant_id, account_id))
        if account is None:
            raise RuntimeError(
                f"clear_account_brand() found no account {account_id!r} in tenant "
                f"{self._tenant_id!r}. The Given must pre-create the account first."
            )
        account.brand = None
        session.commit()

    def _realize_notification_proof_result(self, *, succeeds: bool, url: str | None = None) -> None:
        """e2e realization: verify the REAL prover will produce the requested verdict.

        The in-process injection seam cannot reach the live server, but this must
        not silently no-op -- that is exactly the failure this project's
        realize_e2e guard exists to prevent (a Given that thinks it configured a
        fault while the server runs unconfigured).

        Instead we check the property that makes the outcome hold out of process:
        production's prover refuses any RFC 2606/6761 reserved TLD without a DNS
        lookup, so a ``.example`` url fails closed on the live server for the same
        reason it fails in process. If the request is anything else, the intent
        genuinely has no realization and we say so rather than pretending.
        """
        from src.core.security.egress.policy import is_reserved_tld_host

        if succeeds:
            raise RuntimeError(
                "set_notification_proof_result(succeeds=True) has no e2e realization: forcing a "
                "SUCCESSFUL challenge needs a reachable HTTPS endpoint the live stack can call. "
                "Grade the success direction through proof REUSE (seed an already-active "
                "subscriber and re-send the identical tuple) instead."
            )
        if url is None:
            raise RuntimeError(
                "set_notification_proof_result(url=None) has no e2e realization: a GLOBAL "
                "proof-failure default has no server-side equivalent. Scope the failure to the "
                "scenario's url so production's reserved-TLD refusal realizes it."
            )
        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""
        if not is_reserved_tld_host(hostname):
            raise RuntimeError(
                f"cannot force a proof-of-control failure for {url!r} on the live server: the "
                "host is not under a reserved TLD, so production would really try to reach it "
                "and the verdict would depend on the environment. Use a reserved-TLD url."
            )
        # Reserved TLD: production's own prover refuses it. Nothing to inject --
        # the intent is already true of the real system, and verified here.

    @realize_e2e(_realize_notification_proof_result)
    def set_notification_proof_result(self, *, succeeds: bool, url: str | None = None) -> None:
        """Force the proof-of-control outcome, globally or for one url.

        ``url=None`` sets the default for every endpoint; a url-scoped call
        overrides just that endpoint. Scenarios that need a subscriber to exist as
        active seed it through a real sync (which must therefore PROVE), and then
        scope the failure to the url under test — so the success path is genuinely
        exercised rather than assumed.

        That is also what stops an always-``False`` prover from greening the suite:
        with the default at False, the seeding Given can no longer create its active
        subscriber and the scenario fails loudly. The positive direction is graded by
        the setup, not by an assertion nobody wrote.

        Over e2e the injection seam is unreachable, so ``_realize_notification_proof_result``
        VERIFIES instead of injecting: the scenarios use reserved-TLD urls, which
        production's own prover refuses without a DNS lookup, so the same verdict
        holds on the live server. Anything it cannot verify is declared unrealizable
        rather than silently no-oped.
        """
        if url is None:
            self._proof_default = succeeds
        else:
            self._proof_overrides[url] = succeeds
        self._apply_proof_mock()

    def _apply_proof_mock(self) -> None:
        """Rebuild the in-process prover mock from the current default + overrides.

        Separate from the public setter so ``_configure_mocks`` can establish the
        default without going through @realize_e2e — see the note there.
        """
        prover = MagicMock()
        overrides = self._proof_overrides
        default = self._proof_default

        # ``**_signing`` absorbs the strategy / seller_agent_url production threads in
        # (#1291 C2). A positional-only stand-in would make every account-sync test fail
        # with a TypeError the moment the real signature grew, which is a harness break
        # masquerading as a behaviour change.
        async def _prove(_account_id: Any, config: Any, **_signing: Any) -> bool:
            return overrides.get(str(getattr(config, "url", "")), default)

        prover.prove = _prove
        self.mock["notification_proof"].return_value = prover

    def use_real_proof_service(self) -> None:
        """Run PRODUCTION's prover instead of the dict-lookup stand-in.

        The getter stays patched — it is still the one injection seam — but it now hands
        back a real :class:`NotificationProofService`, so ``prove()``'s own body executes:
        the signing-strategy check, the conformant challenge payload, the fire-time SSRF
        check and the echo validation.

        This exists because the stand-in is why that body had ZERO coverage (#1291 C2): on
        every in-process transport it replaced the method, and on e2e the harness reaches
        the refusal branch before the POST. A test that wants to grade the SIGNED path has
        to be able to turn the substitution off, and doing it here rather than by patching
        from a test body keeps the seam single.
        """
        from src.services.notification_proof_service import NotificationProofService

        self.mock["notification_proof"].return_value = NotificationProofService()

    def set_approval_mode(self, mode: str) -> None:
        """Configure account approval mode (BR-RULE-060).

        Account approval mode is a distinct field from creative approval_mode
        (BR-RULE-037) — ``configure_tenant_field`` writes the correct column so
        the MCP real-auth chain (config_loader.get_tenant_by_id) sees it, and
        also updates the in-memory tenant overrides for the mock identity path.
        The tenant row must already exist — No-Quiet-Failures (PR #1430).
        """
        self._account_approval_mode = mode
        if self._session:
            self._require_tenant_row("set_approval_mode")
        self.configure_tenant_field("account_approval_mode", mode)

    async def call_impl_async(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB (async version).

        For use in @pytest.mark.asyncio tests with ``await``.
        """
        from src.core.tools.accounts import _sync_accounts_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_accounts_impl(**kwargs)

    def call_impl(self, **kwargs: Any) -> Any:
        """Call _sync_accounts_impl — or _list_accounts_impl — with real DB.

        Bridges async _impl for sync callers (BDD steps, dispatchers).
        """
        if self.is_list_request(kwargs):
            return self._call_list_impl(**kwargs)
        return asyncio.run(self.call_impl_async(**kwargs))

    def _deliver_via_client(self, transport: Any, tool: str, kwargs: dict[str, Any]) -> DeliverResult:
        """Re-address a list request onto the list verb, then DELEGATE dispatch.

        The one place this dual-verb env diverges from a single-verb one. It is
        not a second dispatch implementation: ADDRESS, WRAP, DELIVER and UNWRAP
        all still happen exactly once, inside ``BaseTestEnv._deliver_via_client``
        -> ``_dispatch_core`` -> the one ``AdCPTestClient`` core, for BOTH verbs.
        All this override changes is WHICH tool name the core is asked to
        address — the request-content discriminator the class attributes above
        structurally cannot carry, expressed at the one frame that holds the
        request and the tool name together.

        This is why ``deliver_mcp``/``deliver_a2a`` are gone (they were
        allowlisted at ``test_architecture_harness_single_dispatch``): each
        re-implemented the list leg on ``_run_mcp_client``/``_run_a2a_handler``
        directly, SKIPPING the core's address resolution and shared unwrap, so
        the two verbs were graded through two different dispatch paths. Now they
        are graded through one — and per-transport genuineness is unchanged,
        because the core's own MCP/A2A DELIVER calls those very same env
        primitives (``tests/harness/client.py``: ``_deliver_mcp`` /
        ``_deliver_a2a``).
        """
        if self.is_list_request(kwargs):
            tool = self.LIST_TOOL
        return super()._deliver_via_client(transport, tool, kwargs)

    def response_parser(self, tool: str) -> Any:
        """Type each verb's wire into its OWN response model.

        The base would hand both verbs ``RESPONSE_MODEL`` (sync_accounts', which
        has no pinned SDK model), so the list wire would be parsed as a
        ``SyncAccountsResponse``. This is the sanctioned hook for an env that
        selects a parser from request content — see ``BaseTestEnv.response_parser``.
        """
        if tool == self.LIST_TOOL:
            return ListAccountsResponse
        return super().response_parser(tool)

    SYNC_REST_ENDPOINT = "/api/v1/accounts/sync"

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """List scenarios POST the collection; sync scenarios POST its /sync sub-resource.

        A @property (not a static attr) because the E2E dispatcher reads it directly,
        and the verb is only known once the request is in hand. Safe because
        RestE2EDispatcher calls build_rest_body() — which sets the flag below —
        BEFORE reading this (tests/harness/dispatchers.py).
        """
        return self.LIST_REST_ENDPOINT if self._active_list else self.SYNC_REST_ENDPOINT

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """Route the in-process REST call to the endpoint of the verb being dispatched.

        Necessary because the two REST paths read ``REST_ENDPOINT`` in OPPOSITE
        orders: the e2e dispatcher calls ``build_rest_body`` first (so the flag is
        already set), while the in-process ``call_rest`` reads ``REST_ENDPOINT``
        BEFORE building the body. Setting the flag here — and recomputing the
        endpoint from it — makes both orders correct.
        """
        self._active_list = self.is_list_request(kwargs)
        return super()._run_rest_request(self.REST_ENDPOINT, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Serialize flat sync_accounts kwargs into the REST request body.

        The base implementation understands a ``req`` model and returns ``{}`` for
        anything else — so a caller dispatching flat parameters would POST an empty
        body and grade nothing (the route would reject on ``accounts`` being empty,
        which looks like a production failure but is a harness artifact). Scenarios
        that must observe how the REST route itself treats a wire field — rather than
        how a locally-constructed request model treats it — dispatch flat, so the
        flat form needs a faithful body here.
        """
        # Set the verb flag HERE, unconditionally: the E2E dispatcher reads
        # REST_ENDPOINT right after this call, and a sync request must clear a flag a
        # preceding list request left set.
        self._active_list = self.is_list_request(kwargs)
        if kwargs.get("req") is not None:
            return super().build_rest_body(**kwargs)
        return {key: value for key, value in kwargs.items() if value is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        """Parse REST JSON into the response model of whichever verb was dispatched.

        Deliberately does NOT reset ``_active_list`` afterwards, unlike the
        ``MediaBuyDualEnv`` precedent this otherwise follows: both writers above set the
        flag UNCONDITIONALLY from ``is_list_request(kwargs)`` at the start of every
        request, so a stale value cannot survive into the next one. A reset here would
        additionally be wrong on the e2e error path, which routes to ``parse_rest_error``
        and would leave the flag cleared mid-request.
        """
        if self._active_list:
            return self._parse_list_rest_response(data)
        return super().parse_rest_response(data)
