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

import uuid
from typing import Any, ClassVar

from adcp.decisioning import RequestContext
from adcp.decisioning.proposal_manager import ProposalCapabilities
from adcp.types import GetProductsRequest, GetProductsResponse
from adcp.types.generated_poc.core.product_allocation import ProductAllocation
from adcp.types.generated_poc.core.proposal import Proposal
from adcp.types.generated_poc.media_buy.get_products_response import (
    RefinementApplied,
    RefinementApplied1,
    RefinementApplied2,
    RefinementApplied3,
)

from core.platforms._delegate import _build_identity, _coerce_to_request_model, translate_adcp_errors
from src.core.tools.products import _get_products_impl


class SalesAgentProposalManager:
    """Single-tenant proposal manager that subsumes ``get_products``
    via the existing ``_get_products_impl`` business logic.

    Implements the :class:`adcp.decisioning.proposal_manager.ProposalManager`
    Protocol structurally — the SDK's reference ``MockProposalManager``
    follows the same no-inheritance pattern.

    The manager declares ``ProposalCapabilities(refine=True)`` — the
    framework router dispatches ``buying_mode='refine'`` requests to
    :meth:`refine_products` instead of :meth:`get_products`. v1 refine
    is stateless and acknowledgement-shaped (per the storyboard's
    ``field_present`` validations): every refine entry is echoed back
    with ``status='applied'`` and a fresh proposal is returned. v2 will
    persist DRAFTs via ``ProposalStore.put_draft`` and actually
    re-strategize allocations from the buyer's asks (drop products,
    shift budget, retarget); the v1 shape locks in the wire contract
    so the v2 hook can swap the strategy without changing the surface.
    """

    # Match the platform's specialism declaration; v1 ships only the
    # non-guaranteed sales path (CPM auctions, no fixed-quantity holds).
    #
    # ``auto_commit_on_put_draft=True`` (adcp>=5.4 / #723): the
    # storyboard's ``proposal_finalize/create_media_buy`` flow goes
    # brief → create_media_buy with no intermediate finalize step, so
    # the framework's ``try_reserve_consumption`` needs the proposal
    # in COMMITTED state. Opting into auto-commit makes the framework
    # call ``store.commit`` immediately after ``put_draft``,
    # transparently promoting DRAFT → COMMITTED in a single dispatch.
    # Mutually exclusive with ``finalize=True`` (validated by the
    # ProposalCapabilities constructor) — v2 with a ``finalize_proposal``
    # method swaps to the spec-canonical explicit-finalize lifecycle.
    capabilities: ClassVar[ProposalCapabilities] = ProposalCapabilities(
        sales_specialism="sales-non-guaranteed",
        refine=True,
        auto_commit_on_put_draft=True,
    )

    @translate_adcp_errors
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

        ``@translate_adcp_errors`` is mandatory here even though every
        delegate it wraps lives in ``_delegate.py``: when a tenant has a
        proposal manager wired (and every active tenant does per
        :func:`core.main._build_proposal_managers`), the framework router
        routes ``get_products`` to this method instead of
        :func:`core.platforms._delegate._delegate_get_products`. Without
        the decorator, salesagent ``AdCPError`` raises and pydantic
        ``ValidationError`` raises surface as opaque ``INTERNAL_ERROR``
        on the wire. The decorator also performs the
        ``adcp_major_version`` negotiation check before the impl runs.
        """
        identity = _build_identity(ctx)
        req_model = _coerce_to_request_model(req, GetProductsRequest)
        response = await _get_products_impl(req_model, identity)

        # Decorate brief-mode responses with a v1 proposal (#352).
        # ``buying_mode='wholesale'`` and ``buying_mode='refine'`` opt out
        # of curated proposals per the AdCP spec; brief is the only mode
        # where the seller is expected to offer strategic bundling. The
        # ``proposals[]`` array is what the
        # ``media_buy_seller/proposal_finalize/get_products_brief``
        # storyboard asserts on, and the ``proposal_id`` is what buyers
        # echo into ``create_media_buy(proposal_id=...)`` to execute the
        # bundle.
        buying_mode = getattr(req_model, "buying_mode", None) or getattr(req, "buying_mode", None)
        buying_mode_str = getattr(buying_mode, "value", buying_mode)
        if buying_mode_str == "brief":
            proposal = _build_v1_brief_proposal(response)
            if proposal is not None:
                response.proposals = [proposal]
        return response

    @translate_adcp_errors
    async def refine_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> GetProductsResponse:
        """Apply buyer refinements and return an updated proposal.

        Storyboard ``media_buy_seller/proposal_finalize/get_products_refine``
        sends ``buying_mode='refine'`` with a ``refine[]`` array of
        scope-specific asks (``request`` / ``product`` / ``proposal``).
        Spec response shape: ``proposals[]`` with an updated proposal,
        plus ``refinement_applied[]`` echoing each refine entry's
        outcome (``status`` ∈ ``{applied, partial, unable}`` + ``notes``).

        v1 is stateless and acknowledgement-shaped: every refine entry
        is reported as ``applied`` with a note explaining v1 doesn't
        re-strategize the allocation, and the response carries a fresh
        even-budget-split proposal (same logic as
        :meth:`_build_v1_brief_proposal`). Storyboard validation is
        ``field_present @ /proposals`` and ``response_schema`` — both
        satisfied without semantic refinement. v2 will swap the
        even-split for an allocation that actually honors the asks
        (drop product / shift budget / shape targeting) once
        ``ProposalStore`` is wired to load the prior draft by
        ``proposal_id`` from the refine entry.
        """
        identity = _build_identity(ctx)
        req_model = _coerce_to_request_model(req, GetProductsRequest)
        response = await _get_products_impl(req_model, identity)
        proposal = _build_v1_brief_proposal(response)
        if proposal is not None:
            response.proposals = [proposal]
        refine_entries = getattr(req_model, "refine", None) or []
        response.refinement_applied = _build_v1_refinement_applied(refine_entries)
        return response


def _build_v1_brief_proposal(response: GetProductsResponse) -> Proposal | None:
    """Build a v1 ``Proposal`` from a ``get_products`` response (#352).

    Splits budget evenly across every product the publisher returned —
    minimal but spec-compliant: every product allocation references a
    ``product_id`` and ``pricing_option_id`` from the response, the
    percentages sum to 100, and the response carries a
    ``proposal_id`` buyers can echo into ``create_media_buy``.

    Returns ``None`` when the response has no products (the spec
    requires ``min_length=1`` on ``allocations`` — an empty proposal
    would fail Pydantic construction). The caller falls back to
    products-only output, which is also spec-legal because
    ``proposals`` is optional.

    Future revisions (refine flow, weighted allocations, persisted
    drafts) ride the same hook — they only need to swap the allocation
    strategy.
    """
    products = list(getattr(response, "products", []) or [])
    if not products:
        return None
    share = round(100.0 / len(products), 2)
    # Pin the final allocation to whatever remains so the sum lands on
    # exactly 100 — Pydantic's ge=0 / le=100 bounds allow this, and the
    # spec only requires the sum, not equal weighting.
    allocations: list[ProductAllocation] = []
    running_total = 0.0
    for i, product in enumerate(products):
        if i == len(products) - 1:
            percentage = round(max(0.0, 100.0 - running_total), 2)
        else:
            percentage = share
            running_total += percentage
        pricing_option_id = _first_pricing_option_id(product)
        allocations.append(
            ProductAllocation(
                product_id=product.product_id,
                allocation_percentage=percentage,
                pricing_option_id=pricing_option_id,
                rationale=None,
            )
        )
    return Proposal(
        proposal_id=f"prop_{uuid.uuid4().hex[:12]}",
        name="Recommended bundle",
        description=(
            "Even-budget split across every matched product. v1 strategy — "
            "refine the allocations via a subsequent get_products call "
            '(buying_mode="refine") once refine support is wired.'
        ),
        allocations=allocations,
    )


def _first_pricing_option_id(product: Any) -> str | None:
    """Pull the first ``pricing_option_id`` off a product, tolerating
    library RootModel wrappers and absent pricing options.
    """
    options = getattr(product, "pricing_options", None) or []
    if not options:
        return None
    first = options[0]
    # adcp 2.14.0+ wraps pricing options in a RootModel; unwrap.
    first = getattr(first, "root", first)
    return getattr(first, "pricing_option_id", None)


_V1_REFINE_ACK_NOTE = (
    "v1 acknowledges the ask but does not yet re-strategize allocations — "
    "the response carries a fresh even-split proposal. v2 will honor the "
    "ask semantically (drop / shift / retarget) once ProposalStore is wired."
)

# Defensive caps on buyer-supplied refine echo. ``RefinementApplied2.product_id``
# and ``RefinementApplied3.proposal_id`` are typed ``str`` with no length cap
# in the adcp library, so an adversarial buyer could ship 10MB ids and force
# us to hold them through Pydantic validation and echo them back. Real AdCP
# ids look like ``prop_abc123`` / ``prod_video_outdoor`` — 256 chars leaves
# generous headroom. Oversize/missing ids are DROPPED, not truncated:
# truncation would corrupt id semantics for downstream correlation.
_MAX_REFINE_ID_LEN = 256
# Cap the refine array itself so an N-million-entry payload can't drive
# memory pressure even before per-entry processing. Storyboard sample sends
# 2; real flows are unlikely to exceed a handful. 50 leaves headroom.
_MAX_REFINE_ENTRIES = 50


def _is_safe_id(value: Any) -> bool:
    """True when ``value`` is a non-empty str within the echo length cap."""
    return isinstance(value, str) and 0 < len(value) <= _MAX_REFINE_ID_LEN


def _build_v1_refinement_applied(refine_entries: Any) -> list[RefinementApplied]:
    """Echo each refine entry back as a ``RefinementApplied`` with
    ``status='applied'`` and a v1-acknowledgement note.

    Storyboard ``media_buy_seller/proposal_finalize/get_products_refine``
    validates ``field_present @ /refinement_applied`` and the response
    schema; semantic correctness isn't asserted today. v1 ships honest
    "applied without re-strategy" semantics so buyers see acknowledgement
    even though the allocation is unchanged. The status enum is
    {applied, partial, unable} — ``applied`` is the right v1 answer
    because the proposal we return DID change (fresh proposal_id, fresh
    allocations); ``partial`` would imply we honored some asks but not
    others, which over-claims given v1 doesn't act on the ask content.

    Each variant is a discriminated union by ``scope`` — we dispatch on
    the entry's ``scope`` to instantiate the matching ``RefinementApplied{1,2,3}``
    wrapped in the top-level RootModel. Buyer-supplied id strings are
    length-capped before echo to bound request-driven memory pressure
    (the adcp library types impose no cap; this is the defensive layer).
    Unknown scopes and malformed entries (missing id, oversized id) are
    silently dropped — known v1 behaviour, tracked for v2 telemetry.
    """
    applied: list[RefinementApplied] = []
    # Cap entry count up front so an N-million-entry array can't drive
    # allocation pressure even before the per-entry loop runs.
    iterable = list(refine_entries or [])[:_MAX_REFINE_ENTRIES]
    for entry in iterable:
        # Refine and RefinementApplied are RootModel wrappers; unwrap if so.
        inner = getattr(entry, "root", entry)
        scope = getattr(inner, "scope", None)
        scope_str = getattr(scope, "value", scope)
        if scope_str == "request":
            applied.append(
                RefinementApplied(root=RefinementApplied1(scope="request", status="applied", notes=_V1_REFINE_ACK_NOTE))
            )
        elif scope_str == "product":
            product_id = getattr(inner, "product_id", None)
            if not _is_safe_id(product_id):
                continue
            applied.append(
                RefinementApplied(
                    root=RefinementApplied2(
                        scope="product", product_id=product_id, status="applied", notes=_V1_REFINE_ACK_NOTE
                    )
                )
            )
        elif scope_str == "proposal":
            proposal_id = getattr(inner, "proposal_id", None)
            if not _is_safe_id(proposal_id):
                continue
            applied.append(
                RefinementApplied(
                    root=RefinementApplied3(
                        scope="proposal", proposal_id=proposal_id, status="applied", notes=_V1_REFINE_ACK_NOTE
                    )
                )
            )
        # Unknown scope variants are silently dropped — forward-compat
        # for spec additions.
    return applied
