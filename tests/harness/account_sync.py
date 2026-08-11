"""AccountSyncEnv — integration test environment for both _sync_accounts_impl AND
_list_accounts_impl (accounts superset env).

Patches: audit logger ONLY (inherited from AccountListEnv).
Real: get_db_session, AccountRepository, all upsert/deactivate/query logic (all hit real DB).

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

Genuine per-transport list dispatch (salesagent-oyiv.15): AccountSyncEnv extends
AccountListEnv and routes by request type (mirrors MediaBuyDualEnv's
_is_update_request pattern) — a ListAccountsRequest reaches the real list_accounts
tool on every transport with a real wire body; everything else reaches sync_accounts.
No test-side wire forgery.

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import)

beads: salesagent-7do
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.schemas.account import ListAccountsRequest, ListAccountsResponse, SyncAccountsResponse
from tests.harness.account_list import AccountListEnv


def _is_list_request(kwargs: dict[str, Any]) -> bool:
    req = kwargs.get("req")
    return isinstance(req, ListAccountsRequest)


class AccountSyncEnv(AccountListEnv):
    """Integration test environment for the accounts superset (sync + list).

    Only mocks the audit logger (inherited from AccountListEnv). Everything else
    is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository -> real DB writes/reads
    - Real upsert, deactivate_missing, grant_access, query/filter logic

    Both sync and async call patterns are supported for the sync path:
    - call_impl() / call_a2a(): sync wrappers for BDD steps and dispatchers
    - call_impl_async() / call_a2a_async(): for @pytest.mark.asyncio tests

    List requests (ListAccountsRequest) are routed to AccountListEnv's own
    dispatch methods via super() -- see call_impl/call_a2a/call_mcp below.
    call_impl_async stays sync_accounts-only: a list request reaching it is a
    silent-misdispatch bug this env raises loudly for instead of masking.

    Constructor accepts ``supported_billing`` to configure billing policy
    on the identity (BR-RULE-059).
    """

    def __init__(
        self,
        supported_billing: list[str] | None = None,
        account_approval_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._supported_billing = supported_billing
        self._account_approval_mode = account_approval_mode

    def setup_default_data(self) -> tuple[Any, Any]:
        """Create tenant + principal, then fold constructor billing config into the DB.

        Constructor-passed ``supported_billing`` / ``account_approval_mode`` only
        seed in-memory tenant overrides; over the real MCP/A2A/e2e auth chain the
        live server reads tenant config from its own DB. Once the tenant row
        exists, write those values through the same setters so the DB and the
        in-memory identity agree.
        """
        tenant, principal = super().setup_default_data()
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

        Updates both the in-memory tenant overrides (for mock identity path)
        and the DB tenant record (for real MCP/A2A/e2e auth chain).
        """
        self._supported_billing = supported
        self._tenant_overrides["supported_billing"] = supported
        self._identity_cache.clear()

        if self._session:
            tenant = self._require_tenant_row("set_billing_policy")
            tenant.supported_billing = supported
            self._session.commit()

    def set_approval_mode(self, mode: str) -> None:
        """Configure account approval mode (BR-RULE-060).

        Updates both the in-memory tenant overrides (for mock identity path)
        and the DB tenant record (for real MCP/A2A/e2e auth chain).
        """
        self._account_approval_mode = mode
        self._tenant_overrides["account_approval_mode"] = mode
        self._identity_cache.clear()

        if self._session:
            tenant = self._require_tenant_row("set_approval_mode")
            # BR-RULE-060: account approval mode is a distinct field from creative
            # approval_mode (BR-RULE-037). Write to the correct column so the MCP
            # real-auth chain (which reads DB via config_loader.get_tenant_by_id)
            # sees the test-configured value.
            tenant.account_approval_mode = mode
            self._session.commit()

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

        For use in @pytest.mark.asyncio tests with ``await``. sync_accounts-only:
        raises loudly if a list request reaches this entry point instead of
        silently misdispatching it to _sync_accounts_impl (call_impl routes list
        requests to AccountListEnv.call_impl, which is sync — this async path has
        no list counterpart to route to).
        """
        from src.core.tools.accounts import _sync_accounts_impl

        if _is_list_request(kwargs):
            raise RuntimeError(
                "AccountSyncEnv.call_impl_async() received a ListAccountsRequest — "
                "list requests must go through call_impl (sync), not call_impl_async. "
                "This guard exists so a list request cannot silently misdispatch to "
                "_sync_accounts_impl."
            )
        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_accounts_impl(**kwargs)

    def call_impl(self, **kwargs: Any) -> SyncAccountsResponse | ListAccountsResponse:
        """Route by request type BEFORE the asyncio bridge: a ListAccountsRequest
        reaches AccountListEnv.call_impl (sync); everything else bridges to the
        async _sync_accounts_impl via call_impl_async."""
        if _is_list_request(kwargs):
            return super().call_impl(**kwargs)
        return asyncio.run(self.call_impl_async(**kwargs))

    def call_a2a(self, **kwargs: Any) -> SyncAccountsResponse | ListAccountsResponse:
        """Route to the real list_accounts A2A skill for a ListAccountsRequest,
        sync_accounts otherwise — both via the real AdCPRequestHandler pipeline."""
        if _is_list_request(kwargs):
            return super().call_a2a(**kwargs)
        return self._run_a2a_handler("sync_accounts", SyncAccountsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> SyncAccountsResponse | ListAccountsResponse:
        """Route to the real list_accounts MCP tool for a ListAccountsRequest,
        sync_accounts otherwise — both via Client(mcp) full pipeline dispatch."""
        if _is_list_request(kwargs):
            return super().call_mcp(**kwargs)
        return self._run_mcp_client("sync_accounts", SyncAccountsResponse, **kwargs)

    _active_list: bool = False

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """RestDispatcher.dispatch (tests/harness/dispatchers.py) reads
        env.REST_ENDPOINT BEFORE calling this method, so the *endpoint* argument
        can be stale by the time the routing flag below is known — recompute the
        real endpoint here instead of trusting it (mirrors MediaBuyDualEnv's
        _run_update_rest_request, which builds its own endpoint rather than
        trusting the caller's stale read). Every entry point sets the flag;
        nothing resets it (see build_rest_body for the e2e_rest dispatcher's
        different call order, and the class docstring for why a reset creates a
        stale-flag window)."""
        self._active_list = _is_list_request(kwargs)
        real_endpoint = "/api/v1/accounts" if self._active_list else "/api/v1/accounts/sync"
        return super()._run_rest_request(real_endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """The e2e_rest dispatcher (RestE2EDispatcher) calls build_rest_body BEFORE
        reading REST_ENDPOINT — set the flag here too so that path's REST_ENDPOINT
        read (below) sees the right value. Never reset: an unconditional set on
        every entry point is the only way both call orders see the right value
        for their own dispatch."""
        self._active_list = _is_list_request(kwargs)
        return super().build_rest_body(**kwargs)

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """List requests GET the collection endpoint; sync requests POST to /sync.
        Both routes are POST (no REST_METHOD override needed)."""
        return "/api/v1/accounts" if self._active_list else "/api/v1/accounts/sync"

    def parse_rest_response(self, data: dict[str, Any]) -> SyncAccountsResponse | ListAccountsResponse:
        """Parse REST JSON into ListAccountsResponse or SyncAccountsResponse,
        matching whichever endpoint self._active_list routed this request to."""
        if self._active_list:
            return ListAccountsResponse(**data)
        return SyncAccountsResponse(**data)
