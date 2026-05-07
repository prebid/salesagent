"""Mock seller platform — first milestone target.

Subclasses ``DecisioningPlatform`` and implements the
``sales-non-guaranteed`` storyboard surface end-to-end against an
in-process media-buy store. Sufficient to drive the @adcp/sdk
``media_buy_seller`` storyboard to a clean run.

State machine on a media buy::

    pending_creatives ──sync_creatives + creative_assignments──▶ pending_start
                                                                    │
                                                                    ▼
                              update_media_buy(canceled=True)    active
                                              │                     │
                                              ▼                     ▼
                                          canceled  ◀──update_media_buy(canceled)──┘

A buy created **with** creative_assignments populated on every package
goes straight to ``pending_start`` (no creative-sync gate). A buy
created **without** creative_assignments lands in
``pending_creatives``; a subsequent
``update_media_buy(packages=[{package_id, creative_assignments}])``
moves it forward.

Multi-worker note: the store is per-process, so a multi-worker
deployment splits buys across workers — fine for the storyboard
(single worker), insufficient for production. M3 wires this through
the existing salesagent ``MediaBuy`` ORM.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from adcp.decisioning import (
    AdcpError,
    DecisioningCapabilities,
    DecisioningPlatform,
    RequestContext,
)
from adcp.decisioning.capabilities import (
    Account as CapabilitiesAccount,
)
from adcp.decisioning.capabilities import (
    Adcp,
    IdempotencySupported,
    MediaBuy,
    SupportedProtocol,
)

from core.idempotency import get_idempotency_store
from core.platforms._delegate import (
    _delegate_get_media_buy_delivery,
    _delegate_get_products,
    _delegate_list_creative_formats,
    _delegate_list_creatives,
    _delegate_provide_performance_feedback,
)
from core.stores.accounts import SalesagentAccountStore

# Process-singleton idempotency store wired through ``core.idempotency``.
# Defaults to :class:`PgBackend` for cross-worker durable replay; tests
# set ``CORE_IDEMPOTENCY_BACKEND=memory`` for single-process isolation.
_IDEMPOTENCY = get_idempotency_store()


# In-process media-buy store. Keyed by media_buy_id, scoped to one
# process. The storyboard runner is single-worker, so this is
# sufficient. M3 swaps this for the existing salesagent MediaBuy ORM.
_MEDIA_BUYS: dict[str, dict[str, Any]] = {}


class MockSellerPlatform(DecisioningPlatform):
    """Reads products from the salesagent ``products`` table and runs
    a full ``sales-non-guaranteed`` lifecycle against an in-process
    store. Idempotent on every mutating method."""

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(supported_pricing_models=["cpm"]),
        supported_protocols=[SupportedProtocol.media_buy],
    )
    accounts = SalesagentAccountStore()

    # ─────────────────────────── get_products ────────────────────────

    async def get_products(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        """Delegate to ``src/core/tools/products.py:_get_products_impl``.

        That impl owns 760+ LOC of brief/brand/filters validation,
        brand-manifest policy enforcement, advertising-policy
        compliance, custom-product generation, dynamic variants,
        targeting allowlists, and format matching. Bypassing it would
        mean reimplementing all of that — the greenfield value-add is
        framework primitives, not duplicated business logic.
        """
        return await _delegate_get_products(req, ctx)

    # ─────────────────────────── create_media_buy ────────────────────

    @_IDEMPOTENCY.wrap
    async def create_media_buy(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        packages_in = _get_packages(req)
        if not packages_in:
            raise AdcpError(
                "INVALID_REQUEST",
                message="At least one package is required",
                field="packages",
                recovery="correctable",
            )

        tenant_id = ctx.account.metadata.get("tenant_id", "unknown")
        media_buy_id = f"mb_{tenant_id}_{secrets.token_hex(4)}"

        # Each package gets a stable internal package_id. Buyers refer
        # to packages by package_id on subsequent update_media_buy calls.
        # ``targeting_overlay`` is echoed verbatim — the storyboard's
        # inventory_list_targeting scenario asserts that property_list /
        # collection_list ids round-trip through create → get.
        packages_out: list[dict[str, Any]] = []
        for i, pkg in enumerate(packages_in):
            assignments = list(pkg.get("creative_assignments") or [])
            packages_out.append(
                {
                    "package_id": f"pkg_{i}",
                    "product_id": _first_product_id(pkg),
                    "pricing_option_id": pkg.get("pricing_option_id", "po-cpm-default"),
                    "budget": pkg.get("budget"),
                    "creative_assignments": assignments,
                    "targeting_overlay": pkg.get("targeting_overlay"),
                    "status": "pending",
                }
            )

        # State: pending_creatives if any package has no creatives,
        # else pending_start. The storyboard treats the unassigned-by-
        # any-package case as the "needs creatives" branch — every
        # package must have at least one creative_assignment to bypass
        # the gate.
        all_assigned = all(p["creative_assignments"] for p in packages_out)
        status = "pending_start" if all_assigned else "pending_creatives"

        record: dict[str, Any] = {
            "media_buy_id": media_buy_id,
            "status": status,
            "packages": packages_out,
            "tenant_id": tenant_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "context": _echo_context(req),
            "valid_actions": _valid_actions_for(status),
        }
        _MEDIA_BUYS[media_buy_id] = record
        return _project_media_buy(record)

    # ─────────────────────────── update_media_buy ────────────────────
    # @_IDEMPOTENCY.wrap re-enabled after adcp-client-python#567
    # taught the wrap to support arg-projected methods (the framework
    # dispatches update_media_buy as
    # ``method(self, media_buy_id=..., patch=..., ctx=...)`` rather than
    # the (self, params, context) shape the wrap originally required).
    # Salesagent task #35 closed.
    @_IDEMPOTENCY.wrap
    async def update_media_buy(
        self,
        media_buy_id: str,
        patch: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        # Error path raises AdcpError. The framework wire-projects to
        # the AdCP error envelope. NOTE: the AdcpError raise path
        # bypasses ``inject_context`` (mcp_tools.py:2030 only fires on
        # success returns), so error responses don't echo the buyer's
        # context.correlation_id today — tracked upstream as salesagent
        # #36. Returning ``{"adcp_error": ...}`` as a dict to capture
        # context echo trips FastMCP's per-tool output validator (which
        # only knows the success schema), so raise is the lesser evil.
        tenant_id = ctx.account.metadata.get("tenant_id", "unknown")
        record = _MEDIA_BUYS.get(media_buy_id)
        # Tenant scope: _MEDIA_BUYS is module-global. Same-tenant tools
        # match on stored tenant_id; cross-tenant access surfaces as
        # MEDIA_BUY_NOT_FOUND (don't leak existence to other tenants).
        if record is None or record.get("tenant_id") != tenant_id:
            raise AdcpError(
                "MEDIA_BUY_NOT_FOUND",
                message=f"media_buy_id={media_buy_id!r} not found",
                recovery="correctable",
                field="media_buy_id",
            )

        # Per-package patches FIRST: validate package_ids before any
        # state mutation. An unknown package_id is a correctable buyer
        # error and must surface PACKAGE_NOT_FOUND even when the buy is
        # already canceled (the storyboard's invalid_transitions phase
        # exercises both orderings).
        package_patches = list(_patch_get(patch, "packages") or [])
        if package_patches:
            existing = {p["package_id"]: p for p in record["packages"]}
            for pkg_patch in package_patches:
                pkg_id = (
                    getattr(pkg_patch, "package_id", None)
                    if not isinstance(pkg_patch, dict)
                    else pkg_patch.get("package_id")
                )
                if pkg_id is None or pkg_id not in existing:
                    raise AdcpError(
                        "PACKAGE_NOT_FOUND",
                        message=f"package_id={pkg_id!r} not found on media_buy_id={media_buy_id!r}",
                        recovery="correctable",
                        field="packages",
                    )

        # Cancel — terminal transition; double-cancel is NOT_CANCELLABLE.
        if _patch_get(patch, "canceled") is True:
            if record["status"] == "canceled":
                raise AdcpError(
                    "NOT_CANCELLABLE",
                    message=f"media_buy_id={media_buy_id!r} is already canceled — cannot cancel a terminal buy",
                    recovery="correctable",
                    field="canceled",
                )
            record["status"] = "canceled"
            reason = _patch_get(patch, "cancellation_reason")
            if reason:
                record["cancellation_reason"] = reason
            record["valid_actions"] = []

        # Pause / resume at the buy level. ``paused=True`` flips to
        # ``paused`` (unless terminal); ``paused=False`` resumes back to
        # ``active``. Resume from a non-paused state is a no-op (the
        # storyboard's invalid_transitions phase catches double-resume
        # via the state machine).
        elif _patch_get(patch, "paused") is True:
            if record["status"] not in {"canceled"}:
                record["status"] = "paused"
                record["valid_actions"] = _valid_actions_for("paused")
        elif _patch_get(patch, "paused") is False:
            if record["status"] == "paused":
                record["status"] = "active"
                record["valid_actions"] = _valid_actions_for("active")

        # Apply per-package patches now that package_ids are validated.
        if package_patches:
            existing = {p["package_id"]: p for p in record["packages"]}
            for pkg_patch in package_patches:
                pkg_id = (
                    getattr(pkg_patch, "package_id", None)
                    if not isinstance(pkg_patch, dict)
                    else pkg_patch.get("package_id")
                )
                target = existing[pkg_id]
                assignments = (
                    pkg_patch.get("creative_assignments")
                    if isinstance(pkg_patch, dict)
                    else getattr(pkg_patch, "creative_assignments", None)
                )
                if assignments:
                    target["creative_assignments"] = list(assignments)
                # Per-package targeting overlay updates — buyers swap
                # property_list / collection_list ids by sending the
                # full targeting_overlay payload on the package patch.
                # We replace wholesale rather than merge; that matches
                # the storyboard's update_swap_lists step semantics.
                overlay = (
                    pkg_patch.get("targeting_overlay")
                    if isinstance(pkg_patch, dict)
                    else getattr(pkg_patch, "targeting_overlay", None)
                )
                if overlay is not None:
                    target["targeting_overlay"] = overlay.model_dump() if hasattr(overlay, "model_dump") else overlay
                paused = pkg_patch.get("paused") if isinstance(pkg_patch, dict) else getattr(pkg_patch, "paused", None)
                if paused is not None:
                    target["paused"] = bool(paused)

            # If every package now has creatives, advance from
            # pending_creatives → pending_start.
            if record["status"] == "pending_creatives" and all(p["creative_assignments"] for p in record["packages"]):
                record["status"] = "pending_start"
                record["valid_actions"] = _valid_actions_for("pending_start")

        record["updated_at"] = datetime.now(UTC).isoformat()
        return _project_media_buy(record)

    # ─────────────────────────── sync_creatives ──────────────────────

    @_IDEMPOTENCY.wrap
    async def sync_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],  # noqa: ARG002 — ctx unused in stub
    ) -> dict[str, Any]:
        creatives = getattr(req, "creatives", None) or []
        # Per sync-creatives-response.json each entry needs ``action``
        # (created | updated | unchanged) plus ``status`` from the
        # creative-status enum (processing, pending_review, approved,
        # rejected, archived).
        return {
            "creatives": [
                {
                    "creative_id": (
                        c.creative_id if hasattr(c, "creative_id") else c.get("creative_id") or _new_creative_id()
                    ),
                    "action": "created",
                    "status": "approved",
                }
                for c in creatives
            ],
        }

    # ─────────────────────────── get_media_buys ──────────────────────

    def get_media_buys(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        tenant_id = ctx.account.metadata.get("tenant_id", "unknown")
        ids = list(getattr(req, "media_buy_ids", None) or [])
        if not ids and isinstance(req, dict):
            ids = list(req.get("media_buy_ids") or [])

        # Buyer-supplied id list filters the response. Per spec, missing
        # ids are NOT an error in get_media_buys — they're simply absent
        # from the response. (MEDIA_BUY_NOT_FOUND is the right shape on
        # update / cancel where a single id targets a specific record.)
        out: list[dict[str, Any]] = []
        for mb_id in ids:
            record = _MEDIA_BUYS.get(mb_id)
            if record is not None and record.get("tenant_id") == tenant_id:
                out.append(_project_media_buy(record))

        return {"media_buys": out}

    # ─────────────────────────── get_media_buy_delivery ──────────────

    async def get_media_buy_delivery(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_media_buy_delivery(req, ctx)

    # ───────────────────── v6.0-rc.1 SalesPlatform Protocol methods ────
    # These were soft-warned as missing because MockSellerPlatform didn't
    # register them on the class even though delegates exist. Full
    # delegation to the same _impl functions GamPlatform uses — keeps the
    # storyboard runner exercising the real listing + feedback paths.

    async def list_creative_formats(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creative_formats(req, ctx)

    async def list_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creatives(req, ctx)

    async def provide_performance_feedback(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_provide_performance_feedback(req, ctx)


# ────────────────────────── helpers ──────────────────────────────────


def _get_packages(req: Any) -> list[dict[str, Any]]:
    if hasattr(req, "packages"):
        packages = req.packages or []
        return [p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in packages]
    if isinstance(req, dict):
        return list(req.get("packages") or [])
    return []


def _first_product_id(pkg: dict[str, Any]) -> str:
    """Per spec: package.product_id is a single string. Some buyers
    historically used ``products: [...]`` (list); accept both."""
    if pkg.get("product_id"):
        return str(pkg["product_id"])
    products = pkg.get("products")
    if isinstance(products, list) and products:
        return str(products[0])
    return "unknown"


def _patch_get(patch: Any, key: str) -> Any:
    """Return ``patch[key]`` only when the field was *explicitly set*.

    The generated UpdateMediaBuyRequest declares
    ``canceled: Literal[True] = True`` — i.e. the Pydantic default is
    ``True`` whenever the buyer didn't include the field in the wire
    payload. A naive ``getattr`` would treat every patch as a cancel.

    For Pydantic models we consult ``model_fields_set`` (the names of
    fields that landed via the inbound JSON, not via defaults). For
    raw dicts we just use ``.get`` since dicts don't carry phantom
    defaults.
    """
    if isinstance(patch, dict):
        return patch.get(key)
    fields_set = getattr(patch, "model_fields_set", None)
    if isinstance(fields_set, set) and key not in fields_set:
        return None
    return getattr(patch, key, None)


def _echo_context(req: Any) -> dict[str, Any] | None:
    """Echo back the buyer's ``context`` envelope so subsequent
    storyboard checks can match correlation ids."""
    ctx = _patch_get(req, "context")
    if ctx is None:
        return None
    if hasattr(ctx, "model_dump"):
        return ctx.model_dump()
    if isinstance(ctx, dict):
        return dict(ctx)
    return None


def _valid_actions_for(status: str) -> list[str]:
    """Return the buyer-actionable next operations for a given state.

    Values come from ``schemas/enums/media-buy-valid-action.json``:
    ``pause, resume, cancel, update_budget, update_dates,
    update_packages, add_packages, sync_creatives``. Read-only
    operations like ``get_media_buys`` and ``get_media_buy_delivery``
    are always available and intentionally NOT in this enum (a
    buyer never needs the seller's permission to read).
    """
    if status == "pending_creatives":
        return [
            "sync_creatives",
            "update_packages",
            "update_dates",
            "update_budget",
            "cancel",
        ]
    if status == "pending_start":
        return [
            "update_packages",
            "update_dates",
            "update_budget",
            "add_packages",
            "cancel",
        ]
    if status == "active":
        return [
            "pause",
            "update_packages",
            "update_budget",
            "add_packages",
            "cancel",
        ]
    if status == "paused":
        return [
            "resume",
            "update_packages",
            "update_budget",
            "cancel",
        ]
    if status == "canceled":
        return []  # terminal
    return []


def _project_media_buy(record: dict[str, Any]) -> dict[str, Any]:
    """Project the internal record onto the AdCP wire shape. Strips
    internal-only fields like ``tenant_id``. ``currency`` and
    ``total_budget`` are required on the get_media_buys media-buy
    schema."""
    total_budget = sum(float(p.get("budget") or 0) for p in record.get("packages") or [])
    out = {
        "media_buy_id": record["media_buy_id"],
        "status": record["status"],
        "currency": record.get("currency", "USD"),
        "total_budget": total_budget,
        "packages": [_project_package(p) for p in record["packages"]],
        "valid_actions": record.get("valid_actions") or [],
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }
    if record.get("context"):
        out["context"] = record["context"]
    if record.get("cancellation_reason"):
        out["cancellation_reason"] = record["cancellation_reason"]
    return out


def _project_package(p: dict[str, Any]) -> dict[str, Any]:
    """Project an internal package record onto the AdCP wire shape.
    Drops internal-only fields and only includes optional wire fields
    that were actually set."""
    out: dict[str, Any] = {
        "package_id": p["package_id"],
        "product_id": p["product_id"],
        "pricing_option_id": p["pricing_option_id"],
        "creative_assignments": p.get("creative_assignments") or [],
    }
    if p.get("budget") is not None:
        out["budget"] = p["budget"]
    if p.get("targeting_overlay") is not None:
        out["targeting_overlay"] = p["targeting_overlay"]
    return out


def _new_creative_id() -> str:
    return f"creative_{uuid.uuid4().hex[:12]}"


def _adcp_error(
    code: str,
    message: str,
    *,
    field: str | None = None,
    recovery: str = "correctable",
) -> dict[str, Any]:
    """Build the AdCP L3 error envelope as a dict.

    Returning this from a handler (instead of raising :class:`AdcpError`)
    routes through the framework's success-path serializer, which calls
    ``inject_context`` to echo the buyer's context onto the response.
    The :class:`AdcpError` raise path bypasses ``inject_context`` today
    — tracked upstream.
    """
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "recovery": recovery,
    }
    if field is not None:
        payload["field"] = field
    return {"adcp_error": payload}
