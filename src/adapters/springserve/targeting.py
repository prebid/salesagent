"""Translate AdCP targeting into SpringServe demand-tag fields.

SpringServe demand tags don't wrap targeting in a nested ``targeting``
object -- the targeting fields (``country_codes``, ``state_codes``,
``metro_area_codes``, ``player_sizes``, ``user_agent_devices``, and the
supply-side ``demand_tag_priorities``) live directly on the tag, each
paired with a ``<dimension>_targeting`` discriminator (``"All"`` vs
``"White List"``).

Signal resolution: buyer-supplied ``audience_include`` / ``audience_exclude``
references resolve through operator-declared ``TenantSignal`` rows whose
``adapter_config`` carries SpringServe-specific kinds. Today the only
shipping kind is ``springserve_value_list`` (publisher-curated audience
lists from the KV catalog); each signal produces one ``demand_tag_keys``
entry with ``list_type='white_list'`` or ``'black_list'`` per the
include/exclude mode.

Wire format for KV targeting — the **correct write path** per SpringServe
docs (https://springserve.atlassian.net/wiki/spaces/SSD/pages/1628471383/
Demand+Key-Value+Targeting+API) is a **two-step sub-resource POST**, not
a body attribute on ``/demand_tags``::

    POST /api/v0/demand_tags/<demand_tag_id>/demand_tag_keys
    {
      "key_id": "3997",
      "list_type": "white_list" | "black_list",
      "group": "1",
      "free_values": ["1345713", "1334483", ...],   # for definition_type="free" keys
      "value_ids": [...]                             # for definition_type="list" keys
    }

Grouping semantics from the doc: **same group = AND**, **different
groups = OR** (across entries). Within one entry's value array,
matching is OR. Talpa's keys are all ``definition_type="free"``, so the
materializer expands the publisher's value_list (e.g. "Podcast MV35-54")
into ``free_values`` at write time using the cached ``free_values`` we
captured during inventory sync.

This module exports two helpers:

- :func:`build_demand_tag_targeting` returns the flat kwargs passed to
  :class:`SpringServeDemandTagsClient.create` (geo, device, supply
  priorities — everything that lives directly on the demand_tag).
- :func:`build_demand_tag_kv_entries` returns the list of sub-resource
  payloads the adapter POSTs separately after creating the demand tag.

**Tenant-level opt-in.** KV-targeting writes are gated by the
``enable_key_value_targeting`` flag on the SpringServe connection config;
off by default because most publishers express audience / content / device
through supply-tag selection and demand-tag priorities rather than
demand_tag_keys. Turn it on only when the publisher has free-form KV
keys not reflected in their supply taxonomy.

If a tenant opts in, the parent demand_tag's ``key_value_targeting``
flag must be ``true`` for the sub-resource POST to be accepted -- the
API rejects with ``HTTP 422 "Targeter must have key_value_targeting
set to true"`` otherwise. Whether the v0 API exposes that flag for
write depends on the SpringServe account configuration on the
publisher side. The adapter catches that specific 422 and emits a
warning so the buy succeeds without targeting; any other 422 (bad
key_id, malformed values, etc) propagates to the buyer as an
``upstream_error``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_demand_tag_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build the targeting kwargs for ``SpringServeDemandTagsClient.create``.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device,
            ``audience_include``, ``audience_exclude``).
        product_config: ``SpringServeProductConfig`` as a dict -- supplies
            product-default supply tag inclusion, player sizes, and
            environments.
        tenant_id: When provided, ``audience_include``/``audience_exclude``
            references resolve through the tenant's ``tenant_signals``.
            Required for signal resolution; if omitted, signals are
            ignored (preserves the existing rejection in ``validate_targeting``).

    Output keys correspond 1:1 to Demand Tag fields. Empty values are
    omitted so the client builder doesn't override SpringServe defaults.
    """
    product_config = product_config or {}
    kwargs: dict[str, Any] = {}

    # Supply targeting -- product config carries supply_tag_ids, we turn them
    # into demand_tag_priorities entries (priority + tier default to 1).
    supply_tag_ids = product_config.get("supply_tag_ids") or []
    if supply_tag_ids:
        kwargs["demand_tag_priorities"] = [
            {"supply_tag_id": int(stid), "priority": 1, "tier": 1} for stid in supply_tag_ids
        ]

    # Player + device defaults from product config.
    if product_config.get("player_sizes"):
        kwargs["player_sizes"] = list(product_config["player_sizes"])
    if product_config.get("device_types"):
        kwargs["user_agent_devices"] = list(product_config["device_types"])

    # AdCP-overlay-driven geo. Empty lists in the overlay are no-ops; we
    # only set targeting when there's actually a list.
    if targeting_overlay is not None:
        if getattr(targeting_overlay, "geo_countries", None):
            kwargs["country_codes"] = [c.root for c in targeting_overlay.geo_countries]
        if getattr(targeting_overlay, "geo_regions", None):
            kwargs["state_codes"] = [r.root for r in targeting_overlay.geo_regions]
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                kwargs["metro_area_codes"] = metro_values
        if getattr(targeting_overlay, "device_type_any_of", None):
            # AdCP device-type overlay wins over product defaults when both
            # are set -- buyer intent is more specific than product defaults.
            kwargs["user_agent_devices"] = list(targeting_overlay.device_type_any_of)

    # KV targeting is NOT a body field on /demand_tags. It goes through the
    # sub-resource path via build_demand_tag_kv_entries() -- the adapter
    # POSTs each entry to /demand_tags/<id>/demand_tag_keys after the parent
    # tag is created. See module docstring for the wire-format contract.

    # Escape hatch -- raw demand-tag field overrides (extras win).
    extras = product_config.get("extra_demand_tag_fields") or {}
    if isinstance(extras, dict):
        for key, value in extras.items():
            kwargs[key] = value

    return kwargs


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for SpringServe.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. The Stage-2 cut rejects
    overlays whose wire format isn't verified against the live API yet --
    subsequent stages narrow this list as fields move from "unverified" to
    "verified" against the live account.
    """
    unsupported: list[str] = []
    if targeting_overlay is None:
        return unsupported

    if getattr(targeting_overlay, "geo_postal_areas", None) or getattr(
        targeting_overlay, "geo_postal_areas_exclude", None
    ):
        unsupported.append("Postal-area targeting not supported -- use geo_metros (DMA) or geo_regions instead")

    if getattr(targeting_overlay, "frequency_cap", None):
        unsupported.append(
            "Frequency cap targeting pending SpringServe sandbox validation -- "
            "set frequency caps via SpringServeProductConfig escape hatch for now"
        )

    if getattr(targeting_overlay, "audiences_any_of", None):
        unsupported.append(
            "audiences_any_of is the legacy free-form audience field; use audience_include "
            "with operator-declared SpringServe signal_ids instead."
        )

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append("Free-form dayparting pending SpringServe sandbox validation")

    return unsupported


# ---------------------------------------------------------------------------
# Signal resolution (SpringServe)
# ---------------------------------------------------------------------------


def build_demand_tag_kv_entries(
    targeting_overlay: Any,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Build the sub-resource POST payloads for KV targeting.

    The adapter calls :class:`SpringServeDemandTagsClient.create` first,
    then POSTs each of these payloads to
    ``/demand_tags/<demand_tag_id>/demand_tag_keys``.

    Wire format per SpringServe docs (1628471383)::

        {"key_id": "3997", "list_type": "white_list", "group": "1",
         "free_values": ["1345713", "1334483", ...]}

    Semantics:

    - ``include`` signal -> ``list_type='white_list'``
    - ``exclude`` signal -> ``list_type='black_list'``
    - All entries default to ``group='1'`` (same-group = AND per the
      doc), so multiple signals on different keys AND together which
      matches the intuitive default ("Sports AND Netherlands AND CTV").
      Operators authoring OR-across-keys semantics can use a composed
      signal once we support them.
    - Within an entry, ``free_values`` carries the expanded contents of
      the publisher-curated value_list (for ``definition_type='free'``
      keys -- Talpa's setup). Multiple value_lists on the same key get
      merged into one entry's ``free_values`` (OR within entry).
    """
    if targeting_overlay is None or not tenant_id:
        return []
    include_ids = list(getattr(targeting_overlay, "audience_include", None) or [])
    exclude_ids = list(getattr(targeting_overlay, "audience_exclude", None) or [])
    if not include_ids and not exclude_ids:
        return []
    return _resolve_audience_signals(
        tenant_id=tenant_id,
        include_signal_ids=include_ids,
        exclude_signal_ids=exclude_ids,
    )


def _resolve_audience_signals(
    *,
    tenant_id: str,
    include_signal_ids: list[str],
    exclude_signal_ids: list[str],
) -> list[dict[str, Any]]:
    """Look up each signal_id in ``tenant_signals``, expand the referenced
    value_lists from the inventory cache, and emit the per-key sub-resource
    POST bodies SpringServe accepts.

    Each entry carries ``free_values`` (the expanded list of station IDs,
    etc.) rather than ``value_list_ids`` because Talpa's keys are
    ``definition_type='free'`` -- the API rejects ``value_list_ids`` for
    free-form keys with "Free values can't be blank". Predefined-value
    keys (``definition_type='list'``) are a separate code path for the
    day a publisher exposes one.

    Composed signals are not yet supported -- only passthrough signals
    with ``kind='springserve_value_list'`` resolve.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.springserve_inventory import (
        SpringServeInventoryRepository,
    )
    from src.core.database.repositories.uow import TenantSignalUoW

    all_ids = list(include_signal_ids) + list(exclude_signal_ids)
    if not all_ids:
        return []

    with TenantSignalUoW(tenant_id) as uow:
        assert uow.tenant_signals is not None
        signals_by_id = {s.signal_id: s for s in uow.tenant_signals.list_by_ids(all_ids)}
        missing = [sid for sid in all_ids if sid not in signals_by_id]
        if missing:
            raise ValueError(
                f"SpringServe audience targeting references signal(s) not declared on tenant "
                f"{tenant_id!r}: {', '.join(sorted(missing))}. "
                f"Map each signal from the Signals page first."
            )

        atoms: list[tuple[int, int, str]] = []  # (key_id, value_list_id, list_type)
        for sid in include_signal_ids:
            for key_id, value_list_id in _signal_atoms(signals_by_id[sid]):
                atoms.append((key_id, value_list_id, "white_list"))
        for sid in exclude_signal_ids:
            for key_id, value_list_id in _signal_atoms(signals_by_id[sid]):
                atoms.append((key_id, value_list_id, "black_list"))

    # Pull value_list contents from the inventory cache. One DB round-trip
    # per call; the cache holds raw_json.free_values from the sync.
    needed_list_ids = sorted({vid for _, vid, _ in atoms})
    free_values_by_list_id: dict[int, list[str]] = {}
    with get_db_session() as session:
        repo = SpringServeInventoryRepository(session, tenant_id)
        for row in repo.list_by_type("value_list"):
            try:
                vid = int(row.entity_id)
            except (TypeError, ValueError):
                continue
            if vid in needed_list_ids:
                raw = row.raw_json or {}
                # SpringServe value_lists keep their values in ``free_values``
                # (strings). Cast everything to str so the wire body is
                # JSON-clean regardless of how the publisher entered them.
                free_values_by_list_id[vid] = [str(v) for v in (raw.get("free_values") or [])]

    # Bucket by (key_id, list_type) so multiple value_lists on the same
    # key merge into one entry's ``free_values`` (OR within entry).
    buckets: dict[tuple[int, str], dict[str, Any]] = {}
    for key_id, value_list_id, list_type in atoms:
        bucket_key = (key_id, list_type)
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "key_id": str(key_id),
                "list_type": list_type,
                # Default all entries to group "1" -> AND across distinct
                # keys per the doc. Operators wanting OR semantics author
                # a composed signal (future) or use the SpringServe UI.
                "group": "1",
                "free_values": [],
            }
        for v in free_values_by_list_id.get(value_list_id, []):
            if v not in buckets[bucket_key]["free_values"]:
                buckets[bucket_key]["free_values"].append(v)

    # Stable order so the wire output is reproducible (tests + diffs).
    return sorted(buckets.values(), key=lambda b: (int(b["key_id"]), b["list_type"]))


def _signal_atoms(signal) -> list[tuple[int, int]]:
    """Return ``[(key_id, value_list_id), ...]`` for one signal.

    Today only ``kind='springserve_value_list'`` resolves. Composed
    signals aren't yet supported.
    """
    cfg = signal.adapter_config or {}
    config_type = cfg.get("type")
    kind = cfg.get("kind")

    if config_type == "composed":
        raise ValueError(
            f"Signal {signal.signal_id!r} type='composed' is not yet supported by SpringServe -- "
            f"author each value_list as its own passthrough signal and reference them all in "
            f"audience_include instead."
        )
    if kind != "springserve_value_list":
        raise ValueError(
            f"Signal {signal.signal_id!r} adapter_config.kind={kind!r} is not supported by "
            f"SpringServe. Expected kind='springserve_value_list' (a publisher-curated value_list "
            f"from the KV catalog)."
        )

    key_id = cfg.get("key_id")
    value_list_id = cfg.get("value_list_id")
    if not key_id or not value_list_id:
        raise ValueError(
            f"Signal {signal.signal_id!r} kind='springserve_value_list' missing key_id or "
            f"value_list_id in adapter_config."
        )
    return [(int(key_id), int(value_list_id))]


# Backwards-compatible alias for callers still importing the old name.
build_targeting = build_demand_tag_targeting
