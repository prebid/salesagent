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
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.signal_id import unique_signal_id
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantSignal
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.database.repositories.signal_usage import SignalUsageRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

tenant_signals_bp = Blueprint("tenant_signals", __name__)

_VALID_VALUE_TYPES = ("binary", "categorical", "numeric")
_SIGNAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
# AdCP ``Tag`` regex — lowercase alphanumeric with `_` and `-` only.
_TAG_PATTERN = re.compile(r"^[a-z0-9_-]+$")
# Display labels for the (multi-)adapter source list on the bulk-map UI
# (#480). Keys match ``tenant.ad_server`` values.
_ADAPTER_LABELS = {
    "google_ad_manager": "Google Ad Manager",
    "freewheel": "Freewheel",
    "broadstreet": "Broadstreet",
    "springserve": "SpringServe",
    "mock": "Mock",
}


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _normalize_tags(raw: str | list[str] | None) -> list[str]:
    """Coerce input to a deduplicated, sorted list of valid tags.

    Accepts a comma/whitespace-separated string OR a list of strings.
    Validates each against the AdCP Tag pattern (lowercase alnum + ``_-``).
    Raises ValueError on invalid input — callers translate to a flash or
    400 response.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        pieces = [p.strip().lower() for p in re.split(r"[,\s]+", raw) if p.strip()]
    else:
        pieces = [str(p).strip().lower() for p in raw if str(p).strip()]
    bad = [t for t in pieces if not _TAG_PATTERN.match(t)]
    if bad:
        raise ValueError(f"invalid tag(s): {', '.join(bad)} (lowercase alnum + _- only)")
    return sorted(set(pieces))


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
    """Signals page — source-centric layout (PR 4 redesign).

    One card per source kind (Audience segments, Custom targeting keys,
    Composite signals). Every ad-server entity is a row; mapping state
    lives inline. The earlier "bulk-map vs library" split collapses
    into this single view.
    """
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))

        signal_repo = TenantSignalRepository(session, tenant_id)
        segment_index, kv_index = signal_repo.mapped_index()

        gam_repo = GAMSyncRepository(session, tenant_id)
        segments_rows = gam_repo.list_inventory("audience_segment")
        keys_rows = gam_repo.list_inventory("custom_targeting_key")

        signal_rows = signal_repo.list_all()
        usage_index = SignalUsageRepository(session, tenant_id).usage_index()

        def _mapped_payload(signal: TenantSignal) -> dict[str, Any]:
            usage = usage_index.get(signal.signal_id)
            return {
                "signal_id": signal.signal_id,
                "name": signal.name,
                "tags": signal.tags or [],
                "active_buys": usage.active_buy_count if usage else 0,
                "last_ref": (
                    usage.last_referenced_at.strftime("%Y-%m-%d") if usage and usage.last_referenced_at else None
                ),
            }

        segments: list[dict[str, Any]] = []
        for row in segments_rows:
            mapped_sig = segment_index.get(row.inventory_id)
            segments.append(
                {
                    "id": row.inventory_id,
                    "name": row.name,
                    "type": (row.inventory_metadata or {}).get("type") or "UNKNOWN",
                    "reach": (row.inventory_metadata or {}).get("size"),
                    "mapped": _mapped_payload(mapped_sig) if mapped_sig else None,
                }
            )

        keys: list[dict[str, Any]] = []
        for row in keys_rows:
            key_id = row.inventory_id
            key_name = (row.inventory_metadata or {}).get("display_name") or row.name
            key_type = (row.inventory_metadata or {}).get("type") or "UNKNOWN"
            value_rows = gam_repo.list_values_for_key(key_id) if key_type != "FREEFORM" else []
            values: list[dict[str, Any]] = []
            for v in value_rows:
                mapped_sig = kv_index.get((str(key_id), str(v.inventory_id)))
                values.append(
                    {
                        "id": v.inventory_id,
                        "name": v.name,
                        "display_name": (v.inventory_metadata or {}).get("display_name") or v.name,
                        "key_name": key_name,
                        "mapped": _mapped_payload(mapped_sig) if mapped_sig else None,
                    }
                )
            keys.append(
                {
                    "id": key_id,
                    "name": key_name,
                    "raw_name": row.name,
                    "type": key_type,
                    "values": values,
                    "mapped_count": sum(1 for v in values if v["mapped"]),
                    "total_values": len(values),
                    "is_freeform": key_type == "FREEFORM",
                    "has_cached_values": len(value_rows) > 0,
                }
            )

        composites: list[dict[str, Any]] = []
        for sig in signal_rows:
            cfg = sig.adapter_config or {}
            kind = cfg.get("kind")
            is_composed = cfg.get("type") == "composed"
            is_complex = kind == "gam_targeting_groups"
            if not (is_composed or is_complex):
                continue
            usage = usage_index.get(sig.signal_id)
            composites.append(
                {
                    "signal_id": sig.signal_id,
                    "name": sig.name,
                    "tags": sig.tags or [],
                    "active_buys": usage.active_buy_count if usage else 0,
                    "last_ref": (
                        usage.last_referenced_at.strftime("%Y-%m-%d") if usage and usage.last_referenced_at else None
                    ),
                    "expr": _summarize_composite_expr(cfg),
                }
            )

    tag_set: set[str] = set()
    for s in segments:
        if s["mapped"]:
            tag_set.update(s["mapped"]["tags"])
    for k in keys:
        for v in k["values"]:
            if v["mapped"]:
                tag_set.update(v["mapped"]["tags"])
    for c in composites:
        tag_set.update(c["tags"])
    all_tags = sorted(tag_set)

    seg_mapped = sum(1 for s in segments if s["mapped"])
    seg_unmapped = sum(1 for s in segments if not s["mapped"])
    kv_mapped = sum(1 for k in keys for v in k["values"] if v["mapped"])
    kv_unmapped = sum(1 for k in keys for v in k["values"] if not v["mapped"])
    counts = {
        "mapped": seg_mapped + kv_mapped + len(composites),
        "unmapped": seg_unmapped + kv_unmapped,
        "total": seg_mapped + seg_unmapped + kv_mapped + kv_unmapped + len(composites),
        "segments_mapped": seg_mapped,
        "segments_total": len(segments),
        "kv_mapped": kv_mapped,
        "kv_total": kv_mapped + kv_unmapped,
        "freeform_keys": sum(1 for k in keys if k["is_freeform"]),
    }

    has_inventory = bool(segments or keys or composites)
    adapter_key = tenant.ad_server or "mock"
    adapter_label = _ADAPTER_LABELS.get(adapter_key, adapter_key)
    return render_template(
        "tenant_signals_list.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        segments=segments,
        keys=keys,
        composites=composites,
        all_tags=all_tags,
        counts=counts,
        has_inventory=has_inventory,
        adapter_key=adapter_key,
        adapter_label=adapter_label,
    )


def _summarize_composite_expr(adapter_config: dict) -> str:
    """One-line, mono-friendly expression summary for a composite signal."""
    kind = adapter_config.get("kind")
    if adapter_config.get("type") == "composed":
        criteria = adapter_config.get("criteria") or []
        parts = []
        for c in criteria:
            mode = "NOT " if c.get("mode") == "exclude" else ""
            ckind = c.get("kind", "unknown")
            sid = c.get("segment_id") or c.get("value_id") or ""
            parts.append(f"{mode}{ckind}:{sid}")
        return " AND ".join(parts)
    if kind == "gam_targeting_groups":
        groups = adapter_config.get("groups") or []
        group_strs = []
        for g in groups:
            crits = g.get("criteria") or []
            cstrs = []
            for c in crits:
                op = "NOT IN" if c.get("exclude") else "IN"
                vals = ", ".join(str(v) for v in (c.get("values") or []))
                cstrs.append(f"key{c.get('keyId')} {op} [{vals}]")
            group_strs.append(" AND ".join(cstrs))
        if len(group_strs) > 1:
            return " OR ".join(f"({g})" for g in group_strs)
        return group_strs[0] if group_strs else ""
    return ""


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


# ---------------------------------------------------------------------------
# Composite builder — rare path for multi-key / OR-groups / exclude signals
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/composite", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("create_composite_tenant_signal")
def composite_signal(tenant_id: str):
    """Author a signal whose targeting is too complex for the bulk-mapper.

    Embeds the same ``TargetingWidget`` product authoring uses. The
    widget emits a ``key_value_pairs.groups`` payload that we wrap into
    a ``kind="gam_targeting_groups"`` ``adapter_config`` — the GAM
    materializer (#462) routes that shape directly into
    ``_build_groups_custom_targeting_structure``.

    Composite signals are exclusive at buy time — they can't share an
    ``audience_include`` / ``audience_exclude`` list with other signals
    in the same media buy. That's enforced in the materializer; the form
    surfaces it as a callout so operators understand the bound before
    they author.
    """
    with get_db_session() as session:
        gam_repo = GAMSyncRepository(session, tenant_id)
        segments_rows = gam_repo.list_inventory("audience_segment")
        segments = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "type": (row.inventory_metadata or {}).get("type"),
                "size": (row.inventory_metadata or {}).get("size"),
            }
            for row in segments_rows
        ]

    if request.method == "GET":
        return render_template(
            "tenant_signals_composite.html",
            tenant_id=tenant_id,
            segments=segments,
            form_data=None,
            errors=None,
        )

    form_data, errors, parsed = _validate_composite_form(request.form)
    if not errors:
        with get_db_session() as session:
            if session.get(Tenant, tenant_id) is None:
                flash("Tenant not found.", "error")
                return redirect(url_for("core.index"))
            repo = TenantSignalRepository(session, tenant_id)
            parsed["signal_id"] = unique_signal_id(parsed["name"], exists=lambda sid: repo.get_by_id(sid) is not None)
            signal = TenantSignal(tenant_id=tenant_id, **parsed)
            repo.add(signal)
            session.commit()
        flash(f"Composite signal {parsed['signal_id']!r} created.", "success")
        return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

    return render_template(
        "tenant_signals_composite.html",
        tenant_id=tenant_id,
        segments=segments,
        form_data=form_data,
        errors=errors,
    )


def _validate_composite_form(form) -> tuple[dict, dict, dict]:
    """Two composition modes:

    - ``source="audience"`` — operator ticked N audience segments + chose
      include/exclude per row. Emits an AND of audience_segment criteria
      (existing ``type="composed"`` shape from #439).
    - ``source="custom_keys"`` — TargetingWidget groups payload, wraps
      into ``kind="gam_targeting_groups"`` (the #462 shape).
    """
    source = (form.get("composite_source") or "audience").strip()
    form_data = {
        "composite_source": source,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "audience_picks": form.get("audience_picks") or "",
        "targeting_data": form.get("targeting_data") or "",
    }
    errors: dict[str, str] = {}
    parsed: dict = {}

    if not form_data["name"]:
        errors["name"] = "Name is required (composite signals can't auto-derive a name)."
    else:
        parsed["name"] = form_data["name"]
    parsed["description"] = form_data["description"] or None

    if source == "audience":
        _parse_audience_composition(form_data, errors, parsed)
    elif source == "custom_keys":
        _parse_custom_keys_composition(form_data, errors, parsed)
    else:
        errors["composite_source"] = f"Unknown composite source {source!r}."

    if errors:
        return form_data, errors, parsed

    parsed["value_type"] = "binary"
    parsed["categories"] = []
    parsed["range_min"] = None
    parsed["range_max"] = None
    parsed["data_provider"] = "publisher"
    return form_data, errors, parsed


def _parse_audience_composition(form_data: dict, errors: dict, parsed: dict) -> None:
    """Audience-segment AND builder: list of ``{segment_id, mode}`` picks.
    Two or more picks compose to AND; a single pick is a passthrough."""
    raw = form_data["audience_picks"].strip()
    if not raw:
        errors["audience_picks"] = "Pick at least one audience segment."
        return
    try:
        picks = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        errors["audience_picks"] = f"audience_picks must be JSON: {exc}"
        return
    if not isinstance(picks, list) or not picks:
        errors["audience_picks"] = "Pick at least one audience segment."
        return

    criteria: list[dict] = []
    for pick in picks:
        if not isinstance(pick, dict):
            errors["audience_picks"] = "Each pick must be an object."
            return
        segment_id = str(pick.get("segment_id") or "")
        mode = pick.get("mode", "include")
        if not segment_id:
            errors["audience_picks"] = "Each pick needs a segment_id."
            return
        if mode not in ("include", "exclude"):
            errors["audience_picks"] = f"mode must be include or exclude (got {mode!r})."
            return
        criteria.append({"kind": "audience_segment", "segment_id": segment_id, "mode": mode})

    if len(criteria) == 1:
        parsed["adapter_config"] = {"type": "passthrough", **criteria[0]}
    else:
        parsed["adapter_config"] = {"type": "composed", "criteria": criteria}


def _parse_custom_keys_composition(form_data: dict, errors: dict, parsed: dict) -> None:
    """TargetingWidget groups payload → ``gam_targeting_groups`` adapter
    config. (Existing #462 shape — unchanged.)"""
    raw = form_data["targeting_data"].strip()
    if not raw:
        errors["targeting_data"] = "Build at least one criterion in the targeting builder."
        return

    try:
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        errors["targeting_data"] = f"Targeting builder payload must be JSON: {exc}"
        return

    if not isinstance(payload, dict):
        errors["targeting_data"] = "Targeting builder payload must be a JSON object."
        return

    groups = (payload.get("key_value_pairs") or {}).get("groups") or []
    if not groups:
        errors["targeting_data"] = "Add at least one criterion in the targeting builder."
        return

    for group_idx, group in enumerate(groups):
        criteria = group.get("criteria") or []
        if not criteria:
            errors["targeting_data"] = f"Group {group_idx + 1} has no criteria."
            return
        for crit_idx, criterion in enumerate(criteria):
            if not criterion.get("keyId"):
                errors["targeting_data"] = f"Group {group_idx + 1} criterion {crit_idx + 1} is missing a key."
                return
            if not criterion.get("values"):
                errors["targeting_data"] = f"Group {group_idx + 1} criterion {crit_idx + 1} has no values."
                return

    parsed["adapter_config"] = {
        "type": "passthrough",
        "kind": "gam_targeting_groups",
        "groups": groups,
    }


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
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

        if request.method == "GET":
            mapping_summary = _summarize_adapter_config(
                signal.adapter_config or {}, GAMSyncRepository(session, tenant_id)
            )
            # Deep-link the entity into GAM admin when we know the
            # network. Saves operators from copy-pasting the segment_id
            # into a new tab.
            network_code = getattr(tenant.adapter_config, "gam_network_code", None) if tenant.adapter_config else None
            mapping_summary = _enrich_summary_with_gam_links(mapping_summary, signal.adapter_config or {}, network_code)
            # Project the signal to its buyer-visible ``get_signals`` wire
            # shape — operators want to see what a buyer would discover.
            from src.core.tools.signals import _tenant_signal_to_adcp

            buyer_preview = _tenant_signal_to_adcp(
                signal,
                ad_server=tenant.ad_server,
                agent_url=tenant.public_agent_url,
            ).model_dump(mode="json")
            return render_template(
                "tenant_signals_edit.html",
                tenant_id=tenant_id,
                signal=signal,
                mapping_summary=mapping_summary,
                buyer_preview=buyer_preview,
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


_BULK_OPS = ("add_tag", "remove_tag", "rename_prefix", "rename_suffix")


def _apply_bulk_update(
    repo: TenantSignalRepository, signal_ids: list[str], op_name: str, value: str
) -> tuple[list[str], list[str]]:
    """Apply ``op_name`` to each signal in ``signal_ids``. Pure data-shaping
    over the repository — no session mgmt, no HTTP. Returns ``(updated_ids,
    skipped_ids)``. Caller commits.
    """
    updated: list[str] = []
    skipped: list[str] = []
    for signal in repo.list_by_ids([str(sid) for sid in signal_ids]):
        if op_name == "add_tag":
            tags = list(signal.tags or [])
            if value not in tags:
                signal.tags = sorted(set(tags + [value]))
                updated.append(signal.signal_id)
            else:
                skipped.append(signal.signal_id)
        elif op_name == "remove_tag":
            tags = list(signal.tags or [])
            if value in tags:
                signal.tags = [t for t in tags if t != value]
                updated.append(signal.signal_id)
            else:
                skipped.append(signal.signal_id)
        elif op_name == "rename_prefix":
            if not signal.name.startswith(value):
                signal.name = f"{value}{signal.name}"
                updated.append(signal.signal_id)
            else:
                skipped.append(signal.signal_id)
        elif op_name == "rename_suffix":
            if not signal.name.endswith(value):
                signal.name = f"{signal.name}{value}"
                updated.append(signal.signal_id)
            else:
                skipped.append(signal.signal_id)
    return updated, skipped


@tenant_signals_bp.route("/bulk-update", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("bulk_update_tenant_signals")
def bulk_update(tenant_id: str):
    """Apply an operator-grade bulk operation to N signals at once.

    Request: ``{"signal_ids": [...], "op": "add_tag" | "remove_tag" |
    "rename_prefix" | "rename_suffix", "value": "..."}``.

    Operations:
      - ``add_tag`` / ``remove_tag``: ``value`` is one tag, applied/removed
        idempotently on each signal's ``tags`` list.
      - ``rename_prefix``: ``value`` is prepended to ``name`` if not
        already present. ``signal_id`` is immutable — only the human
        ``name`` changes.
      - ``rename_suffix``: ``value`` is appended to ``name`` if not
        already present.

    Returns ``{"updated": N, "skipped": [signal_id, ...]}``.
    """
    payload = request.get_json(silent=True) or {}
    signal_ids = payload.get("signal_ids") or []
    op_name = payload.get("op")
    value = (payload.get("value") or "").strip()
    if not isinstance(signal_ids, list) or not signal_ids:
        return jsonify({"error": "signal_ids must be a non-empty list"}), 400
    if op_name not in _BULK_OPS:
        return jsonify({"error": f"unsupported op: {op_name!r}"}), 400
    if not value:
        return jsonify({"error": "value is required"}), 400

    if op_name in ("add_tag", "remove_tag"):
        try:
            normalized = _normalize_tags(value)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if len(normalized) != 1:
            return jsonify({"error": "tag operations take exactly one tag"}), 400
        value = normalized[0]

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return jsonify({"error": "tenant not found"}), 404
        repo = TenantSignalRepository(session, tenant_id)
        updated, skipped = _apply_bulk_update(repo, signal_ids, op_name, value)
        session.commit()

    return jsonify({"updated": len(updated), "skipped": skipped, "signal_ids": updated})


def _apply_bulk_delete(
    repo: TenantSignalRepository,
    usage_repo: SignalUsageRepository,
    signal_ids: list[str],
    confirm_typed: str,
) -> tuple[list[str], list[str], list[str]]:
    """Delete each signal in ``signal_ids`` after the reference-safety gate.

    Returns ``(deleted_ids, not_found_ids, blocked_referenced_ids)``. When
    any referenced signal is in the request and ``confirm_typed`` is not
    "DELETE", returns blocked IDs and deletes nothing. Caller commits when
    blocked is empty.
    """
    usage = usage_repo.usage_index()
    referenced = sorted({sid for sid in signal_ids if sid in usage})
    if referenced and confirm_typed != "DELETE":
        return [], [], referenced
    deleted: list[str] = []
    not_found: list[str] = []
    for sid in signal_ids:
        signal = repo.get_by_id(str(sid))
        if signal is None:
            not_found.append(str(sid))
            continue
        repo.delete(signal)
        deleted.append(signal.signal_id)
    return deleted, not_found, []


@tenant_signals_bp.route("/bulk-delete", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("bulk_delete_tenant_signals")
def bulk_delete(tenant_id: str):
    """Delete N signals at once with the same reference-count safety as
    the single delete (#475).

    Request: ``{"signal_ids": [...], "confirm_typed": "DELETE"}``. When
    any of the listed signals are referenced by an active media buy,
    ``confirm_typed`` must equal ``"DELETE"`` — same gate as single
    delete, scaled to the bulk surface.
    """
    payload = request.get_json(silent=True) or {}
    signal_ids = payload.get("signal_ids") or []
    confirm_typed = payload.get("confirm_typed") or ""
    if not isinstance(signal_ids, list) or not signal_ids:
        return jsonify({"error": "signal_ids must be a non-empty list"}), 400

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return jsonify({"error": "tenant not found"}), 404
        deleted, not_found, blocked = _apply_bulk_delete(
            TenantSignalRepository(session, tenant_id),
            SignalUsageRepository(session, tenant_id),
            signal_ids,
            confirm_typed,
        )
        if blocked:
            return (
                jsonify(
                    {
                        "error": "active buys reference one or more signals",
                        "referenced": blocked,
                        "confirm_required": True,
                    }
                ),
                409,
            )
        session.commit()

    return jsonify({"deleted": len(deleted), "not_found": not_found, "signal_ids": deleted})


@tenant_signals_bp.route("/<signal_id>/rename", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("rename_tenant_signal")
def rename_signal(tenant_id: str, signal_id: str):
    """Rename a signal in place — backs the inline-edit affordance on the
    Signals page (PR 4 redesign). Body: ``{"name": "..."}``.
    """
    payload = request.get_json(silent=True) or {}
    new_name = (payload.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "name is required"}), 400
    with get_db_session() as session:
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            return jsonify({"error": "signal not found"}), 404
        signal.name = new_name
        session.commit()
    return jsonify({"ok": True, "signal_id": signal_id, "name": new_name})


@tenant_signals_bp.route("/<signal_id>/delete", methods=["POST", "DELETE"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("delete_tenant_signal")
def delete_signal(tenant_id: str, signal_id: str):
    """Delete a signal with reference-count safety.

    When active media buys reference the signal_id, require the operator
    to type DELETE (sent as form field ``confirm_typed=DELETE``). The JS
    modal prompts for this; the server enforces it so curl users can't
    skip the check. Zero references → no confirm needed.
    """
    with get_db_session() as session:
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

        active_buys = SignalUsageRepository(session, tenant_id).count_references(signal_id)
        if active_buys > 0 and request.form.get("confirm_typed") != "DELETE":
            flash(
                f"Signal {signal_id!r} is referenced by {active_buys} active media buy(s). Type DELETE to confirm.",
                "error",
            )
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

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
        "tags": (form.get("tags") or "").strip(),
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

    try:
        parsed["tags"] = _normalize_tags(form_data["tags"])
    except ValueError as exc:
        errors["tags"] = str(exc)

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


def _gam_admin_url(network_code: str | None, kind: str, entity_id: str) -> str | None:
    """Build a deep link into the GAM admin UI for the given entity.

    Returns ``None`` when the network isn't known (deep link impossible)
    or the entity kind isn't a GAM primitive we know how to address.
    Patterns derived from the GAM admin's hash-based router.
    """
    if not network_code or not entity_id:
        return None
    base = f"https://admanager.google.com/{network_code}"
    if kind == "audience_segment":
        return f"{base}#delivery/audience-segments/detail/audience_segment_id={entity_id}"
    if kind == "custom_targeting_key":
        return f"{base}#inventory/custom-targeting/detail/key_id={entity_id}"
    return None


def _enrich_summary_with_gam_links(summary: dict, adapter_config: dict, network_code: str | None) -> dict:
    """Add a ``gam_url`` field to summary rows for entities we can deep-link.

    Mutates and returns ``summary``. Skips composed / unknown shapes —
    only pass-through audience_segment + custom_key_value carry a single
    entity that can sensibly be linked.
    """
    if not network_code:
        return summary
    kind = adapter_config.get("kind")
    if kind == "audience_segment":
        url = _gam_admin_url(network_code, "audience_segment", str(adapter_config.get("segment_id") or ""))
        if url:
            summary["gam_url"] = url
            summary["gam_label"] = "Open in GAM Admin"
    elif kind == "custom_key_value":
        url = _gam_admin_url(network_code, "custom_targeting_key", str(adapter_config.get("key_id") or ""))
        if url:
            summary["gam_url"] = url
            summary["gam_label"] = "Open key in GAM Admin"
    return summary


# ---------------------------------------------------------------------------
# Mapping summary — humanize adapter_config for the edit page
# ---------------------------------------------------------------------------


def _summarize_adapter_config(adapter_config: dict, gam_repo: GAMSyncRepository) -> dict:
    """Render an adapter_config as a human-readable summary.

    Operators editing a signal need to see what it actually maps to in
    GAM without cracking open the JSON. Returns a dict with:

    - ``label``: short summary string (e.g. "GAM audience segment")
    - ``rows``: list of ``{"label": "...", "value": "..."}`` for the
      template to render as a key-value list
    - ``raw_kind``: the underlying ``kind`` (or ``"composed"``) for
      operator-facing badging

    Falls back to ``{"label": "Unknown shape", "rows": []}`` on adapter
    configs we don't recognize — the Advanced JSON section is still
    available as the source of truth.
    """
    config_type = adapter_config.get("type")
    if config_type == "composed":
        criteria = adapter_config.get("criteria") or []
        rows = [
            {"label": f"Criterion {i + 1}", "value": _summarize_criterion(c, gam_repo)} for i, c in enumerate(criteria)
        ]
        return {
            "label": f"Composed AND of {len(criteria)} criteria",
            "rows": rows,
            "raw_kind": "composed",
        }

    kind = adapter_config.get("kind")
    if kind == "gam_targeting_groups":
        groups = adapter_config.get("groups") or []
        crit_count = sum(len(g.get("criteria") or []) for g in groups)
        return {
            "label": f"Composite GAM targeting — {len(groups)} group(s), {crit_count} criterion(a)",
            "rows": _summarize_groups(groups, gam_repo),
            "raw_kind": "gam_targeting_groups",
        }

    # Pass-through (legacy + explicit). The header already names the kind,
    # so rows just carry the human-readable value with no redundant ``dt``.
    if kind == "audience_segment":
        segment_id = str(adapter_config.get("segment_id") or "")
        seg_name = _lookup_inventory_name(gam_repo, "audience_segment", segment_id)
        return {
            "label": "GAM audience segment",
            "rows": [{"label": "", "value": f"{seg_name or '(unsynced)'} — id {segment_id}"}],
            "raw_kind": kind,
        }
    if kind == "custom_key_value":
        key_id = str(adapter_config.get("key_id") or "")
        value_id = str(adapter_config.get("value_id") or "")
        key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id) or key_id
        return {
            "label": "GAM custom key + value",
            "rows": [{"label": "", "value": f"{key_name} = {value_id}"}],
            "raw_kind": kind,
        }
    if kind in ("freewheel_viewership_profile", "freewheel_audience_item", "freewheel_custom_kv"):
        cfg_str = ", ".join(f"{k}={v}" for k, v in adapter_config.items() if k not in ("type", "kind", "mode"))
        return {
            "label": f"Freewheel {kind.replace('freewheel_', '').replace('_', ' ')}",
            "rows": [{"label": "", "value": cfg_str}],
            "raw_kind": kind,
        }

    return {"label": "Unknown adapter shape — edit JSON directly to update", "rows": [], "raw_kind": kind or "unknown"}


def _summarize_criterion(criterion: dict, gam_repo: GAMSyncRepository) -> str:
    """One-line summary of one composed-criterion dict."""
    kind = criterion.get("kind")
    mode = criterion.get("mode", "include")
    mode_prefix = "EXCLUDE " if mode == "exclude" else ""
    if kind == "audience_segment":
        segment_id = str(criterion.get("segment_id") or "")
        seg_name = _lookup_inventory_name(gam_repo, "audience_segment", segment_id)
        return f"{mode_prefix}audience segment {seg_name or segment_id}"
    if kind == "custom_key_value":
        key_id = str(criterion.get("key_id") or "")
        value_id = str(criterion.get("value_id") or "")
        key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id) or key_id
        return f"{mode_prefix}{key_name}={value_id}"
    return f"{mode_prefix}{kind}"


def _summarize_groups(groups: list[dict], gam_repo: GAMSyncRepository) -> list[dict]:
    """Render the TargetingWidget groups payload as a list of rows."""
    rows: list[dict] = []
    for g_idx, group in enumerate(groups):
        criteria = group.get("criteria") or []
        parts = []
        for crit in criteria:
            key_id = str(crit.get("keyId") or "")
            values = crit.get("values") or []
            exclude = crit.get("exclude")
            key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id) or key_id
            op = "NOT IN" if exclude else "IN"
            parts.append(f"{key_name} {op} [{', '.join(str(v) for v in values)}]")
        rows.append({"label": f"Group {g_idx + 1}", "value": " AND ".join(parts) or "(empty)"})
    return rows


def _lookup_inventory_name(gam_repo: GAMSyncRepository, inventory_type: str, inventory_id: str) -> str | None:
    """Look up the human display name for a GAM inventory row.
    Returns ``None`` when the row isn't in synced inventory (operator hasn't
    synced yet, or the row has been removed in GAM).
    """
    if not inventory_id:
        return None
    for row in gam_repo.list_inventory(inventory_type):
        if row.inventory_id == inventory_id:
            return row.name
    return None
