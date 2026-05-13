"""Model-layer write guard for platform-managed tenant surfaces.

Sprint 1 of [embedded-mode](../../../docs/design/embedded-mode.md):
the boundary between platform-managed and publisher-managed surfaces is
infrastructure vs. business. Platform-managed surfaces (Tenant core columns,
AdapterConfig) are locked to the Tenant Management API on tenants flagged
`is_embedded=True`. Publisher-managed surfaces (Product, Principal,
Creative, Workflow, etc.) remain writable from the UI for embedded tenants.

Enforcement: SQLAlchemy mapper-level `before_insert`/`before_update` listeners
inspect the active session for an authorization flag set by the management
API entrypoint (or a super-admin override for ops). Any write to a
platform-managed surface from any other code path raises
:class:`EmbeddedTenantWriteError`.

API endpoints set the flag on entry::

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        ...

Super-admin tooling sets ``session.info["super_admin_override"] = True`` for
emergency manual mutations.

Platform-spawned background workers (e.g. inventory sync started by the
management API's first-sync-on-provision hook) set
``session.info["platform_background_worker"] = True``. These workers are
acting on behalf of the platform — not the publisher UI — so writes to
platform-managed surfaces like ``adapter_config.custom_targeting_keys``
must pass the guard. The flag is distinct from ``management_api_caller``
so audit logs can still tell synchronous-API and async-worker mutations
apart.

The closed set of authorized ``platform_background_worker`` call sites
(everything else is either publisher UI — blocked by the embedded-mode
middleware — or open-instance code that the guard short-circuits):

- :func:`src.services.background_sync_service._sync_session` —
  inventory + targeting-key + advertisers sync workers, kicked off by
  ``/provision``, ``/refresh``, and the admin-button path.
- :meth:`src.adapters.gam.managers.targeting.GAMTargetingManager.sync_custom_targeting_keys`
  — adapter-layer cache rebuild for the same ``custom_targeting_keys``
  field. Currently dormant (no in-code callers) but flagged for parity.

Adding a new call site is a trust expansion — it should write a
genuinely platform-managed surface, not paper over a publisher-UI write
that would more correctly be routed through the management API.

Importing this module attaches the listeners as a side effect; no further
wiring is required.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from src.core.database.models import (
    AdapterConfig,
    Tenant,
    TenantSigningCredential,
    TenantSigningPolicy,
)


class EmbeddedTenantWriteError(Exception):
    """Raised when a non-API caller mutates a platform-managed surface on an embedded tenant."""


# Per-table allow-list of fields a non-API caller may still write on an embedded tenant.
# Anything not in this set (or any platform-managed table not listed at all) is locked.
#
# Tenant: platform-identity columns (name, billing_plan, is_active, subdomain,
# external_*) remain locked. The business-rules surface is publisher-managed per
# Sprint 5 design (docs/design/embedded-mode-sprint-5.md §"Pattern: shared
# business logic with the UI") — the publisher edits these in the proxied admin
# UI; the management API exposes the same writes for automation.
PUBLISHER_WRITABLE_FIELDS: dict[type, set[str]] = {
    Tenant: {
        # Business rules — written by /settings/business-rules POST
        "measurement_providers",
        "order_name_template",
        "line_item_name_template",
        "approval_mode",
        "creative_review_criteria",
        "creative_auto_approve_threshold",
        "creative_auto_reject_threshold",
        "ai_policy",
        "advertising_policy",
        "enable_axe_signals",
        "brand_manifest_policy",
        "product_ranking_prompt",
        "human_review_required",
    },
    # Sprint 1.8: gam_sandbox_advertiser_id is a runtime cache populated lazily
    # by the routing chain on first sandbox call (not a user-editable surface).
    # Routing-chain writes are internal infrastructure, not publisher UI traffic.
    #
    # gam_manual_approval_required / mock_manual_approval_required mirror the
    # tenant.human_review_required business rule onto the adapter config — same
    # publisher-managed setting, two storage locations kept in sync by the
    # /settings/business-rules handler.
    AdapterConfig: {
        "gam_sandbox_advertiser_id",
        "gam_manual_approval_required",
        "mock_manual_approval_required",
    },
}


_AUTH_FLAGS = ("management_api_caller", "super_admin_override", "platform_background_worker")


def _caller_is_authorized(target: Any, connection: Any) -> bool:
    """Return True if the active session/connection is allowed to mutate platform-managed state.

    Accepts the flag from either the session ``info`` dict (typical: API endpoints set it
    on the session they're using) or the connection ``info`` dict (fallback for code paths
    that operate at the Core SQL level without a Session). Either is sufficient.
    """
    # Prefer the session attached to the target — that's what API endpoints actually mutate.
    session = Session.object_session(target)
    session_info = getattr(session, "info", None)
    if session_info and any(session_info.get(flag) for flag in _AUTH_FLAGS):
        return True

    connection_info = getattr(connection, "info", None)
    if connection_info and any(connection_info.get(flag) for flag in _AUTH_FLAGS):
        return True

    return False


def _changed_fields(mapper, target) -> set[str]:
    """Return the set of *column* attribute names that have unsaved changes on ``target``.

    Only column properties are inspected. Relationship properties are excluded — adding
    a child row (Product, Principal, etc.) shows up in the parent Tenant's relationship
    history, but represents a publisher-managed write, not a platform-managed mutation.
    """
    changed = set()
    for col in mapper.column_attrs:
        history = get_history(target, col.key)
        if history.has_changes():
            changed.add(col.key)
    return changed


def _resolve_embedded_flag(target: Any, connection: Any) -> bool:
    """Return True if the parent tenant of ``target`` is flagged ``is_embedded``."""
    if isinstance(target, Tenant):
        return bool(target.is_embedded)

    tenant_id = getattr(target, "tenant_id", None)
    if not tenant_id:
        return False

    result = connection.execute(select(Tenant.is_embedded).where(Tenant.tenant_id == tenant_id)).scalar()
    return bool(result)


def _enforce(mapper, connection, target, *, op: str) -> None:
    """Block ``op`` on ``target`` unless the caller is authorized.

    Allows the write through unchanged when:
    - The parent tenant is not flagged is_embedded (open-instance tenant), or
    - The caller has set ``management_api_caller`` or ``super_admin_override``, or
    - For updates, every changed field is in the publisher-writable allow-list for
      this model.
    """
    if not _resolve_embedded_flag(target, connection):
        return

    if _caller_is_authorized(target, connection):
        return

    changed = _changed_fields(mapper, target) if op == "update" else set()
    writable = PUBLISHER_WRITABLE_FIELDS.get(type(target), set())

    # Update with no actual column changes is a relationship-only flush (e.g. a child
    # Product was added to the Tenant). Those are publisher-managed writes by definition,
    # so don't block them.
    if op == "update" and not changed:
        return

    if op == "update" and changed.issubset(writable):
        return

    detail = sorted(changed) if changed else "(insert)"
    raise EmbeddedTenantWriteError(
        f"{type(target).__name__} for tenant "
        f"{getattr(target, 'tenant_id', '?')!r} is platform-managed; "
        f"changes to {detail} must go through the Tenant Management API."
    )


@event.listens_for(Tenant, "before_update")
def _block_tenant_update(mapper, connection, target):
    _enforce(mapper, connection, target, op="update")


@event.listens_for(Tenant, "before_insert")
def _block_tenant_insert(mapper, connection, target):
    # Inserts are only blocked when the new row itself is flagged is_embedded;
    # creating a non-embedded tenant from any code path is fine.
    if not getattr(target, "is_embedded", False):
        return
    if _caller_is_authorized(target, connection):
        return
    raise EmbeddedTenantWriteError("Inserting a Tenant with is_embedded=True requires the Tenant Management API.")


@event.listens_for(AdapterConfig, "before_update")
def _block_adapter_config_update(mapper, connection, target):
    _enforce(mapper, connection, target, op="update")


@event.listens_for(AdapterConfig, "before_insert")
def _block_adapter_config_insert(mapper, connection, target):
    _enforce(mapper, connection, target, op="insert")


# Note: PublisherPartner is intentionally NOT guarded here — the partner
# roster is publisher-managed (the embedded admin UI lets each tenant add
# its own publishers via /publisher-partners, opted in with
# ``allow_embedded_writes=True`` on the four mutation routes). If a future
# design decision flips that to platform-managed, also remove
# ``allow_embedded_writes=True`` from src/admin/blueprints/publisher_partners.py
# so the gate is consistent across layers.

# Signing infrastructure (signing-non-embedded design) is platform-managed —
# per-tenant signing policy and the salesagent's own outbound signing
# credentials. Both are infrastructure surfaces; publisher UI never writes
# them on embedded tenants.
for _signing_model in (
    TenantSigningPolicy,
    TenantSigningCredential,
):

    @event.listens_for(_signing_model, "before_update")
    def _block_signing_update(mapper, connection, target):
        _enforce(mapper, connection, target, op="update")

    @event.listens_for(_signing_model, "before_insert")
    def _block_signing_insert(mapper, connection, target):
        _enforce(mapper, connection, target, op="insert")
