"""The one place a tool's WIRING is declared: which transports reach it, and what runs it.

A tool used to be declared between three and four times -- ``_register_tool`` in
``src/core/main.py``, an ``AgentSkill`` literal plus a ``skill_handlers`` row in the A2A
server, and a ``@router.post``/``@router.put`` decorator in ``src/routes/api_v1.py`` -- and
each declaration could disagree with the others. :data:`TOOLS` is the single declaration all
of them are now derived from; ``docs/development/building-tools.md`` is the design.

These rows are hand-written and therefore capable of being wrong. What makes them right is
that the transports are GENERATED from them: MCP registration loops this mapping, the A2A
card is ``_derived_skills()`` over it, A2A dispatch is ``_dispatch_skill`` validating a
parameter bag into ``TOOLS[name].dto``, and the REST router adds a route per ``rest``
binding. A row cannot disagree with a registration that is built from it.

:class:`ToolSpec` says where a tool is reachable and what runs it. It says nothing about the
tool's SHAPE -- the DTO says that itself, which is why ``dto`` is a reference to a model and
not a description of one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, get_type_hints

from pydantic import BaseModel

from src.core.resolved_identity import AccountIdentity, PublicIdentity, ResolvedIdentity
from src.core.schemas import (
    CompleteTaskRequest,
    CreateMediaBuyRequest,
    GetAdcpCapabilitiesRequest,
    GetMediaBuyDeliveryRequest,
    GetMediaBuysRequest,
    GetProductsRequest,
    GetTaskStatusRequest,
    ListAccountsRequest,
    ListCreativeFormatsRequest,
    ListCreativesRequest,
    ListTasksRequest,
    SyncAccountsRequest,
    SyncCreativesRequest,
    UpdateMediaBuyRequest,
)
from src.core.schemas._base import AdcpResponse, BuyerRequest
from src.core.tools.accounts import _list_accounts_impl, _sync_accounts_impl
from src.core.tools.capabilities import _get_adcp_capabilities_impl
from src.core.tools.creative_formats import _list_creative_formats_impl
from src.core.tools.creatives._sync import _sync_creatives_impl
from src.core.tools.creatives.listing import _list_creatives_impl
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from src.core.tools.products import _get_products_impl
from src.core.tools.task_management import _complete_task_impl, _get_task_status_impl, _list_tasks_impl

if TYPE_CHECKING:
    from src.core.tenant_context import TenantContext


@dataclass(frozen=True)
class RestBinding:
    """How one tool is reached over REST.

    ``verb`` is deliberately narrow. REST is this project's own surface, not AdCP's, and an
    AdCP tool call carries a request body -- so a verb that cannot is not a shape this
    registry can express. ``GET /capabilities`` was the one route that needed it, and the
    answer was deleting the route (design doc, "Decisions this forces"), not widening the
    type: it was a
    second shape for a tool that already had one, taking no body, so a buyer could not send
    ``protocols``, ``context`` or ``ext`` that the same tool accepted everywhere else.

    ``path`` is written WITHOUT the router's ``/api/v1`` prefix -- the prefix belongs to the
    router, is declared there once, and repeating it on thirteen rows would be thirteen more
    places for it to drift.
    """

    verb: Literal["POST", "PUT"]
    path: str
    path_fields: frozenset[str] = frozenset()


class ToolImpl[Req: BuyerRequest, Id: PublicIdentity](Protocol):
    """How the boundary calls an implementation: ``impl(req=<DTO>, identity=<identity>)``.

    Typing ``ToolSpec.impl`` with this is what makes mypy grade every row: an implementation
    whose ``req`` is not the row's ``dto`` (or a supertype of it), whose ``identity`` is not
    one of the three identity types, or which declares a third parameter without a default,
    fails to type-check at the row that names it. Sync and async implementations both
    satisfy it -- a coroutine is an ``Awaitable`` of the response -- and the boundary awaits
    whichever comes back.

    ``Id`` is the identity type the implementation declared -- ``ResolvedIdentity`` for a
    protected tool, ``AccountIdentity`` for one whose DTO requires an account,
    ``PublicIdentity`` for a public one -- and it is the row's whole credential policy:
    see ``ToolSpec.requires_credential``.

    A protocol accepts a callable that takes MORE than it asks for, so two things it cannot
    pin are pinned by ``.ast-grep/rules/impl-signature-is-request-and-identity.yml``:
    ``identity`` declared Optional or defaulted, and an extra defaulted parameter.
    """

    def __call__(self, *, req: Req, identity: Id) -> AdcpResponse | Awaitable[AdcpResponse]: ...


_IDENTITY_TYPES: tuple[type[PublicIdentity], ...] = (PublicIdentity, ResolvedIdentity, AccountIdentity)


def _declared_identity_type(impl: object) -> type[PublicIdentity]:
    """The class an implementation annotates its ``identity`` parameter with."""
    declared = get_type_hints(impl)["identity"]
    if declared not in _IDENTITY_TYPES:
        raise TypeError(f"{impl!r} declares identity: {declared!r}; expected one of {_IDENTITY_TYPES}")
    return declared


@dataclass(frozen=True)
class ToolSpec[Req: BuyerRequest, Id: PublicIdentity]:
    """One tool's wiring: what runs it, what shape it takes, and where it is reachable.

    Generic in the request so that ``dto`` and ``impl`` are checked against EACH OTHER: the
    DTO the transports validate into is the one the implementation is typed to receive.
    Generic in the identity for the same reason: the type the resolver hands over is the
    type the implementation declared, and mypy checks the two at the row.
    """

    dto: type[Req]
    impl: ToolImpl[Req, Id]
    rest: RestBinding | None
    a2a: bool = True

    def __post_init__(self) -> None:
        """Refuse, at load, an identity annotation that disagrees with the DTO about the account.

        Whether the boundary resolves an account is DERIVED from the DTO: it resolves one
        iff the DTO declares ``account`` and the request carries it, so the DTO alone
        determines the identity type. Both mismatches are refused:

        - ``identity: AccountIdentity`` on a DTO whose ``account`` is optional or absent
          asserts an account that is not always there;
        - ``identity: ResolvedIdentity`` on a DTO that REQUIRES ``account`` declares an
          Optional the resolver never leaves empty, so the tool would be written to narrow
          it -- the re-check this whole design removes.
        """
        declared = _declared_identity_type(self.impl)
        claims_account = declared is AccountIdentity
        if claims_account != self.requires_account():
            expected = "AccountIdentity" if self.requires_account() else "ResolvedIdentity"
            raise TypeError(
                f"{self.impl!r} declares identity: {declared.__name__} but {self.dto.__name__} "
                f"{'requires' if self.requires_account() else 'does not require'} account; annotate {expected}"
            )

    def requires_account(self) -> bool:
        """Whether every request to this tool names an account: the DTO declares it required."""
        if not issubclass(self.dto, BaseModel):
            raise TypeError(f"{self.dto!r} is not a pydantic model")
        field = self.dto.model_fields.get("account")
        return field is not None and field.is_required()

    def requires_credential(self, tenant: TenantContext | None = None) -> bool:
        """Whether a caller must present a credential that resolves to a principal.

        DERIVED, never declared on the row. Two sources, both read here:

        - the implementation's identity annotation: ``ResolvedIdentity`` (or its
          ``AccountIdentity`` subclass) needs a caller, ``PublicIdentity`` serves anybody;
        - the SELLER's policy, when a ``tenant`` is given: a DTO that declares ``brand`` on
          a tenant whose ``brand_manifest_policy`` is ``require_auth`` needs a caller too
          (BR-UC-001 INV-1). The resolver asks with the tenant once it has loaded the row,
          so the refusal is minted there and nowhere else; without a tenant the answer is
          the annotation's alone, which is what the boundary uses to type the result.

        The row used to carry a required-or-optional ``auth`` literal beside the impl, which
        is a second statement of the same fact -- and a tool whose row said required while
        its body re-checked ``identity.principal`` for ``None`` was the symptom. Now the
        annotation is the policy, the resolver builds the type the annotation names, and the
        body reads the fields without a check.

        What it means is fixed by the pinned enum, ``3.1/enums/error-code.json``: AUTH_MISSING
        is "No credentials were presented. Sellers MUST return this code when no
        `Authorization` header was included in the request", and AUTH_INVALID is "Sellers MUST
        return this code when an `Authorization` header was present but verification failed".
        The second MUST names no task, so it is not this policy's to waive: the resolver
        refuses a rejected credential on every row. What this policy decides is the ABSENT
        credential only -- a protected tool refuses it (AUTH_MISSING), a public tool serves
        the caller anonymously, which is the case
        ``dist/compliance/3.1.1/universal/security.yaml`` sets aside ("public tasks like
        get_adcp_capabilities return 200 without credentials by design").
        """
        if issubclass(_declared_identity_type(self.impl), ResolvedIdentity):
            return True
        return tenant is not None and self.accepts_brand() and tenant.brand_manifest_policy == "require_auth"

    def accepts_brand(self) -> bool:
        """Whether the DTO declares ``brand``: the field a seller's brand policy governs."""
        if not issubclass(self.dto, BaseModel):
            raise TypeError(f"{self.dto!r} is not a pydantic model")
        return "brand" in self.dto.model_fields


#: Every tool this seller implements, keyed by its AdCP tool name.
_TOOLS: dict[str, ToolSpec] = {
    "get_adcp_capabilities": ToolSpec(
        dto=GetAdcpCapabilitiesRequest,
        impl=_get_adcp_capabilities_impl,
        rest=RestBinding("POST", "/capabilities"),
    ),
    "get_products": ToolSpec(
        dto=GetProductsRequest,
        impl=_get_products_impl,
        rest=RestBinding("POST", "/products"),
    ),
    "list_creative_formats": ToolSpec(
        dto=ListCreativeFormatsRequest,
        impl=_list_creative_formats_impl,
        rest=RestBinding("POST", "/creative-formats"),
    ),
    "list_accounts": ToolSpec(
        dto=ListAccountsRequest,
        impl=_list_accounts_impl,
        rest=RestBinding("POST", "/accounts"),
    ),
    "sync_accounts": ToolSpec(
        dto=SyncAccountsRequest,
        impl=_sync_accounts_impl,
        rest=RestBinding("POST", "/accounts/sync"),
    ),
    "create_media_buy": ToolSpec(
        dto=CreateMediaBuyRequest,
        impl=_create_media_buy_impl,
        rest=RestBinding("POST", "/media-buys"),
    ),
    "update_media_buy": ToolSpec(
        dto=UpdateMediaBuyRequest,
        impl=_update_media_buy_impl,
        rest=RestBinding("PUT", "/media-buys/{media_buy_id}", frozenset({"media_buy_id"})),
    ),
    "get_media_buys": ToolSpec(
        dto=GetMediaBuysRequest,
        impl=_get_media_buys_impl,
        rest=RestBinding("POST", "/media-buys/query"),
    ),
    "get_media_buy_delivery": ToolSpec(
        dto=GetMediaBuyDeliveryRequest,
        impl=_get_media_buy_delivery_impl,
        rest=RestBinding("POST", "/media-buys/delivery"),
    ),
    "sync_creatives": ToolSpec(
        dto=SyncCreativesRequest,
        impl=_sync_creatives_impl,
        rest=RestBinding("POST", "/creatives/sync"),
    ),
    "list_creatives": ToolSpec(
        dto=ListCreativesRequest,
        impl=_list_creatives_impl,
        rest=RestBinding("POST", "/creatives"),
    ),
    "list_tasks": ToolSpec(
        dto=ListTasksRequest,
        impl=_list_tasks_impl,
        rest=RestBinding("POST", "/tasks/query"),
    ),
    "get_task_status": ToolSpec(
        dto=GetTaskStatusRequest,
        impl=_get_task_status_impl,
        rest=RestBinding("POST", "/tasks/{task_id}", frozenset({"task_id"})),
    ),
    "complete_task": ToolSpec(
        dto=CompleteTaskRequest,
        impl=_complete_task_impl,
        rest=RestBinding("POST", "/tasks/{task_id}/complete", frozenset({"task_id"})),
    ),
}

#: The registry, READ-ONLY. ``ToolSpec`` is already frozen, so this makes the whole
#: declaration immutable: nothing may add, drop or repoint a row at runtime. Every transport
#: reads this per call rather than snapshotting it -- MCP registration and the REST router
#: each used to freeze a row into a closure, which made the registry and the thing that
#: actually ran two different objects, silently divergent and impossible to substitute.
#:
#: The underlying dict is module-private. A test that must stand a fixture row in for a real
#: one patches ``_TOOLS`` (tests/helpers/capture_wrapper_req.py) -- reaching past the public
#: surface deliberately and greppably, rather than the public surface being mutable so that
#: it can.
TOOLS: Mapping[str, ToolSpec] = MappingProxyType(_TOOLS)


def is_adcp_operation(name: str) -> bool:
    """Whether *name* is an AdCP operation this seller implements. THE predicate.

    The registry owns the answer because the registry IS the answer: a row here is what makes
    a tool reachable over MCP, A2A and REST, so "is this an AdCP operation name" and "is there
    a row for it" are one question. Published as a function rather than leaving callers to
    write ``name in TOOLS`` so that the question has a NAME, and so the one caller outside
    this module that has to ask it -- the capability-declaration validator enforcing the
    namespace split at security.mdx @ v3.1.1 :1053 -- asks the registry instead of deciding
    tool-ness by looking for a ``/``.

    A ``/`` test is what that validator would otherwise be, and it is the wrong shape twice
    over: it is a second definition of a question this module already answers, and it answers
    it differently. ``get_signals`` contains no slash and is not a row here; ``tasks/cancel``
    contains one and is not an AdCP operation either. Only one of those two facts is derivable
    from punctuation.
    """
    return name in TOOLS
