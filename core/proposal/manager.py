"""SalesAgentProposalManager — wraps salesagent's get_products with the
v1.5 ``ProposalManager`` primitive so a single code path serves both
the stateless ``buying_mode='brief'`` flow AND the stateful
``buying_mode='refine'`` proposal-iteration flow.

Architectural shape (per @bokelley): every seller's get_products is
*conceptually* a proposal generation step — the response is a proposed
configuration of products, pricing, formats, inventory hints. v1.5
ProposalManager + ProposalStore lifts that fully into a managed
primitive: ``get_products`` produces a draft, ``refine_products``
iterates, ``ProposalStore.commit`` persists, and downstream
``create_media_buy(proposal_id=X)`` consumes via ``try_reserve_consumption``
+ ``finalize_consumption``.

When the framework's :class:`LazyPlatformRouter` has a wired
:class:`ProposalManager` for a tenant, it routes ``get_products`` to
``manager.get_products`` instead of ``platform.get_products`` —
subsuming the call entirely. ``platform.get_products`` becomes the
fallback for tenants without a manager wired (none of ours, but the
framework keeps it for migration).

v1 scope: stateless ``get_products`` that delegates to
``_get_products_impl`` (same brain as core/platforms/_delegate.py).
DRAFT persistence + refinement land in v2 once the storyboard's
sales-proposal-mode bundle drives them. Keep the surface narrow until
real flows demand more — premature lifecycle hooks would lock us into
shapes that don't match the actual proposal flow we want.
"""

from __future__ import annotations

from typing import Any, ClassVar

from adcp.decisioning import AdcpError, RequestContext
from adcp.decisioning.proposal_manager import ProposalCapabilities
from adcp.types import GetProductsRequest, GetProductsResponse

from core.platforms._delegate import _build_identity, _coerce_to_request_model
from src.core.tools.products import _get_products_impl


class SalesAgentProposalManager:
    """Single-tenant proposal manager that subsumes ``get_products``
    via the existing ``_get_products_impl`` business logic.

    Implements the :class:`adcp.decisioning.proposal_manager.ProposalManager`
    Protocol structurally — the SDK's reference ``MockProposalManager``
    follows the same no-inheritance pattern.

    The manager declares ``ProposalCapabilities(refine=False)`` for v1
    — the framework router falls through to :meth:`get_products` even
    when a buyer sends ``buying_mode='refine'``. v2 flips refine on,
    persists DRAFTs via ``ProposalStore.put_draft``, and loads prior
    drafts in :meth:`refine_products` to support iterative shortlisting.
    """

    # Match the platform's specialism declaration; v1 ships only the
    # non-guaranteed sales path (CPM auctions, no fixed-quantity holds).
    capabilities: ClassVar[ProposalCapabilities] = ProposalCapabilities(
        sales_specialism="sales-non-guaranteed",
        refine=False,
    )

    async def get_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> GetProductsResponse:
        """Delegate to ``_get_products_impl`` — same path the platform
        method took before the manager subsumed it. Returns the
        ``GetProductsResponse`` Pydantic model rather than a wire dict
        because the framework's ProposalManager protocol declares the
        typed return; the inner adcp serializer handles model_dump.
        """
        identity = _build_identity(ctx)
        req_model = _coerce_to_request_model(req, GetProductsRequest)
        return await _get_products_impl(req_model, identity)

    async def refine_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> GetProductsResponse:
        """Refine-mode stub.

        Required by the :class:`ProposalManager` Protocol surface so
        adopters declaring ``capabilities.refine=True`` have a typed
        signature to override. v1 sets ``capabilities.refine=False`` —
        the framework routes refine requests to :meth:`get_products`
        instead and this method is never invoked. Raises
        ``UNSUPPORTED_FEATURE`` defensively if reached.
        """
        del req, ctx
        raise AdcpError(
            "UNSUPPORTED_FEATURE",
            message=(
                "refine_products called on SalesAgentProposalManager; v1 "
                "declares capabilities.refine=False. Buyers should rely on "
                "get_products for product discovery."
            ),
        )
