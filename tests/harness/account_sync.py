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

beads: salesagent-7do
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas.account import SyncAccountsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._realize import realize_e2e


class AccountSyncEnv(IntegrationEnv):
    """Integration test environment for _sync_accounts_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository -> real DB writes
    - Real upsert, deactivate_missing, grant_access logic

    Both sync and async call patterns are supported:
    - call_impl() / call_a2a(): sync wrappers for BDD steps and dispatchers
    - call_impl_async() / call_a2a_async(): for @pytest.mark.asyncio tests

    Constructor accepts ``supported_billing`` to configure billing policy
    on the identity (BR-RULE-059).
    """

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
        from src.core.security.url_validator import is_reserved_tld_host

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

    def identity_for(self, transport: Any) -> Any:
        """Build identity with billing policy and approval mode on the tenant dict."""
        if self._supported_billing is not None:
            self._tenant_overrides["supported_billing"] = self._supported_billing
        if self._account_approval_mode is not None:
            self._tenant_overrides["account_approval_mode"] = self._account_approval_mode
        self._identity_cache.clear()
        return super().identity_for(transport)

    async def call_impl_async(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB (async version).

        For use in @pytest.mark.asyncio tests with ``await``.
        """
        from src.core.tools.accounts import _sync_accounts_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_accounts_impl(**kwargs)

    def call_impl(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB (sync wrapper).

        Bridges async _impl for sync callers (BDD steps, dispatchers).
        """
        return asyncio.run(self.call_impl_async(**kwargs))

    def call_a2a(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call sync_accounts via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("sync_accounts", SyncAccountsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call sync_accounts via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("sync_accounts", SyncAccountsResponse, **kwargs)

    REST_ENDPOINT = "/api/v1/accounts/sync"

    def parse_rest_response(self, data: dict[str, Any]) -> SyncAccountsResponse:
        """Parse REST JSON into SyncAccountsResponse."""
        return SyncAccountsResponse(**data)
