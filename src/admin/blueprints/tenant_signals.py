"""Admin blueprint for managing tenant signals.

Operator authoring surface for ``TenantSignal`` — the publisher's
first-party map of "what targeting can a buyer apply on this inventory."
Storefronts discover signals through the AdCP ``get_signals`` tool;
buyers reference the resulting ``signal_id`` in
``audience_include`` / ``audience_exclude`` on ``create_media_buy``.

UX (post-#465):

- **Landing** (``/signals/``) is the operator's primary surface. Three
  states depending on synced inventory:
  1. **No inventory** — empty state with a "Sync inventory first" CTA.
  2. **Has unmapped inventory** — bulk-map table on top (segments tab +
     KV-pairs tab) showing every synced GAM entity, with a "Mapped as X"
     badge inline for rows that already have a signal. Operator ticks
     boxes → ``POST /signals/bulk-create`` mints one TenantSignal per
     row in a single transaction.
  3. **Everything mapped** — just the existing signals library below
     the bulk panel.
- **Composite builder** (``/signals/composite``) is the rare-path
  surface: multi-key AND, OR-groups, exclude. Embeds the same
  ``TargetingWidget`` product authoring uses.
- **Detail / edit** (``/signals/<signal_id>/edit``) — rename, edit
  description, delete. Where ``signal_id`` is visible (monospace,
  copyable) for ops debugging.

The legacy ``/signals/add`` route is gone — see #458/#462/#464 in git
history if you need the source-picker UI back.
"""

from __future__ import annotations

import json
import logging
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.signal_id import unique_signal_id
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantSignal
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

tenant_signals_bp = Blueprint("tenant_signals", __name__)

_VALID_VALUE_TYPES = ("binary", "categorical", "numeric")
_SIGNAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _parse_float(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


# ---------------------------------------------------------------------------
# Landing page — bulk-map + existing library
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/")
@require_tenant_access()
def list_signals(tenant_id: str):
    """Signals library landing.

    Three rendered states (see module docstring). All three are the same
    template — the conditional branches inside it decide what to show.
    """
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))

        signal_repo = TenantSignalRepository(session, tenant_id)
        segment_index, kv_index = signal_repo.mapped_index()

        # Load synced GAM inventory rows (the bulk-map source). Empty when
        # the tenant hasn't synced — that's State A (the "sync first" CTA).
        gam_repo = GAMSyncRepository(session, tenant_id)
        segments_rows = gam_repo.list_inventory("audience_segment")
        keys_rows = gam_repo.list_inventory("custom_targeting_key")

        segments = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "size": (row.inventory_metadata or {}).get("size"),
                "type": (row.inventory_metadata or {}).get("type"),
                "mapped_signal_id": (
                    segment_index[row.inventory_id].signal_id if row.inventory_id in segment_index else None
                ),
                "mapped_signal_name": (
                    segment_index[row.inventory_id].name if row.inventory_id in segment_index else None
                ),
            }
            for row in segments_rows
        ]
        keys = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "display_name": (row.inventory_metadata or {}).get("display_name") or row.name,
                "type": (row.inventory_metadata or {}).get("type", "UNKNOWN"),
            }
            for row in keys_rows
        ]

        rows = signal_repo.list_all()
        signals = [
            {
                "signal_id": row.signal_id,
                "name": row.name,
                "description": row.description,
                "value_type": row.value_type,
                "categories": row.categories or [],
                "adapter_kind": (row.adapter_config or {}).get("kind"),
                "is_composed": (row.adapter_config or {}).get("type") == "composed",
                "is_complex": (row.adapter_config or {}).get("kind") == "gam_targeting_groups",
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    has_inventory = bool(segments or keys)
    return render_template(
        "tenant_signals_list.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        signals=signals,
        segments=segments,
        keys=keys,
        kv_index_size=len(kv_index),
        has_inventory=has_inventory,
    )


# ---------------------------------------------------------------------------
# Bulk-map: turn ticked GAM entities into TenantSignal rows in one txn
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/bulk-create", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("bulk_create_tenant_signals")
def bulk_create(tenant_id: str):
    """Mint one TenantSignal per ticked row from the bulk-map UI.

    Request: JSON ``{"items": [{"kind": "audience_segment"|
    "custom_key_value", ...source-specific ids...}, ...]}``.
    For segments: ``{"kind": "audience_segment", "segment_id":
    "...", "segment_name": "..."}``. For KVs: ``{"kind":
    "custom_key_value", "key_id": "...", "value_id": "...",
    "key_name": "...", "value_name": "..."}``.

    Names auto-derive from the supplied display names; ``signal_id``
    slugified + collision-disambiguated. Rows that would duplicate an
    existing mapping silently skip — the UI already prevented re-checking
    them, this is the defensive backstop.

    Returns JSON ``{"created": N, "skipped": [signal_id, ...]}``.
    """
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items must be a non-empty list"}), 400

    created: list[str] = []
    skipped: list[str] = []
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return jsonify({"error": "tenant not found"}), 404
        repo = TenantSignalRepository(session, tenant_id)
        segment_index, kv_index = repo.mapped_index()

        for item in items:
            kind = item.get("kind")
            if kind == "audience_segment":
                segment_id = str(item.get("segment_id") or "")
                segment_name = str(item.get("segment_name") or f"Segment {segment_id}")
                if not segment_id:
                    continue
                if segment_id in segment_index:
                    skipped.append(segment_index[segment_id].signal_id)
                    continue
                signal_id = unique_signal_id(segment_name, exists=lambda sid: repo.get_by_id(sid) is not None)
                signal = TenantSignal(
                    tenant_id=tenant_id,
                    signal_id=signal_id,
                    name=segment_name,
                    value_type="binary",
                    adapter_config={
                        "type": "passthrough",
                        "kind": "audience_segment",
                        "segment_id": segment_id,
                    },
                    data_provider="publisher",
                    targeting_dimension="audience",
                )
                repo.add(signal)
                created.append(signal_id)
            elif kind == "custom_key_value":
                key_id = str(item.get("key_id") or "")
                value_id = str(item.get("value_id") or "")
                key_name = str(item.get("key_name") or f"Key {key_id}")
                value_name = str(item.get("value_name") or f"Value {value_id}")
                if not key_id or not value_id:
                    continue
                if (key_id, value_id) in kv_index:
                    skipped.append(kv_index[(key_id, value_id)].signal_id)
                    continue
                derived_name = f"{key_name}={value_name}"
                signal_id = unique_signal_id(derived_name, exists=lambda sid: repo.get_by_id(sid) is not None)
                signal = TenantSignal(
                    tenant_id=tenant_id,
                    signal_id=signal_id,
                    name=derived_name,
                    value_type="binary",
                    adapter_config={
                        "type": "passthrough",
                        "kind": "custom_key_value",
                        "key_id": key_id,
                        "value_id": value_id,
                    },
                    data_provider="publisher",
                )
                repo.add(signal)
                created.append(signal_id)
            else:
                return jsonify({"error": f"unsupported kind: {kind!r}"}), 400

        session.commit()

    return jsonify({"created": len(created), "skipped": skipped, "signal_ids": created})


# ---------------------------------------------------------------------------
# Detail / edit / delete
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/<signal_id>/edit", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("update_tenant_signal")
def edit_signal(tenant_id: str, signal_id: str):
    """Edit name / description / advanced config of an existing signal.

    Detail surface that doubles as edit. Operators reach this via the
    library list. ``signal_id`` is shown here (monospace, copyable) — the
    one place it's legitimately needed for debugging integrations.
    """
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

        if request.method == "GET":
            return render_template(
                "tenant_signals_edit.html",
                tenant_id=tenant_id,
                signal=signal,
                form_data=None,
                errors=None,
                value_types=_VALID_VALUE_TYPES,
            )

        form_data, errors, parsed = _validate_edit_form(request.form)
        if errors:
            return render_template(
                "tenant_signals_edit.html",
                tenant_id=tenant_id,
                signal=signal,
                form_data=form_data,
                errors=errors,
                value_types=_VALID_VALUE_TYPES,
            )
        for field, value in parsed.items():
            setattr(signal, field, value)
        session.commit()
    flash(f"Signal {signal_id!r} updated.", "success")
    return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))


@tenant_signals_bp.route("/<signal_id>/delete", methods=["POST", "DELETE"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("delete_tenant_signal")
def delete_signal(tenant_id: str, signal_id: str):
    with get_db_session() as session:
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
        else:
            repo.delete(signal)
            session.commit()
            flash(f"Signal {signal_id!r} deleted.", "success")
    return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Edit-form validation (light — only name / description / advanced JSON)
# ---------------------------------------------------------------------------


def _validate_edit_form(form) -> tuple[dict, dict, dict]:
    """Edit form is light: name (required), description, optional
    advanced-JSON for hand-authored rows. ``signal_id`` is immutable
    (buyer-referenced handle).
    """
    form_data = {
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "value_type": (form.get("value_type") or "").strip(),
        "categories": (form.get("categories") or "").strip(),
        "range_min": (form.get("range_min") or "").strip(),
        "range_max": (form.get("range_max") or "").strip(),
        "targeting_dimension": (form.get("targeting_dimension") or "").strip(),
        "data_provider": (form.get("data_provider") or "").strip(),
        "adapter_config": form.get("adapter_config") or "",
    }
    errors: dict[str, str] = {}
    parsed: dict = {}

    if not form_data["name"]:
        errors["name"] = "Name is required."
    else:
        parsed["name"] = form_data["name"]
    parsed["description"] = form_data["description"] or None
    parsed["targeting_dimension"] = form_data["targeting_dimension"] or None
    parsed["data_provider"] = form_data["data_provider"] or None

    if form_data["value_type"]:
        if form_data["value_type"] not in _VALID_VALUE_TYPES:
            errors["value_type"] = f"value_type must be one of {', '.join(_VALID_VALUE_TYPES)}."
        else:
            parsed["value_type"] = form_data["value_type"]

    if form_data["categories"]:
        parsed["categories"] = _parse_csv(form_data["categories"])

    if form_data["range_min"] or form_data["range_max"]:
        try:
            parsed["range_min"] = _parse_float(form_data["range_min"])
            parsed["range_max"] = _parse_float(form_data["range_max"])
        except ValueError:
            errors["range"] = "range_min and range_max must be numeric or empty."

    if form_data["adapter_config"]:
        try:
            adapter_config = json.loads(form_data["adapter_config"])
            if not isinstance(adapter_config, dict):
                raise ValueError("adapter_config must be a JSON object.")
            parsed["adapter_config"] = adapter_config
        except (ValueError, json.JSONDecodeError) as exc:
            errors["adapter_config"] = f"Invalid JSON: {exc}"

    return form_data, errors, parsed
