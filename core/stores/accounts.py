"""AccountStore over the salesagent ``principals`` and ``tenants`` tables.

Resolution order:
1. Bearer-authenticated principal's ``tenant_id`` (set by
   :class:`BearerTokenAuthMiddleware`) — authenticated buyers always
   operate in their principal's tenant regardless of which Host header
   they arrive under. Critical for embedded mode where one host serves
   many tenants.
2. Subdomain-set contextvar (set by :class:`SubdomainTenantMiddleware`)
   — fallback for unauthenticated discovery (agent card, well-known)
   and traditional one-tenant-per-subdomain deployments.
3. Explicit ``account.account_id`` like ``"tenant-a:acct_demo"`` —
   storyboards/dev.
4. Reject with ``ACCOUNT_NOT_FOUND``.

The resolved Account's ``metadata['tenant_id']`` is what
:class:`PlatformRouter` reads to pick the per-tenant ``DecisioningPlatform``.

This store also implements the framework's optional
:class:`AccountStoreUpsert` / :class:`AccountStoreList` Protocols so
``sync_accounts`` / ``list_accounts`` work on the wire — adcp >= 4.6.1's
:class:`PlatformHandler` dispatchers route the wire skill calls through
these methods.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, ClassVar

from adcp.decisioning import AdcpError
from adcp.decisioning.context import AuthInfo
from adcp.decisioning.types import Account
from adcp.server import current_tenant, current_transport
from adcp.server.auth import current_principal
from adcp.server.auth import current_tenant as auth_current_tenant
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as PrincipalRow
from src.core.database.models import Tenant
from src.core.exceptions import AdCPError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    ListAccountsRequest,
    SyncAccountsRequest,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.accounts import _list_accounts_impl, _sync_accounts_impl

logger = logging.getLogger(__name__)


class SalesagentAccountStore:
    """Tenant-scoped AccountStore backed by the salesagent ORM.

    Reads :class:`Tenant` rows from the existing schema. Bridges to
    :class:`PlatformRouter` by stamping ``metadata['tenant_id']`` on
    every resolved Account.
    """

    resolution: ClassVar[str] = "explicit"

    def resolve(
        self,
        ref: dict[str, Any] | None = None,
        auth_info: AuthInfo | None = None,
    ) -> Account[dict[str, Any]]:
        tenant_id = (
            self._tenant_from_principal(auth_info) or self._tenant_from_subdomain() or self._tenant_from_ref(ref)
        )
        if tenant_id is None or not self._tenant_exists(tenant_id):
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message=(
                    "Could not resolve a tenant. Authenticate via "
                    "x-adcp-auth, send via the tenant subdomain "
                    "(e.g. acme.example.com), or pass account.account_id "
                    "with a 'tenant_id:' prefix."
                ),
                recovery="terminal",
                field="account",
            )

        account_id = (ref or {}).get("account_id") if isinstance(ref, dict) else None
        if not account_id:
            account_id = f"{tenant_id}:default"

        return Account(
            id=account_id,
            metadata={"tenant_id": tenant_id},
            auth_info=_auth_info_to_dict(auth_info),
        )

    @staticmethod
    def _tenant_from_principal(auth_info: AuthInfo | None = None) -> str | None:
        """Authenticated principal's tenant — beats subdomain.

        Resolution order:

        1. ``auth_info.principal`` — the framework-canonical handoff.
           ``serve()`` threads the verified principal onto every
           ``AccountStore.resolve`` call via this argument; reading it
           here is task-safe (the MCP handler runs in a different
           asyncio task than the bearer-auth middleware, so its
           ContextVars may not be visible).
        2. ``adcp.server.auth.current_tenant`` ContextVar — set by
           :class:`BearerTokenAuthMiddleware` when the middleware and
           the handler share a task (A2A, REST). Falls back here when
           ``auth_info`` is absent (callers outside the framework
           dispatch, e.g. legacy admin paths).
        3. ``adcp.server.auth.current_principal`` ContextVar → DB
           lookup. Defensive belt-and-suspenders.

        Returns ``None`` if no principal can be resolved by any path —
        callers fall through to subdomain / ref resolution.
        """
        # 1. Framework-canonical: auth_info.principal carries the principal_id
        # the bearer middleware verified, regardless of which task we're in.
        principal_id: str | None = None
        if auth_info is not None and getattr(auth_info, "principal", None):
            principal_id = auth_info.principal
        else:
            # 2. ContextVar fast path (same-task callers).
            tenant_id = auth_current_tenant.get()
            if tenant_id:
                return tenant_id
            # 3. ContextVar principal → DB lookup fallback.
            principal_id = current_principal.get()

        if not principal_id:
            return None
        with get_db_session() as session:
            row = session.scalars(select(PrincipalRow).filter_by(principal_id=principal_id)).first()
        return row.tenant_id if row else None

    @staticmethod
    def _tenant_from_subdomain() -> str | None:
        tenant = current_tenant()
        return tenant.id if tenant else None

    @staticmethod
    def _tenant_from_ref(ref: dict[str, Any] | None) -> str | None:
        if not isinstance(ref, dict):
            return None
        account_id = ref.get("account_id")
        if not isinstance(account_id, str) or ":" not in account_id:
            return None
        prefix, _ = account_id.split(":", 1)
        return prefix

    @staticmethod
    def _tenant_exists(tenant_id: str) -> bool:
        with get_db_session() as session:
            row = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        return row is not None and row.is_active

    # ----- AccountStoreUpsert / AccountStoreList Protocols -----------
    #
    # The framework (adcp >= 4.6.1) routes ``sync_accounts`` through
    # ``upsert(refs, ctx)`` and ``list_accounts`` through
    # ``list(filter, ctx)`` — see ``adcp.decisioning.accounts``. Our
    # ``_sync_accounts_impl`` / ``_list_accounts_impl`` operate on the
    # parsed request models, so we coerce the framework's narrower
    # arguments back to those models inside ``_coerce_*`` below.

    async def upsert(
        self,
        refs: Any,
        ctx: Any | None = None,
    ) -> Any:
        """Forward ``sync_accounts`` (the framework's
        :class:`AccountStoreUpsert.upsert` Protocol method) to
        ``_sync_accounts_impl``.

        ``refs`` is the ``list[AccountReference]`` projected from
        ``params.accounts`` by the framework dispatcher; we rebuild a
        :class:`SyncAccountsRequest` with a synthesised idempotency_key
        so the impl's existing model contract is preserved.
        """
        req = self._coerce_sync_accounts_payload(refs)
        return await self._sync_accounts(req, ctx)

    async def upsert_request(
        self,
        params: Any,
        ctx: Any | None = None,
    ) -> Any:
        """Forward the full ``sync_accounts`` request when the SDK supplies it.

        The SDK's stock AccountStore dispatch projects the request down to
        ``accounts[]`` before calling :meth:`upsert`. SalesAgent needs the
        request-level ``push_notification_config`` so buyer-registered
        webhooks can be persisted for later account/catalog changes.
        """
        req = self._coerce_sync_accounts_payload(params)
        return await self._sync_accounts(req, ctx)

    async def _sync_accounts(self, req: SyncAccountsRequest, ctx: Any | None) -> Any:
        identity = self._identity_from_ctx(ctx)
        try:
            return await _sync_accounts_impl(req=req, identity=identity)
        except AdCPError as exc:
            raise self._translate(exc) from exc

    async def list(
        self,
        filter_: Any = None,
        ctx: Any | None = None,
    ) -> Any:
        """Forward ``list_accounts`` (the framework's
        :class:`AccountStoreList.list` Protocol method) to
        ``_list_accounts_impl``.

        ``filter_`` is the flat filter dict projected from
        :class:`ListAccountsRequest` by the framework dispatcher
        (``status`` / ``sandbox`` / ``pagination``, ``None``-stripped).
        """
        req = self._coerce_list_accounts_payload(filter_)
        identity = self._identity_from_ctx(ctx)
        try:
            return await asyncio.to_thread(_list_accounts_impl, req, identity)
        except AdCPError as exc:
            raise self._translate(exc) from exc

    @staticmethod
    def _coerce_sync_accounts_payload(payload: Any) -> SyncAccountsRequest:
        """Normalise the framework's ``upsert`` argument into a
        :class:`SyncAccountsRequest` for the impl. ``list`` is the
        post-#610 ``refs`` shape; everything else is treated as the
        full request."""
        if isinstance(payload, SyncAccountsRequest):
            return payload
        if isinstance(payload, list):
            # Framework projected ``params.accounts`` to a list of refs
            # (the wire-side ``idempotency_key`` is consumed by the
            # framework before reaching us). Synthesise a fresh uuid4
            # for the impl's contract: collision-safe under concurrent
            # retries that happen to hit a recycled list address.
            return SyncAccountsRequest.model_construct(
                accounts=payload,
                idempotency_key=f"framework-{uuid.uuid4()}",
            )
        if hasattr(payload, "model_dump"):
            return SyncAccountsRequest(**payload.model_dump(exclude_none=True))
        if isinstance(payload, dict):
            return SyncAccountsRequest(**payload)
        return SyncAccountsRequest.model_validate(payload)

    @staticmethod
    def _coerce_list_accounts_payload(payload: Any) -> ListAccountsRequest | None:
        """Normalise the framework's ``list`` argument into a
        :class:`ListAccountsRequest`. ``None`` and an empty filter dict
        both map to ``None`` (the impl's no-filter path)."""
        if payload is None:
            return None
        if isinstance(payload, ListAccountsRequest):
            return payload
        if hasattr(payload, "model_dump"):
            return ListAccountsRequest(**payload.model_dump(exclude_none=True))
        if isinstance(payload, dict):
            if not payload:
                return None
            return ListAccountsRequest(**payload)
        return ListAccountsRequest.model_validate(payload)

    def _identity_from_ctx(self, ctx: Any | None) -> ResolvedIdentity:
        """Build a :class:`ResolvedIdentity` for the account-store dispatch
        path (``sync_accounts`` / ``list_accounts``).

        Tenant resolution mirrors :meth:`_tenant_from_principal` — that's
        the framework-canonical fallback chain already used by
        :meth:`resolve`. Reading ``ctx.auth_info.principal`` is
        task-safe: the MCP handler runs in a different asyncio task than
        ``BearerTokenAuthMiddleware``, so its ContextVars may not be
        visible. Looking up the persisted Principal row gives us
        ``tenant_id`` regardless of whether the ContextVar propagated
        (#354).

        We deliberately don't read ``ctx.agent.tenant_id`` as a
        fallback — the framework's :class:`BuyerAgent` doesn't carry
        ``tenant_id`` today, and accepting that attribute as authoritative
        would widen the trust boundary the moment a custom
        :class:`BuyerAgentRegistry` started populating it.
        """
        from src.core.config_loader import get_tenant_by_id

        auth_info = getattr(ctx, "auth_info", None) if ctx is not None else None
        tenant_id = self._tenant_from_principal(auth_info)
        # Derive principal_id with the same fallback order — ``auth_info``
        # first (task-safe), then ContextVar.
        principal_id: str | None = None
        if auth_info is not None:
            principal_id = getattr(auth_info, "principal", None)
        if not principal_id:
            principal_id = current_principal.get()
        if not tenant_id:
            raise AdcpError(
                "ACCOUNT_NOT_FOUND",
                message=(
                    "sync_accounts/list_accounts requires an authenticated "
                    "principal — no tenant resolved on the request context."
                ),
                recovery="terminal",
                field="account",
            )
        tenant_dict = get_tenant_by_id(tenant_id)
        # Mirror ``core/platforms/_delegate.py:_build_identity`` — read
        # the actual transport from the ``current_transport`` ContextVar
        # populated by ``TransportDetectMiddleware``. Hard-coding ``mcp``
        # would silently misroute future protocol-aware behavior (webhook
        # payload shape, transport-specific status messaging) for A2A
        # callers reaching account dispatch.
        detected = current_transport.get()
        protocol: str = detected if detected in ("mcp", "a2a") else "mcp"
        return ResolvedIdentity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            tenant=tenant_dict,
            protocol=protocol,
            testing_context=AdCPTestContext(),
        )

    @staticmethod
    def _translate(exc: AdCPError) -> AdcpError:
        return AdcpError(
            exc.error_code,
            message=exc.message or str(exc),
            recovery=exc.recovery,
            details=exc.details if isinstance(exc.details, dict) else None,
        )


def _auth_info_to_dict(auth_info: AuthInfo | None) -> dict[str, Any] | None:
    if auth_info is None:
        return None
    return {
        "kind": auth_info.kind,
        "key_id": auth_info.key_id,
        "principal": auth_info.principal,
        "scopes": list(auth_info.scopes),
    }
