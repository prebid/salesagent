"""Buyer Routing page — Sprint 5 workstream B (skeleton) + C (editor) + E (promote).

Renders ``/tenant/<tenant_id>/buyer-routing`` with three sections:
default GAM advertiser, routing rules, and recent activity.

Workstream C wires the editor: a searchable GAM-advertiser picker, default
advertiser save, and routing-rule CRUD modal. The page handler renders
read-only state and exposes session-authenticated JSON sub-endpoints under
the same tenant scope so the in-page JS can call them via session cookies
(the tenant-management API key is server-to-server only and must never
reach the browser).

Workstream E adds promote-from-activity wiring: each activity row carries
its (agent, operator, brand_house, brand_id) so the JS can prefill the
Add Rule modal.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime

from flask import Blueprint, abort, jsonify, render_template, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.admin.api_schemas.tenant_management import (
    CreateBuyerAdvertiserMappingRequest,
    UpdateBuyerAdvertiserMappingRequest,
)
from src.admin.services.tenant_status_service import invalidate_status_cache
from src.admin.tenant_management_api import (
    _is_routing_rule_unique_violation,
    _routing_rule_to_mapping,
    _validate_gam_advertiser_id,
)
from src.admin.utils import require_tenant_access
from src.admin.utils.embedded_mode_auth import is_embedded_view
from src.core.database.database_session import get_db_session
from src.core.database.embedded_tenant_guard import EmbeddedTenantWriteError
from src.core.database.models import (
    AdvertiserRoutingRule,
    GamAdvertiser,
    Principal,
    SyncJob,
    Tenant,
)
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.services.gam_advertisers_sync import sync_advertisers
from src.services.recent_buyers_service import compute_recent_buyers

logger = logging.getLogger(__name__)

buyer_routing_bp = Blueprint("buyer_routing", __name__, url_prefix="/tenant")


# Bootstrap badge classes per ``Account.resolved_via`` enum value. The
# design doc pins these colors so publishers reading the activity table
# learn the routing chain at a glance — amber rows are fall-throughs to
# the tenant default, the publisher's "should I promote this to a rule?"
# decision point.
RESOLVED_VIA_BADGE: dict[str, str] = {
    "exact": "bg-success",  # green
    "house": "bg-primary",  # blue
    "operator": "bg-info",  # teal
    "default": "bg-warning",  # amber — the fall-through cohort
    "account": "bg-purple",  # purple — pre-mapped via /accounts
    "unknown": "bg-secondary",  # grey — legacy NULL rows
    "sandbox": "bg-dark",  # slate — sandbox carve-out
}


def _badge_class_for(resolved_via: str) -> str:
    return RESOLVED_VIA_BADGE.get(resolved_via, "bg-secondary")


def _api_error_json(code: str, message: str, status: int, details: dict | None = None):
    """Compact JSON error matching the tenant-management ApiError shape."""
    body: dict = {"error": code, "message": message}
    if details is not None:
        body["details"] = details
    return jsonify(body), status


def _resolve_advertiser_names(session, tenant_id: str, ids: set[str]) -> dict[str, dict]:
    """Batch-load name + status for every referenced advertiser id.

    Returns ``{id: {"name": str, "status": str}}``. Missing rows are simply
    absent from the dict — callers fall back to rendering the raw id.
    Empty ``ids`` short-circuits to avoid a wasted round trip.
    """
    if not ids:
        return {}
    # FIXME(embedded-mode-sprint-5-piece-C: fold into BuyerRoutingService):
    # batch advertiser-name resolution lives here today; lift into a
    # service module when the page handler grows beyond a single function.
    rows = session.scalars(
        select(GamAdvertiser).where(
            GamAdvertiser.tenant_id == tenant_id,
            GamAdvertiser.advertiser_id.in_(ids),
        )
    ).all()
    return {r.advertiser_id: {"name": r.name, "status": r.status} for r in rows}


@buyer_routing_bp.route("/<tenant_id>/buyer-routing", strict_slashes=False)
@require_tenant_access()
def buyer_routing_page(tenant_id: str):
    """Render the buyer-routing page.

    Server-side: read tenant, rules, recent activity, batch-resolve
    advertiser names from the local cache. Client-side: in-page JS calls
    the JSON sub-endpoints below for picker search + CRUD.
    """
    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            abort(404, description=f"Tenant {tenant_id!r} not found")

        rules = session.scalars(
            select(AdvertiserRoutingRule)
            .filter_by(tenant_id=tenant_id)
            # Most-specific first matches the resolution chain ordering
            # in src/services/buyer_advertiser_routing.py — exact wins
            # over house wildcard wins over operator wildcard.
            .order_by(
                AdvertiserRoutingRule.brand_id.is_(None).asc(),
                AdvertiserRoutingRule.brand_house.is_(None).asc(),
                AdvertiserRoutingRule.operator_domain.asc(),
            )
        ).all()

        rule_rows = [
            {
                # principal_id may not exist as a column yet — workstream A0
                # adds it in parallel. ``getattr`` lets us ship before/after
                # without a hard dependency.
                "principal_id": getattr(rule, "principal_id", None),
                "operator_domain": rule.operator_domain,
                "brand_house": rule.brand_house,
                "brand_id": rule.brand_id,
                "gam_advertiser_id": rule.gam_advertiser_id,
                "id": rule.id,
            }
            for rule in rules
        ]

        default_gam_advertiser_id = tenant.default_gam_advertiser_id
        is_embedded = is_embedded_view(tenant)
        tenant_name = tenant.name

        # Advertiser → buyer-agent assignments. Listed independently of
        # routing rules so the operator can see every synced advertiser
        # and pick which buyer agent should see its orders via
        # get_media_buys.
        advertiser_rows = session.scalars(
            select(GamAdvertiser)
            .where(GamAdvertiser.tenant_id == tenant_id)
            .order_by(GamAdvertiser.name.asc(), GamAdvertiser.advertiser_id.asc())
        ).all()
        principal_rows = session.scalars(
            select(Principal)
            .where(Principal.tenant_id == tenant_id)
            .order_by(Principal.name.asc(), Principal.principal_id.asc())
        ).all()
        principal_name_by_id = {p.principal_id: p.name for p in principal_rows}
        advertiser_assignments = [
            {
                "advertiser_id": r.advertiser_id,
                "name": r.name,
                "status": r.status,
                "principal_id": r.principal_id,
                "principal_name": principal_name_by_id.get(r.principal_id) if r.principal_id else None,
            }
            for r in advertiser_rows
        ]
        principals_for_picker = [{"principal_id": p.principal_id, "name": p.name} for p in principal_rows]

        recent_rows = compute_recent_buyers(tenant_id, days=30, limit=100)

        activity_rows: list[dict] = []
        sandbox_rows: list[dict] = []
        for row in recent_rows:
            view_row = {
                "principal_id": None,  # workstream A0 will plumb agent through Account
                "operator_domain": row.operator_domain,
                "brand_house": row.brand_house,
                "brand_id": row.brand_id,
                "last_seen_at": row.last_seen_at,
                "request_count": row.request_count,
                "resolved_gam_advertiser_id": row.resolved_gam_advertiser_id,
                "resolved_via": row.resolved_via,
                "badge_class": _badge_class_for(row.resolved_via),
            }
            if row.sandbox:
                sandbox_rows.append(view_row)
            else:
                activity_rows.append(view_row)

        # Collect all referenced advertiser ids so the picker preview, the
        # rules table, and the activity table all share one batch lookup.
        referenced_ids: set[str] = set()
        if default_gam_advertiser_id:
            referenced_ids.add(default_gam_advertiser_id)
        for r in rule_rows:
            if r["gam_advertiser_id"]:
                referenced_ids.add(r["gam_advertiser_id"])
        for a in activity_rows:
            if a["resolved_gam_advertiser_id"]:
                referenced_ids.add(a["resolved_gam_advertiser_id"])
        advertiser_names = _resolve_advertiser_names(session, tenant_id, referenced_ids)

    return render_template(
        "buyer_routing.html",
        tenant={
            "tenant_id": tenant_id,
            "name": tenant_name,
            "is_embedded": is_embedded,
            "default_gam_advertiser_id": default_gam_advertiser_id,
        },
        tenant_id=tenant_id,
        rules=rule_rows,
        activity=activity_rows,
        sandbox_rows=sandbox_rows,
        advertiser_names=advertiser_names,
        advertiser_assignments=advertiser_assignments,
        principals_for_picker=principals_for_picker,
    )


# ---------------------------------------------------------------------------
# JSON sub-endpoints — session-authenticated, called by the page's JS.
#
# These are NOT proxies. They share the ORM helpers with
# ``tenant_management_api.py`` (``_validate_gam_advertiser_id``,
# ``_routing_rule_to_mapping``, ``_is_routing_rule_unique_violation``) so
# the JSON shape and validation rules match the tenant-management API
# bit-for-bit. Tenant-management API keys are server-to-server secrets
# and MUST NOT reach the browser; admin pages authenticate via session
# cookies + ``require_tenant_access()``.
# ---------------------------------------------------------------------------


_ADVERTISER_PICKER_DEFAULT_LIMIT = 50
_ADVERTISER_PICKER_MAX_LIMIT = 200


@buyer_routing_bp.route("/<tenant_id>/buyer-routing/api/advertisers", methods=["GET"], strict_slashes=False)
@require_tenant_access(api_mode=True)
def search_advertisers(tenant_id: str):
    """Searchable read over the synced ``gam_advertisers`` cache.

    Session-authenticated mirror of ``GET /api/v1/tenant-management/tenants/
    {id}/gam/advertisers`` — same shape, same ``q`` semantics. Used by the
    in-page picker (default-advertiser save + Add/Edit Rule modals).
    """
    q_raw = (request.args.get("q") or "").strip()
    try:
        limit = int(request.args.get("limit", _ADVERTISER_PICKER_DEFAULT_LIMIT))
    except ValueError:
        limit = _ADVERTISER_PICKER_DEFAULT_LIMIT
    limit = max(1, min(_ADVERTISER_PICKER_MAX_LIMIT, limit))

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        # FIXME(embedded-mode-sprint-5-piece-C: fold into BuyerRoutingService)
        base = select(GamAdvertiser).where(GamAdvertiser.tenant_id == tenant_id)

        if q_raw and q_raw.isdigit():
            # Numeric → exact id match (single-result return path).
            base = base.where(GamAdvertiser.advertiser_id == q_raw)
        elif len(q_raw) >= 2:
            # Avoid the expensive scan from a single keystroke.
            base = base.where(func.lower(GamAdvertiser.name).contains(q_raw.lower()))

        rows = session.scalars(
            base.order_by(GamAdvertiser.name.asc(), GamAdvertiser.advertiser_id.asc()).limit(limit)
        ).all()

        advertisers = [
            {"id": r.advertiser_id, "name": r.name, "status": r.status, "currency_code": r.currency_code} for r in rows
        ]
    return jsonify({"advertisers": advertisers})


@buyer_routing_bp.route("/<tenant_id>/buyer-routing/api/default-advertiser", methods=["PATCH"], strict_slashes=False)
@require_tenant_access(api_mode=True, role=("admin", "member"), allow_embedded_writes=True)
def update_default_advertiser(tenant_id: str):
    """Set ``Tenant.default_gam_advertiser_id`` from the page's picker.

    Mirrors the relevant slice of ``PATCH /api/v1/tenant-management/tenants/
    {id}`` (``default_gam_advertiser_id`` field only). Body shape:
    ``{"default_gam_advertiser_id": "<id>"}``. Empty / missing → 400.
    """
    payload = request.get_json(silent=True) or {}
    if "default_gam_advertiser_id" not in payload:
        return _api_error_json(
            "invalid_default_advertiser",
            "default_gam_advertiser_id is required.",
            400,
        )
    new_id = payload.get("default_gam_advertiser_id")
    if new_id is not None and (not isinstance(new_id, str) or not new_id):
        return _api_error_json(
            "invalid_default_advertiser",
            "default_gam_advertiser_id must be a non-empty string or null.",
            400,
        )

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        # Reject ids that aren't in the synced cache (graceful when cache
        # is empty — same rule as POST /buyer-advertiser-mappings).
        if new_id is not None and not _validate_gam_advertiser_id(session, tenant_id, new_id):
            return _api_error_json(
                "invalid_advertiser_id",
                f"gam_advertiser_id {new_id!r} is not in the synced advertisers cache "
                f"for this tenant. Refresh the cache or pick an existing advertiser.",
                400,
                details={"gam_advertiser_id": new_id},
            )

        tenant.default_gam_advertiser_id = new_id
        tenant.updated_at = datetime.now(UTC)
        try:
            session.commit()
        except EmbeddedTenantWriteError as exc:
            session.rollback()
            return _api_error_json("managed_tenant_write_blocked", str(exc), 403)

        invalidate_status_cache(tenant_id)
        return jsonify({"default_gam_advertiser_id": new_id})


@buyer_routing_bp.route("/<tenant_id>/buyer-routing/api/rules", methods=["POST"], strict_slashes=False)
@require_tenant_access(api_mode=True, role=("admin", "member"), allow_embedded_writes=True)
def create_rule(tenant_id: str):
    """Session-authenticated routing-rule create — same body + errors as
    ``POST /api/v1/tenant-management/tenants/{id}/buyer-advertiser-mappings``.
    """
    payload = request.get_json(silent=True) or {}
    try:
        req = CreateBuyerAdvertiserMappingRequest(**payload)
    except (TypeError, ValueError) as exc:
        return _api_error_json("invalid_request", str(exc), 400)

    if req.brand_id is not None and req.brand_house is None:
        return _api_error_json(
            "brand_house_required",
            "brand_id requires brand_house — a brand-level rule must be scoped to a parent house.",
            400,
        )

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        if not _validate_gam_advertiser_id(session, tenant_id, req.gam_advertiser_id):
            return _api_error_json(
                "invalid_advertiser_id",
                f"gam_advertiser_id {req.gam_advertiser_id!r} is not in the synced advertisers cache for this tenant.",
                400,
                details={"gam_advertiser_id": req.gam_advertiser_id},
            )

        rule = AdvertiserRoutingRule(
            id=f"rule_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            principal_id=req.principal_id,
            operator_domain=req.operator_domain,
            brand_house=req.brand_house,
            brand_id=req.brand_id,
            gam_advertiser_id=req.gam_advertiser_id,
        )
        session.add(rule)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if _is_routing_rule_unique_violation(exc):
                return _api_error_json(
                    "routing_rule_duplicate",
                    "A routing rule with this (principal_id, operator_domain, brand_house, brand_id) "
                    "tuple already exists.",
                    409,
                    details={
                        "principal_id": req.principal_id,
                        "operator_domain": req.operator_domain,
                        "brand_house": req.brand_house,
                        "brand_id": req.brand_id,
                    },
                )
            raise
        invalidate_status_cache(tenant_id)
        session.refresh(rule)
        return jsonify(_routing_rule_to_mapping(rule).model_dump(mode="json")), 201


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/rules/<rule_id>",
    methods=["PATCH"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True, role=("admin", "member"), allow_embedded_writes=True)
def patch_rule(tenant_id: str, rule_id: str):
    """Session-authenticated routing-rule patch — same shape as the API."""
    payload = request.get_json(silent=True) or {}
    try:
        req = UpdateBuyerAdvertiserMappingRequest(**payload)
    except (TypeError, ValueError) as exc:
        return _api_error_json("invalid_request", str(exc), 400)

    with get_db_session() as session:
        rule = session.scalars(select(AdvertiserRoutingRule).filter_by(id=rule_id, tenant_id=tenant_id)).first()
        if rule is None:
            return _api_error_json(
                "routing_rule_not_found",
                f"Routing rule {rule_id!r} not found for tenant {tenant_id!r}",
                404,
            )

        if req.principal_id is not None:
            rule.principal_id = req.principal_id
        if req.brand_house is not None:
            rule.brand_house = req.brand_house
        if req.brand_id is not None:
            rule.brand_id = req.brand_id
        if req.gam_advertiser_id is not None:
            if not _validate_gam_advertiser_id(session, tenant_id, req.gam_advertiser_id):
                session.rollback()
                return _api_error_json(
                    "invalid_advertiser_id",
                    f"gam_advertiser_id {req.gam_advertiser_id!r} is not in the synced advertisers cache.",
                    400,
                    details={"gam_advertiser_id": req.gam_advertiser_id},
                )
            rule.gam_advertiser_id = req.gam_advertiser_id

        if rule.brand_id is not None and rule.brand_house is None:
            session.rollback()
            return _api_error_json(
                "brand_house_required",
                "brand_id requires brand_house — a brand-level rule must be scoped to a parent house.",
                400,
            )

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if _is_routing_rule_unique_violation(exc):
                return _api_error_json(
                    "routing_rule_duplicate",
                    "A routing rule with this (principal_id, operator_domain, brand_house, brand_id) "
                    "tuple already exists.",
                    409,
                    details={
                        "principal_id": rule.principal_id,
                        "operator_domain": rule.operator_domain,
                        "brand_house": rule.brand_house,
                        "brand_id": rule.brand_id,
                    },
                )
            raise
        invalidate_status_cache(tenant_id)
        session.refresh(rule)
        return jsonify(_routing_rule_to_mapping(rule).model_dump(mode="json"))


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/rules/<rule_id>",
    methods=["DELETE"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True, role=("admin", "member"), allow_embedded_writes=True)
def delete_rule(tenant_id: str, rule_id: str):
    """Session-authenticated routing-rule delete — 204 on success, 404 on
    miss. JS treats 404 as a benign race ("someone else deleted it")."""
    with get_db_session() as session:
        rule = session.scalars(select(AdvertiserRoutingRule).filter_by(id=rule_id, tenant_id=tenant_id)).first()
        if rule is None:
            return _api_error_json(
                "routing_rule_not_found",
                f"Routing rule {rule_id!r} not found for tenant {tenant_id!r}",
                404,
            )

        session.delete(rule)
        session.commit()
        invalidate_status_cache(tenant_id)
    return "", 204


# ---------------------------------------------------------------------------
# Advertiser → buyer-agent assignment.
#
# Independent of routing rules: routing rules map (operator, brand_house,
# brand_id) → advertiser, while these endpoints map advertiser → buyer
# agent. Used by get_media_buys to project gam_orders rows into the
# assigned agent's response.
# ---------------------------------------------------------------------------


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/advertiser-assignments",
    methods=["GET"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True)
def list_advertiser_assignments(tenant_id: str):
    """List GAM advertisers and their currently assigned buyer agent.

    Returns every active advertiser plus the principal_id (if any) the
    operator has assigned. Used to populate the assignment table in the
    buyer-routing page.
    """
    with get_db_session() as session:
        tenant_repo = TenantConfigRepository(session, tenant_id)
        if tenant_repo.get_tenant() is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        rows = GAMSyncRepository(session, tenant_id).list_advertisers()

        principal_ids = [r.principal_id for r in rows if r.principal_id]
        principals_by_id = tenant_repo.get_principal_names(principal_ids)

        advertisers = [
            {
                "advertiser_id": r.advertiser_id,
                "name": r.name,
                "status": r.status,
                "currency_code": r.currency_code,
                "principal_id": r.principal_id,
                "principal_name": principals_by_id.get(r.principal_id) if r.principal_id else None,
            }
            for r in rows
        ]
    return jsonify({"advertisers": advertisers})


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/advertiser-assignments/<advertiser_id>",
    methods=["PATCH"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True, role=("admin", "member"), allow_embedded_writes=True)
def patch_advertiser_assignment(tenant_id: str, advertiser_id: str):
    """Assign or clear the buyer agent for a GAM advertiser.

    Body: ``{"principal_id": "<id>" | null}``. Setting ``null`` clears
    the assignment so the advertiser's orders are no longer projected
    to any buyer.
    """
    payload = request.get_json(silent=True) or {}
    if "principal_id" not in payload:
        return _api_error_json(
            "invalid_request",
            "Body must include a 'principal_id' field (string or null).",
            400,
        )
    new_principal_id = payload["principal_id"]
    if new_principal_id is not None and not isinstance(new_principal_id, str):
        return _api_error_json(
            "invalid_request",
            "'principal_id' must be a string or null.",
            400,
        )
    if new_principal_id == "":
        new_principal_id = None

    with get_db_session() as session:
        gam_repo = GAMSyncRepository(session, tenant_id)
        tenant_repo = TenantConfigRepository(session, tenant_id)
        advertiser = gam_repo.get_advertiser(advertiser_id)
        if advertiser is None:
            return _api_error_json(
                "advertiser_not_found",
                f"GAM advertiser {advertiser_id!r} not found for tenant {tenant_id!r}",
                404,
            )

        if new_principal_id is not None:
            principal = tenant_repo.get_principal(new_principal_id)
            if principal is None:
                return _api_error_json(
                    "principal_not_found",
                    f"Principal {new_principal_id!r} not found for tenant {tenant_id!r}",
                    404,
                    details={"principal_id": new_principal_id},
                )

        advertiser.principal_id = new_principal_id
        session.commit()

        return jsonify(
            {
                "advertiser_id": advertiser_id,
                "principal_id": new_principal_id,
            }
        )


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/principals",
    methods=["GET"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True)
def list_principals(tenant_id: str):
    """List buyer agents available for advertiser assignment.

    Used to populate the agent dropdown in the assignment UI.
    """
    with get_db_session() as session:
        tenant_repo = TenantConfigRepository(session, tenant_id)
        if tenant_repo.get_tenant() is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        rows = tenant_repo.list_principals()
        principals = [{"principal_id": p.principal_id, "name": p.name} for p in rows]
    return jsonify({"principals": principals})


@buyer_routing_bp.route(
    "/<tenant_id>/buyer-routing/api/sync-advertisers",
    methods=["POST"],
    strict_slashes=False,
)
@require_tenant_access(api_mode=True, role=("admin", "member"))
def trigger_advertiser_sync(tenant_id: str):
    """Trigger a background GAM advertisers sync from the buyer-routing page.

    Creates a pending SyncJob, spawns a daemon thread, and returns the
    sync_id so the client can poll
    ``GET /tenant/<tenant_id>/gam/sync-status/<sync_id>`` for progress.
    """
    started_at = datetime.now(UTC)
    sync_id = f"sync_{tenant_id}_advertisers_{int(started_at.timestamp() * 1_000_000)}"

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            return _api_error_json("tenant_not_found", f"Tenant {tenant_id!r} does not exist", 404)

        job = SyncJob(
            sync_id=sync_id,
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            sync_type="advertisers",
            status="pending",
            started_at=started_at,
            triggered_by="admin_ui",
            triggered_by_id="sync_advertisers_button",
        )
        session.add(job)
        session.commit()

    def _run() -> None:
        try:
            sync_advertisers(tenant_id, sync_id=sync_id)
        except Exception:
            logger.exception("[%s] advertiser sync thread failed", sync_id)

    threading.Thread(target=_run, daemon=True, name=f"adv-sync-{sync_id}").start()
    return jsonify({"sync_id": sync_id})
