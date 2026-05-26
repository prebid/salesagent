"""Admin blueprint for managing inventory profiles.

Inventory profiles are reusable configurations of:
- Ad units and placements (inventory)
- Creative formats
- Publisher properties
- Optional targeting defaults

Products can reference inventory profiles to avoid duplicating configuration.
"""

import json
import logging
import re
from collections.abc import Sequence

from flask import Blueprint, Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from src.adapters.gam.formats import canonical_format_dict
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.canonical_formats import (
    CANONICAL_DISPLAY_FORMAT_IDS,
    DEFAULT_CREATIVE_AGENT_URL,
    DISPLAY_FORMAT_LABELS,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty,
    InventoryProfile,
    Product,
    PropertyTag,
    Tenant,
)
from src.services.bundle_adapter import adapter_for_tenant
from src.services.inventory_bundle_reference_sync import recompute_bundle_references

logger = logging.getLogger(__name__)

inventory_profiles_bp = Blueprint("inventory_profiles", __name__)
TAG_PATTERN = re.compile(r"^[a-z0-9_]+$")
INVENTORY_PICKER_LIMIT = 200
GAM_CANONICAL_DISPLAY_FORMAT_IDS = CANONICAL_DISPLAY_FORMAT_IDS
GAM_CANONICAL_DISPLAY_FORMAT_LABELS = DISPLAY_FORMAT_LABELS
GAM_SPECIAL_SIZE = (1, 1)


def _generate_profile_id(name: str) -> str:
    """Generate a profile_id from name.

    Args:
        name: Human-readable profile name

    Returns:
        URL-safe profile ID (lowercase with underscores)
    """
    # Convert to lowercase, replace spaces/special chars with underscores
    profile_id = re.sub(r"[^a-z0-9_]+", "_", name.lower())
    # Remove leading/trailing underscores
    profile_id = profile_id.strip("_")
    # Collapse multiple underscores
    profile_id = re.sub(r"_+", "_", profile_id)
    return profile_id


def _unique_profile_id(session, tenant_id: str, base: str) -> str:
    """Return a tenant-unique profile_id, suffixing _2, _3, ... if needed."""
    existing = set(
        session.scalars(select(InventoryProfile.profile_id).where(InventoryProfile.tenant_id == tenant_id)).all()
    )
    if base not in existing:
        return base
    for i in range(2, 1000):
        candidate = f"{base}_{i}"
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"Could not find unique profile_id for {base}")


def _size_to_tuple(size) -> tuple[int, int] | None:
    """Normalize synced GAM size metadata to ``(width, height)``."""
    if isinstance(size, dict) and size.get("width") and size.get("height"):
        try:
            return int(size["width"]), int(size["height"])
        except (TypeError, ValueError):
            return None
    if isinstance(size, dict):
        format_id = str(size.get("id") or size.get("format_id") or "")
        match = re.search(r"(\d+)x(\d+)", format_id)
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except (TypeError, ValueError):
                return None
    if isinstance(size, str) and "x" in size:
        try:
            width, height = size.lower().split("x", 1)
            return int(width), int(height)
        except (TypeError, ValueError):
            return None
    return None


def _is_gam_special_size(size: tuple[int, int] | None) -> bool:
    return size == GAM_SPECIAL_SIZE


def _adcp_inventory_capabilities(metadata: dict) -> dict:
    capabilities = metadata.get("adcp_capabilities") if isinstance(metadata, dict) else None
    return capabilities if isinstance(capabilities, dict) else {}


def _capability_slot_kind(metadata: dict) -> str:
    capabilities = _adcp_inventory_capabilities(metadata)
    slot_kind = capabilities.get("slot_kind")
    if isinstance(slot_kind, str) and slot_kind:
        return slot_kind
    special = capabilities.get("special_size")
    if isinstance(special, dict) and special.get("kind"):
        return str(special["kind"])
    return ""


def _special_size_is_classified(metadata: dict) -> bool:
    return bool(_capability_slot_kind(metadata))


def _display_format_ids_for_capabilities(metadata: dict) -> list[str]:
    capabilities = _adcp_inventory_capabilities(metadata)
    render_modes = capabilities.get("render_modes")
    if not isinstance(render_modes, dict):
        return list(GAM_CANONICAL_DISPLAY_FORMAT_IDS)

    format_ids = []
    if render_modes.get("image"):
        format_ids.append("display_image")
    if render_modes.get("html"):
        format_ids.append("display_html")
    if render_modes.get("js"):
        format_ids.append("display_js")
    return format_ids or list(GAM_CANONICAL_DISPLAY_FORMAT_IDS)


def _fixed_display_format_ids_for_capabilities(metadata: dict) -> list[str]:
    slot_kind = _capability_slot_kind(metadata)
    if slot_kind and slot_kind not in {"fixed_display", "responsive_display", "rich_media"}:
        return []
    return _display_format_ids_for_capabilities(metadata)


def _native_formats_for_capabilities(metadata: dict) -> list[dict]:
    if _capability_slot_kind(metadata) != "native":
        return []
    return [canonical_format_dict("native_standard")]


def _video_formats_for_capabilities(metadata: dict, size: tuple[int, int] | None = None) -> list[dict]:
    if _capability_slot_kind(metadata) != "olv":
        return []
    params = {"width": size[0], "height": size[1]} if size else {}
    return [
        canonical_format_dict("video_standard", **params),
        canonical_format_dict("video_vast", **params),
    ]


def _responsive_display_formats(metadata: dict) -> list[dict]:
    capabilities = _adcp_inventory_capabilities(metadata)
    slot_kind = _capability_slot_kind(metadata)
    if slot_kind not in {"responsive_display", "rich_media", "fixed_display"}:
        return []

    dimensions = capabilities.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}

    format_params = {
        key: int(dimensions[key])
        for key in ("min_width", "max_width", "min_height", "max_height")
        if dimensions.get(key) is not None
    }
    if not format_params:
        return []

    return [
        {
            "agent_url": DEFAULT_CREATIVE_AGENT_URL,
            "id": format_id,
            **format_params,
        }
        for format_id in _display_format_ids_for_capabilities(metadata)
    ]


def _selected_gam_ad_units(session, tenant_id: str, inventory_config: dict) -> list:
    """Resolve selected ad units plus placement children from synced GAM inventory."""
    return [
        row
        for row, _capability_metadata in _selected_gam_ad_units_with_capability_metadata(
            session, tenant_id, inventory_config
        )
    ]


def _selected_gam_ad_units_with_capability_metadata(session, tenant_id: str, inventory_config: dict) -> list[tuple]:
    """Resolve selected GAM ad units plus placement-level capability fallbacks."""
    from src.core.database.repositories.gam_sync import GAMSyncRepository

    repo = GAMSyncRepository(session, tenant_id)
    ad_unit_ids = {str(i) for i in (inventory_config.get("ad_units") or []) if i}
    capability_metadata_by_ad_unit: dict[str, dict] = {}

    placement_ids = [str(i) for i in (inventory_config.get("placements") or []) if i]
    placements = repo.list_inventory_by_ids("placement", placement_ids)
    for placement in placements:
        metadata = placement.inventory_metadata if isinstance(placement.inventory_metadata, dict) else {}
        placement_ad_unit_ids = [str(i) for i in metadata.get("ad_unit_ids", []) if i]
        ad_unit_ids.update(placement_ad_unit_ids)
        if _adcp_inventory_capabilities(metadata):
            for ad_unit_id in placement_ad_unit_ids:
                capability_metadata_by_ad_unit.setdefault(ad_unit_id, metadata)

    if not ad_unit_ids:
        return []
    rows = repo.list_inventory_by_ids("ad_unit", sorted(ad_unit_ids))
    resolved = []
    for row in rows:
        metadata = row.inventory_metadata if isinstance(row.inventory_metadata, dict) else {}
        if not _adcp_inventory_capabilities(metadata):
            metadata = capability_metadata_by_ad_unit.get(str(row.inventory_id), metadata)
        resolved.append((row, metadata))
    return resolved


def _metadata_has_special_size(metadata: dict) -> bool:
    return any(_is_gam_special_size(_size_to_tuple(raw_size)) for raw_size in metadata.get("sizes") or [])


def _responsive_format_key(fmt: dict) -> tuple:
    return (
        fmt.get("id"),
        fmt.get("min_width"),
        fmt.get("max_width"),
        fmt.get("min_height"),
        fmt.get("max_height"),
    )


def _format_key(fmt: dict) -> tuple:
    return (
        fmt.get("id"),
        fmt.get("width"),
        fmt.get("height"),
        fmt.get("duration_ms"),
        fmt.get("min_width"),
        fmt.get("max_width"),
        fmt.get("min_height"),
        fmt.get("max_height"),
    )


def _unclassified_gam_special_size_units(session, tenant_id: str, inventory_config: dict) -> list[dict]:
    """Selected synced ad units with GAM ``1x1`` special sizes needing setup."""
    unclassified = []
    for ad_unit, capability_metadata in _selected_gam_ad_units_with_capability_metadata(
        session, tenant_id, inventory_config
    ):
        metadata = ad_unit.inventory_metadata if isinstance(ad_unit.inventory_metadata, dict) else {}
        if _metadata_has_special_size(metadata) and not _special_size_is_classified(capability_metadata):
            unclassified.append(
                {
                    "id": ad_unit.inventory_id,
                    "name": ad_unit.name,
                }
            )
    return unclassified


def _derive_canonical_format_ids_from_inventory(session, tenant_id: str, inventory_config: dict) -> list[dict]:
    """Derive AdCP v2 canonical display formats from selected synced GAM inventory.

    GAM inventory sync stores concrete creative sizes on ad-unit metadata.
    Products should expose fixed sizes through canonical parameterized format
    IDs (``display_image``, ``display_html``, ``display_js``) instead of the
    legacy fixed-size IDs (``display_300x250_image`` etc.).

    GAM ``1x1`` is a special placeholder for fluid/native/interstitial/etc.,
    not a literal display slot. It is intentionally skipped until the
    placement/ad-unit capability is classified.
    """

    selected_ad_units = _selected_gam_ad_units_with_capability_metadata(session, tenant_id, inventory_config)
    fixed_formats: set[tuple[str, int, int]] = set()
    native_formats_by_id: dict[str, dict] = {}
    video_formats_by_key: dict[tuple, dict] = {}
    responsive_formats_by_key: dict[tuple, dict] = {}

    for ad_unit, capability_metadata in selected_ad_units:
        metadata = ad_unit.inventory_metadata if isinstance(ad_unit.inventory_metadata, dict) else {}
        for raw_size in metadata.get("sizes") or []:
            parsed = _size_to_tuple(raw_size)
            if not parsed:
                continue
            if _is_gam_special_size(parsed):
                for fmt in _native_formats_for_capabilities(capability_metadata):
                    native_formats_by_id.setdefault(fmt["id"], fmt)
                for fmt in _video_formats_for_capabilities(capability_metadata):
                    video_formats_by_key.setdefault(_format_key(fmt), fmt)
                for fmt in _responsive_display_formats(capability_metadata):
                    responsive_formats_by_key.setdefault(_responsive_format_key(fmt), fmt)
                continue
            for fmt in _native_formats_for_capabilities(capability_metadata):
                native_formats_by_id.setdefault(fmt["id"], fmt)
            for fmt in _video_formats_for_capabilities(capability_metadata, parsed):
                video_formats_by_key.setdefault(_format_key(fmt), fmt)
            for format_id in _fixed_display_format_ids_for_capabilities(capability_metadata):
                fixed_formats.add((format_id, parsed[0], parsed[1]))

    formats = [
        {
            "agent_url": DEFAULT_CREATIVE_AGENT_URL,
            "id": format_id,
            "width": width,
            "height": height,
        }
        for format_id, width, height in sorted(fixed_formats, key=lambda item: (item[1], item[2], item[0]))
    ]
    formats.extend(native_formats_by_id[key] for key in sorted(native_formats_by_id))
    formats.extend(video_formats_by_key[key] for key in sorted(video_formats_by_key))
    formats.extend(responsive_formats_by_key[key] for key in sorted(responsive_formats_by_key))
    return formats


def _format_type_label(format_id: str) -> str:
    if format_id in GAM_CANONICAL_DISPLAY_FORMAT_LABELS:
        return GAM_CANONICAL_DISPLAY_FORMAT_LABELS[format_id]
    legacy_display = re.match(r"display_\d+x\d+_(image|html|js)$", format_id)
    if legacy_display:
        return GAM_CANONICAL_DISPLAY_FORMAT_LABELS.get(f"display_{legacy_display.group(1)}", legacy_display.group(1))
    return format_id.replace("_", " ")


def _format_display_groups(formats: list[dict]) -> list[dict]:
    """Group parameterized creative formats into human-readable size rows."""
    grouped: dict[tuple[int, int], set[str]] = {}
    standalone: list[dict] = []

    for fmt in formats or []:
        format_id = fmt.get("id") or fmt.get("format_id") or ""
        label = _format_type_label(format_id)
        size = _size_to_tuple(fmt)
        if size:
            grouped.setdefault(size, set()).add(label)
        elif label:
            standalone.append({"label": label})

    ordered = []
    label_order = {label: i for i, label in enumerate(GAM_CANONICAL_DISPLAY_FORMAT_LABELS.values())}
    for width, height in sorted(grouped):
        labels = sorted(grouped[(width, height)], key=lambda item: label_order.get(item, len(label_order)))
        ordered.append({"size": f"{width}x{height}", "types": labels})

    ordered.extend(standalone)
    return ordered


def _format_display_summary_for_groups(groups: list[dict], limit: int = 2) -> list[str]:
    summary = []
    for group in groups:
        if group.get("size"):
            summary.append(f"{group['size']} {'/'.join(group['types'])}")
        elif group.get("label"):
            summary.append(group["label"])
    return summary[:limit]


def _format_display_summary(formats: list[dict], limit: int = 2) -> list[str]:
    return _format_display_summary_for_groups(_format_display_groups(formats), limit)


def _authorized_publisher_domains(session, tenant_id: str, tenant: Tenant) -> set[str]:
    """Publisher domains this tenant can author into buyer-visible bundles."""
    return set(_bundle_authorable_domains(session, tenant_id, tenant))


def _bundle_authorable_domains(session, tenant_id: str, tenant: Tenant) -> list[str]:
    """Publisher domains available to the wholesale-product inventory layer."""
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    repo = TenantConfigRepository(session, tenant_id)
    discovered_domains = [prop.publisher_domain for prop in repo.list_authorized_properties() if prop.publisher_domain]
    discovered_domains.extend(
        partner.publisher_domain
        for partner in repo.list_publisher_partners()
        if partner.publisher_domain and partner.is_verified
    )
    return _bundle_property_domains(tenant, discovered_domains)


def _bundle_property_domains(tenant: Tenant, discovered_domains: Sequence[str]) -> list[str]:
    """Domains worth showing in the bundle editor."""
    domains = sorted({domain for domain in [tenant.primary_domain, *discovered_domains] if domain})
    non_local = [domain for domain in domains if domain and domain != "localhost" and not domain.endswith(".localhost")]
    if non_local:
        return non_local
    return [tenant.primary_domain] if tenant.primary_domain else []


def _default_property_tag_rows(tenant_domains: Sequence[str]) -> list[dict[str, str]]:
    return [{"domain": domain, "tags": "all_inventory"} for domain in tenant_domains if domain]


def _parse_tag_publisher_properties(form, default_domain: str, allowed_domains: set[str]) -> list[dict]:
    """Parse one or more publisher-domain/tag rows from bundle forms."""
    rows_domains = form.getlist("publisher_domain[]")
    rows_tags_raw = form.getlist("property_tags[]")
    if not rows_domains:
        rows_domains = [default_domain]
        rows_tags_raw = [form.get("property_tags", "")]

    publisher_props: list[dict] = []
    for row_idx, (domain, tag_str) in enumerate(zip(rows_domains, rows_tags_raw, strict=False)):
        domain = (domain or "").strip()
        if not domain:
            raise ValueError(f"Row {row_idx + 1}: publisher domain is required")
        if domain not in allowed_domains:
            raise ValueError(f"Row {row_idx + 1}: publisher domain '{domain}' is not authorized for this tenant")

        tags_in = [t.strip().lower() for t in (tag_str or "").split(",")]
        tags_clean = [t for t in tags_in if t]
        if not tags_clean:
            raise ValueError(f"Row {row_idx + 1} ({domain}): add at least one property tag")

        bad = next((t for t in tags_clean if not TAG_PATTERN.match(t)), None)
        if bad:
            raise ValueError(f"Row {row_idx + 1} ({domain}): tag '{bad}' must use lowercase, numbers, underscores.")

        publisher_props.append(
            {
                "publisher_domain": domain,
                "property_tags": tags_clean,
                "selection_type": "by_tag",
            }
        )
    return publisher_props


def _authorized_property_ids_by_domain(session, tenant_id: str) -> dict[str, set[str]]:
    rows = session.execute(
        select(AuthorizedProperty.publisher_domain, AuthorizedProperty.property_id).where(
            AuthorizedProperty.tenant_id == tenant_id
        )
    ).all()
    ids_by_domain: dict[str, set[str]] = {}
    for domain, property_id in rows:
        ids_by_domain.setdefault(domain, set()).add(property_id)
    return ids_by_domain


def _parse_full_publisher_properties_json(
    raw: str,
    allowed_domains: set[str],
    authorized_property_ids_by_domain: dict[str, set[str]] | None = None,
) -> list[dict]:
    """Parse canonical publisher_properties JSON from the bundle editor."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid publisher properties JSON: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise ValueError("Publisher properties are required")

    publisher_props: list[dict] = []
    for row_idx, prop in enumerate(parsed, start=1):
        if not isinstance(prop, dict):
            raise ValueError(f"Row {row_idx}: publisher property must be an object")
        domain = (prop.get("publisher_domain") or "").strip()
        if not domain:
            raise ValueError(f"Row {row_idx}: publisher domain is required")
        if domain not in allowed_domains:
            raise ValueError(f"Row {row_idx}: publisher domain '{domain}' is not authorized for this tenant")

        property_ids = [str(item).strip() for item in prop.get("property_ids") or [] if str(item).strip()]
        property_tags = [str(item).strip().lower() for item in prop.get("property_tags") or [] if str(item).strip()]
        if not property_ids and not property_tags:
            raise ValueError(f"Row {row_idx} ({domain}): choose at least one property tag or ID")

        if property_ids and authorized_property_ids_by_domain is not None:
            valid_ids = authorized_property_ids_by_domain.get(domain, set())
            invalid_ids = sorted(set(property_ids) - valid_ids)
            if invalid_ids:
                raise ValueError(f"Row {row_idx} ({domain}): invalid property IDs: {', '.join(invalid_ids)}")

        bad = next((tag for tag in property_tags if not TAG_PATTERN.match(tag)), None)
        if bad:
            raise ValueError(f"Row {row_idx} ({domain}): tag '{bad}' must use lowercase, numbers, underscores.")

        if property_ids:
            publisher_props.append(
                {
                    "publisher_domain": domain,
                    "property_ids": property_ids,
                    "selection_type": "by_id",
                }
            )
        else:
            publisher_props.append(
                {
                    "publisher_domain": domain,
                    "property_tags": property_tags,
                    "selection_type": "by_tag",
                }
            )
    return publisher_props


def _get_inventory_summary(inventory_config: dict) -> str:
    """Generate human-readable inventory summary.

    Args:
        inventory_config: Inventory configuration dict

    Returns:
        Summary string (e.g., "12 ad units, 3 placements")
    """
    ad_units = len(inventory_config.get("ad_units", []))
    placements = len(inventory_config.get("placements", []))

    parts = []
    if ad_units:
        parts.append(f"{ad_units} ad unit{'s' if ad_units != 1 else ''}")
    if placements:
        parts.append(f"{placements} placement{'s' if placements != 1 else ''}")

    return ", ".join(parts) if parts else "No inventory"


def _get_format_summary(formats: list[dict], tenant_id: str) -> str:
    """Generate human-readable format summary.

    Args:
        formats: List of format ID objects
        tenant_id: Tenant ID for format lookup

    Returns:
        Summary string (e.g., "Display (300x250, 728x90), Video")
    """
    if not formats:
        return "No formats"

    groups = _format_display_groups(formats)
    format_names = _format_display_summary_for_groups(groups, limit=5)

    summary = ", ".join(format_names)
    if len(groups) > 5:
        summary += f" (+{len(groups) - 5} more)"

    return summary


def _get_property_summary(publisher_properties: list[dict]) -> str:
    """Generate human-readable property summary.

    Args:
        publisher_properties: List of publisher property objects

    Returns:
        Summary string (e.g., "2 domains, 5 properties")
    """
    if not publisher_properties:
        return "No properties"

    domains = {p["publisher_domain"] for p in publisher_properties if "publisher_domain" in p}

    # Count property IDs and tags
    property_count = 0
    tag_count = 0
    for prop in publisher_properties:
        property_count += len(prop.get("property_ids", []))
        tag_count += len(prop.get("property_tags", []))

    parts = [f"{len(domains)} domain{'s' if len(domains) != 1 else ''}"]
    if property_count:
        parts.append(f"{property_count} propert{'ies' if property_count != 1 else 'y'}")
    if tag_count:
        parts.append(f"{tag_count} tag{'s' if tag_count != 1 else ''}")

    return ", ".join(parts)


@inventory_profiles_bp.route("/")
@require_tenant_access()
def list_inventory_profiles(tenant_id: str):
    """List all inventory profiles for this tenant.

    Surfaces the design handoff's coverage strip + enriched bundle cards +
    "What's not bundled" rail. The render data is shaped to make the
    template purely presentational — no business logic in Jinja.
    """
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)

        # Bundles + product-usage counts in one query (prevents N+1).
        stmt = (
            select(InventoryProfile, func.count(Product.product_id).label("product_count"))
            .outerjoin(Product, InventoryProfile.id == Product.inventory_profile_id)
            .where(InventoryProfile.tenant_id == tenant_id)
            .group_by(InventoryProfile.id)
            .order_by(InventoryProfile.name)
        )
        results = session.execute(stmt).all()

        bundles_data = [_build_bundle_card(profile, product_count) for profile, product_count in results]

        # Bundle-adapter dispatch (#521). Adapters that report real
        # inventory (GAM today) light up coverage + unbundled rail; stubs
        # (FreeWheel, SpringServe) keep label + vocab visible but return
        # empty rails until their sync surfaces participate.
        adapter = adapter_for_tenant(tenant.ad_server) if tenant else None
        if adapter is not None:
            coverage = _build_coverage_summary(session, tenant_id, bundles_data, adapter)
            for card, (profile, _) in zip(bundles_data, results, strict=True):
                _attach_bundle_card_coverage(card, session, tenant_id, profile, adapter, coverage["adUnitsTotal"])
            unbundled_items = _list_unbundled_inventory(session, tenant_id, limit=50, adapter=adapter)
            adapter_label = adapter.label
            adapter_vocab = {
                "ad_units": adapter.vocab["primary"],
                "placements": adapter.vocab["secondary"],
            }
            has_synced_inventory = adapter.has_synced_inventory(session, tenant_id)
            # Seed suggestions: surface top-level placements as "promote into
            # a bundle" candidates when the tenant has zero bundles. The list
            # is a peek (5 max) — operators with hundreds of placements use
            # the full browser. Stub adapters return [].
            seed_suggestions = (
                _list_seed_suggestions(session, tenant_id, limit=5, adapter=adapter) if len(bundles_data) == 0 else []
            )
        else:
            # Tenant on an ad server with no registered adapter — keep the
            # page renderable but minimal.
            coverage = None
            for card in bundles_data:
                card["coverage"] = None
            unbundled_items = []
            adapter_label = (
                (tenant.ad_server if tenant and tenant.ad_server else "your ad server").replace("_", " ").title()
            )
            adapter_vocab = {"ad_units": "ad units", "placements": "placements"}
            has_synced_inventory = False
            seed_suggestions = []

    return render_template(
        "inventory_profiles_list.html",
        tenant_id=tenant_id,
        tenant=tenant,
        bundles=bundles_data,
        coverage=coverage,
        unbundled_items=unbundled_items,
        adapter_label=adapter_label,
        adapter_vocab=adapter_vocab,
        has_synced_inventory=has_synced_inventory,
        seed_suggestions=seed_suggestions,
    )


def _build_bundle_card(profile: InventoryProfile, product_count: int) -> dict:
    """Card-shape payload for one bundle on the redesigned list page.

    The template reads these keys directly — keep the contract stable.
    """
    config = profile.inventory_config or {}
    ad_units = config.get("ad_units") or []
    placements = config.get("placements") or []
    formats = profile.format_ids or []
    format_groups = _format_display_groups(formats)
    publisher_properties = profile.publisher_properties or []

    # Property tags vs property_ids — both shapes are valid; the design
    # surfaces tags by default and falls back to a count of specific
    # properties when the operator picked them explicitly.
    property_tags: list[str] = []
    property_id_count = 0
    for prop in publisher_properties:
        property_tags.extend(prop.get("property_tags") or [])
        property_id_count += len(prop.get("property_ids") or [])
    property_mode = "tag" if property_tags or not property_id_count else "ids"

    return {
        "id": profile.id,
        "profile_id": profile.profile_id,
        "name": profile.name,
        "description": profile.description or "",
        "ad_unit_count": len(ad_units),
        "placement_count": len(placements),
        "format_count": len(formats),
        "format_ids": [f.get("id", "") for f in formats][:4],
        "format_group_count": len(format_groups),
        "format_labels": _format_display_summary_for_groups(format_groups),
        "property_mode": property_mode,
        "property_tags": sorted(set(property_tags)),
        "property_id_count": property_id_count,
        "products_using": product_count,
        "updated_at": profile.updated_at,
        "coverage": None,
    }


def _attach_bundle_card_coverage(
    card: dict, session, tenant_id: str, profile: InventoryProfile, adapter, ad_units_total: int
) -> None:
    """Attach per-bundle synced ad-unit coverage to a list-card payload (#549)."""
    if ad_units_total <= 0:
        card["coverage"] = None
        return
    card["coverage"] = {
        "covered": adapter.coverage_for_bundle(session, tenant_id, profile.inventory_config or {}),
        "total": ad_units_total,
    }


def _build_coverage_summary(session, tenant_id: str, bundles_data: list[dict], adapter) -> dict:
    """Coverage strip payload — four numbers across the top of the list page.

    Totals come from the bundle adapter (#521); "bundled" counts come from
    the denormalized ``InventoryBundleReference`` table that's kept fresh
    by ``recompute_bundle_references`` at bundle save-time.
    """
    from src.core.database.models import InventoryBundleReference

    ad_units_total = adapter.count_inventory(session, tenant_id, "ad_unit")
    placements_total = adapter.count_inventory(session, tenant_id, "placement")
    ad_units_bundled = (
        session.scalar(
            select(func.count())
            .select_from(InventoryBundleReference)
            .where(
                InventoryBundleReference.tenant_id == tenant_id,
                InventoryBundleReference.adapter == adapter.adapter_id,
                InventoryBundleReference.entity_type == "ad_unit",
            )
        )
        or 0
    )
    placements_bundled = (
        session.scalar(
            select(func.count())
            .select_from(InventoryBundleReference)
            .where(
                InventoryBundleReference.tenant_id == tenant_id,
                InventoryBundleReference.adapter == adapter.adapter_id,
                InventoryBundleReference.entity_type == "placement",
            )
        )
        or 0
    )
    products_composed = sum(b["products_using"] for b in bundles_data)

    return {
        "bundles": len(bundles_data),
        "adUnitsBundled": ad_units_bundled,
        "adUnitsTotal": ad_units_total,
        "placementsBundled": placements_bundled,
        "placementsTotal": placements_total,
        "productsComposed": products_composed,
    }


def _list_unbundled_inventory(session, tenant_id: str, limit: int, adapter) -> list[dict]:
    """Rows for the "What's not bundled" rail.

    Synced entities (per the bundle adapter, #521) that don't appear in
    any ``InventoryBundleReference``. Limit caps the list — operators with
    thousands of unbundled units don't need to scroll through all of them
    on the dashboard; the rail is a peek, not the canonical browser.
    """
    from src.core.database.repositories.inventory_bundle_reference import (
        InventoryBundleReferenceRepository,
    )

    bundle_repo = InventoryBundleReferenceRepository(session, tenant_id)
    bundled_by_type = {
        "ad_unit": bundle_repo.bundled_external_ids(adapter=adapter.adapter_id, entity_type="ad_unit"),
        "placement": bundle_repo.bundled_external_ids(adapter=adapter.adapter_id, entity_type="placement"),
    }
    rows = adapter.list_unbundled(session, tenant_id, bundled_by_type, limit)

    return [
        {
            "id": row.external_id,
            "adapter_id": row.external_id,
            "kind": row.entity_type,
            "name": row.name,
            "meta": row.meta,
        }
        for row in rows
    ]


def _build_bundle_summary(
    profile: InventoryProfile, product_count: int, adapter_label: str, adapter_id: str | None = None
) -> dict:
    """Sidebar summary payload for the edit page.

    Shapes the bundle for at-a-glance review: inventory totals, format count,
    property mode, product usage. The template reads these keys directly.
    """
    config = profile.inventory_config or {}
    publisher_properties = profile.publisher_properties or []

    property_tags: list[str] = []
    property_id_count = 0
    for prop in publisher_properties:
        property_tags.extend(prop.get("property_tags") or [])
        property_id_count += len(prop.get("property_ids") or [])
    property_mode = "tags" if property_tags or not property_id_count else "ids"

    return {
        "adapter_label": adapter_label,
        "adapter_id": adapter_id,
        "ad_unit_count": len(config.get("ad_units") or []),
        "placement_count": len(config.get("placements") or []),
        "format_count": len(profile.format_ids or []),
        "property_mode": property_mode,
        "property_tag_count": len(set(property_tags)),
        "property_id_count": property_id_count,
        "products_using": product_count,
    }


def _compute_blast_radius(session, tenant_id: str, profile: InventoryProfile) -> list[dict]:
    """Which placements/ad units in this bundle also appear in other bundles.

    "Blast radius" = inventory shared with sibling bundles. Reads as context,
    not a warning — bundles share placements, not state, so edits here are
    local. Returned as ``[{kind, external_id, others}]`` where ``others`` is
    the count of *other* bundles also referencing that external_id.

    Builds a single ``{external_id -> set(bundle_ids)}`` index per entity type
    so lookups are O(1). A naive double-loop is O(N · M) over bundles ×
    placements, which gets expensive past a few hundred bundles.
    """
    from src.core.database.repositories.inventory_profile import InventoryProfileRepository

    repo = InventoryProfileRepository(session, tenant_id)
    all_bundles = repo.list_all()

    my_config = profile.inventory_config or {}
    my_placements = my_config.get("placements") or []
    my_ad_units = my_config.get("ad_units") or []
    if not my_placements and not my_ad_units:
        return []

    placement_index: dict[str, set[int]] = {}
    ad_unit_index: dict[str, set[int]] = {}
    for b in all_bundles:
        if b.id == profile.id:
            continue
        cfg = b.inventory_config or {}
        for ext_id in cfg.get("placements") or []:
            placement_index.setdefault(ext_id, set()).add(b.id)
        for ext_id in cfg.get("ad_units") or []:
            ad_unit_index.setdefault(ext_id, set()).add(b.id)

    reused: list[dict] = []
    for ext_id in my_placements:
        bundles = placement_index.get(ext_id)
        if bundles:
            reused.append({"kind": "placement", "external_id": ext_id, "others": len(bundles)})
    for ext_id in my_ad_units:
        bundles = ad_unit_index.get(ext_id)
        if bundles:
            reused.append({"kind": "ad_unit", "external_id": ext_id, "others": len(bundles)})

    return reused


def _resolve_inventory_names(
    session, tenant_id: str, profile: InventoryProfile, adapter
) -> dict[str, dict[str, dict[str, str]]]:
    """Map external IDs in the bundle's inventory_config to human-readable names.

    Solves the editor's "raw IDs are unverifiable" problem (#530). Dispatches
    through the bundle adapter (#521) so each ad server's sync surface plugs
    in without changing the call site.

    Returns shape::

        {
            "ad_units": { external_id: {"name": ..., "id": external_id}, ... },
            "placements": { external_id: {"name": ..., "id": external_id}, ... },
        }

    Missing IDs (e.g., the entity was deleted in the ad server after the
    bundle saved) don't appear in the map — the template falls back to
    showing the raw ID with an "unresolved" marker.
    """
    config = profile.inventory_config or {}
    ad_unit_ids = list(config.get("ad_units") or [])
    placement_ids = list(config.get("placements") or [])
    if not ad_unit_ids and not placement_ids:
        return {"ad_units": {}, "placements": {}}

    ad_unit_rows = adapter.list_inventory_by_ids(session, tenant_id, "ad_unit", ad_unit_ids)
    placement_rows = adapter.list_inventory_by_ids(session, tenant_id, "placement", placement_ids)

    return {
        "ad_units": {row.external_id: {"name": row.name, "id": row.external_id} for row in ad_unit_rows},
        "placements": {row.external_id: {"name": row.name, "id": row.external_id} for row in placement_rows},
    }


def _placement_child_ids(row) -> list[str]:
    metadata = (row.raw or {}).get("metadata") or {}
    raw_ids = metadata.get("ad_unit_ids") or metadata.get("targeted_ad_unit_ids") or []
    return [str(ad_unit_id) for ad_unit_id in raw_ids if ad_unit_id]


def _placement_subkind(row, child_ids: list[str]) -> str:
    metadata = (row.raw or {}).get("metadata") or {}
    explicit = metadata.get("bundle_kind") or metadata.get("placement_kind") or metadata.get("kind")
    if explicit in {"site", "tag"}:
        return explicit
    return "site" if child_ids else "tag"


def _format_ad_unit_sizes(row) -> list[str]:
    metadata = (row.raw or {}).get("metadata") or {}
    sizes = metadata.get("sizes") or []
    formatted: list[str] = []
    for size in sizes:
        if isinstance(size, dict) and size.get("width") and size.get("height"):
            formatted.append(f"{size['width']}x{size['height']}")
        elif isinstance(size, str):
            formatted.append(size)
    return formatted


def _format_inventory_capabilities(row) -> dict:
    metadata = (row.raw or {}).get("metadata") or {}
    return _adcp_inventory_capabilities(metadata)


def _has_unclassified_special_size(row) -> bool:
    metadata = (row.raw or {}).get("metadata") or {}
    sizes = metadata.get("sizes") or []
    return any(_is_gam_special_size(_size_to_tuple(size)) for size in sizes) and not _special_size_is_classified(
        metadata
    )


def _bundle_membership_counts(session, tenant_id: str, ids_by_key: dict[str, set[str]]) -> dict[str, dict[str, int]]:
    """Return direct bundle membership counts for picker row badges."""
    counts: dict[str, dict[str, int]] = {"ad_units": {}, "placements": {}}
    relevant_ids = {key: {str(external_id) for external_id in ids} for key, ids in ids_by_key.items()}
    if not relevant_ids["ad_units"] and not relevant_ids["placements"]:
        return counts

    configs = session.scalars(
        select(InventoryProfile.inventory_config).where(InventoryProfile.tenant_id == tenant_id)
    ).all()
    for config in configs:
        if not isinstance(config, dict):
            continue
        for key in ("ad_units", "placements"):
            for external_id in set(config.get(key) or []):
                external_id = str(external_id)
                if external_id not in relevant_ids[key]:
                    continue
                counts[key][external_id] = counts[key].get(external_id, 0) + 1
    return counts


def _inventory_picker_row(row, bundle_count: int = 0) -> dict:
    child_ids = _placement_child_ids(row) if row.entity_type == "placement" else []
    raw = row.raw or {}
    metadata = raw.get("metadata") or {}
    return {
        "id": row.external_id,
        "name": row.name,
        "kind": row.entity_type,
        "subkind": _placement_subkind(row, child_ids) if row.entity_type == "placement" else "unit",
        "meta": row.meta,
        "path": raw.get("path") or [],
        "status": raw.get("status") or "",
        "child_ids": child_ids,
        "child_count": len(child_ids),
        "parent_id": str(metadata.get("parent_id") or "") if row.entity_type == "ad_unit" else "",
        "sizes": _format_ad_unit_sizes(row) if row.entity_type == "ad_unit" else [],
        "capabilities": _format_inventory_capabilities(row),
        "needs_capability_setup": _has_unclassified_special_size(row) if row.entity_type == "ad_unit" else False,
        "bundle_count": bundle_count,
    }


def _merge_picker_rows(base_rows: list, selected_rows: list) -> list:
    """Keep the bounded default list, plus any currently selected rows."""
    merged = list(base_rows)
    seen = {row.external_id for row in merged}
    for row in selected_rows:
        if row.external_id not in seen:
            merged.append(row)
            seen.add(row.external_id)
    return merged


def _build_inventory_picker_payload(
    session,
    tenant_id: str,
    adapter,
    inventory_config: dict | None = None,
    limit: int = INVENTORY_PICKER_LIMIT,
) -> dict[str, list[dict]]:
    """Synced inventory rows exposed to the in-page bundle picker (#545).

    The editor keeps the save path unchanged: the modal writes selected
    external IDs into the existing hidden textareas. This payload only gives
    the browser enough metadata to search, display a tree-ish path, and render
    chips with human-readable labels before POST.
    """
    if adapter is None:
        return {"ad_units": [], "placements": []}

    selected_ad_unit_ids = list((inventory_config or {}).get("ad_units") or [])
    selected_placement_ids = list((inventory_config or {}).get("placements") or [])

    placement_rows = _merge_picker_rows(
        adapter.list_inventory(session, tenant_id, "placement", limit=limit),
        adapter.list_inventory_by_ids(session, tenant_id, "placement", selected_placement_ids),
    )
    child_ad_unit_ids = sorted({child_id for row in placement_rows for child_id in _placement_child_ids(row)})

    ad_unit_rows = adapter.list_inventory(session, tenant_id, "ad_unit", limit=limit)
    selected_ad_unit_rows = adapter.list_inventory_by_ids(session, tenant_id, "ad_unit", selected_ad_unit_ids)
    child_ad_unit_rows = adapter.list_inventory_by_ids(session, tenant_id, "ad_unit", child_ad_unit_ids)
    ad_unit_rows = _merge_picker_rows(_merge_picker_rows(ad_unit_rows, selected_ad_unit_rows), child_ad_unit_rows)
    membership_counts = _bundle_membership_counts(
        session,
        tenant_id,
        {
            "ad_units": {row.external_id for row in ad_unit_rows},
            "placements": {row.external_id for row in placement_rows},
        },
    )

    payload = {
        "ad_units": [
            _inventory_picker_row(row, membership_counts["ad_units"].get(row.external_id, 0)) for row in ad_unit_rows
        ],
        "placements": [
            _inventory_picker_row(row, membership_counts["placements"].get(row.external_id, 0))
            for row in placement_rows
        ],
    }
    return payload


def _list_products_using(session, tenant_id: str, profile_id: int) -> list[dict]:
    """Products that reference this bundle, with name + id for sidebar rendering.

    Tenant-scoped via the repository. Used by the edit page's Summary card to
    expand the bare "Used by N products" count into linkable product names
    (#530). Capped client-side to top-5 + "+N more" overflow.
    """
    from src.core.database.repositories.product import ProductRepository

    rows = ProductRepository(session, tenant_id).list_by_inventory_profile(profile_id)
    return [{"product_id": r.product_id, "name": r.name} for r in rows]


def _list_seed_suggestions(session, tenant_id: str, limit: int, adapter) -> list[dict]:
    """Synced placements to surface as "promote into a bundle" candidates.

    Empty-state UX (#481): a fresh tenant with thousands of synced ad units
    sees a paralysing blank canvas. Promoting placements is the no-think
    starting point. Dispatches through the bundle adapter (#521) so each
    ad server's notion of "top-level placement" plugs in.
    """
    rows = adapter.list_top_level_placements(session, tenant_id, limit)
    return [
        {
            "external_id": row.external_id,
            "name": row.name,
            "meta": row.meta,
        }
        for row in rows
    ]


@inventory_profiles_bp.route("/add", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("create_inventory_profile")
def add_inventory_profile(tenant_id: str):
    """Create new inventory profile."""
    if request.method == "POST":
        try:
            # Parse form data
            form_data = request.form.to_dict()

            # Basic fields
            name = form_data.get("name", "").strip()
            if not name:
                flash("Bundle name is required", "error")
                return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            profile_id = form_data.get("profile_id", "").strip() or _generate_profile_id(name)
            description = form_data.get("description", "").strip()

            # Parse inventory config
            inventory_config = {
                "ad_units": json.loads(form_data.get("targeted_ad_unit_ids", "[]")),
                "placements": json.loads(form_data.get("targeted_placement_ids", "[]")),
                "include_descendants": form_data.get("include_descendants") == "on",
            }

            submitted_formats = json.loads(form_data.get("formats", "[]"))

            # Parse publisher properties based on property_mode
            # NOTE: New unified inventory page sends either:
            # - property_mode = "all" or "specific" with full publisher_properties JSON
            # - or legacy modes: "tags", "property_ids", "full"
            publisher_properties: list[dict] = []
            property_mode = form_data.get("property_mode", "tags")

            with get_db_session() as prop_session:
                tenant = prop_session.get(Tenant, tenant_id)
                if not tenant or not tenant.primary_domain:
                    flash("Tenant primary_domain not configured", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                publisher_domain = tenant.primary_domain
                allowed_domains = _authorized_publisher_domains(prop_session, tenant_id, tenant)
                authorized_property_ids_by_domain = _authorized_property_ids_by_domain(prop_session, tenant_id)

                if property_mode == "tags":
                    try:
                        publisher_properties = _parse_tag_publisher_properties(
                            request.form, publisher_domain, allowed_domains
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                elif property_mode == "property_ids":
                    # by_id mode: Parse selected_property_ids checkboxes
                    selected_property_ids = request.form.getlist("selected_property_ids")
                    if not selected_property_ids:
                        flash("At least one property must be selected", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    # Verify property_ids exist in authorized properties
                    stmt = select(AuthorizedProperty.property_id).filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id.in_(selected_property_ids),
                    )
                    existing_ids = set(prop_session.scalars(stmt).all())
                    invalid_ids = set(selected_property_ids) - existing_ids
                    if invalid_ids:
                        flash(f"Invalid property IDs: {', '.join(invalid_ids)}", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_ids": selected_property_ids,
                            "selection_type": "by_id",
                        }
                    ]

                elif property_mode in {"full", "all", "specific"}:
                    # "full" is legacy textarea JSON mode
                    # "all"/"specific" are used by the unified inventory page and
                    # send complete publisher_properties JSON in the form field.
                    publisher_properties_json = form_data.get("publisher_properties", "").strip()
                    if publisher_properties_json:
                        try:
                            publisher_properties = _parse_full_publisher_properties_json(
                                publisher_properties_json,
                                allowed_domains,
                                authorized_property_ids_by_domain,
                            )
                        except ValueError as exc:
                            flash(str(exc), "error")
                            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    if not publisher_properties:
                        flash("Publisher properties are required", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            # Parse targeting template (optional)
            targeting_template = None
            targeting_json = form_data.get("targeting_template", "").strip()
            if targeting_json:
                try:
                    targeting_template = json.loads(targeting_json)
                except json.JSONDecodeError as e:
                    flash(f"Invalid targeting template JSON: {e}", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            # Create inventory profile
            with get_db_session() as session:
                # Check for duplicate profile_id
                existing = session.scalar(
                    select(InventoryProfile).where(
                        InventoryProfile.tenant_id == tenant_id, InventoryProfile.profile_id == profile_id
                    )
                )
                if existing:
                    flash(f"Inventory bundle with ID '{profile_id}' already exists", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                unclassified_special_units = _unclassified_gam_special_size_units(session, tenant_id, inventory_config)
                if unclassified_special_units:
                    flash("Classify GAM 1x1 special inventory before saving this bundle.", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                formats = _derive_canonical_format_ids_from_inventory(session, tenant_id, inventory_config)
                if not formats:
                    formats = submitted_formats
                if not formats:
                    flash("Pick inventory with synced creative sizes before saving this bundle.", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                profile = InventoryProfile(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    name=name,
                    description=description if description else None,
                    inventory_config=inventory_config,
                    format_ids=formats,
                    publisher_properties=publisher_properties,
                    targeting_template=targeting_template,
                )

                session.add(profile)
                # Keep InventoryBundleReference in lockstep with the bundle
                # write so the two share a transaction (#485).
                recompute_bundle_references(session, tenant_id)
                session.commit()

                flash(f"Inventory bundle '{name}' created successfully!", "success")
                return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating inventory profile: {e}", exc_info=True)
            flash(f"Error creating inventory bundle: {str(e)}", "error")
            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

    # GET: Show form
    with get_db_session() as session:
        # Get tenant and check primary_domain upfront
        tenant = session.get(Tenant, tenant_id)
        if not tenant or not tenant.primary_domain:
            flash("Tenant primary_domain must be configured before creating inventory bundles", "warning")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        # Get authorized properties for property selection
        authorized_properties = session.scalars(
            select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)
        ).all()

        # Get property tags (as PropertyTag objects, not strings)
        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

        tenant_domains = _bundle_authorable_domains(session, tenant_id, tenant)

        seed_placement = (request.args.get("seed_placement") or "").strip()
        profile = InventoryProfile(
            tenant_id=tenant_id,
            profile_id="",
            name="",
            description="",
            inventory_config={
                "ad_units": [],
                "placements": [seed_placement] if seed_placement else [],
                "include_descendants": True,
            },
            format_ids=[],
            publisher_properties=[],
            targeting_template=None,
        )

        adapter = adapter_for_tenant(tenant.ad_server)
        adapter_label = (
            adapter.label if adapter is not None else (tenant.ad_server or "your ad server").replace("_", " ").title()
        )
        bundle_summary = _build_bundle_summary(
            profile,
            product_count=0,
            adapter_label=adapter_label,
            adapter_id=adapter.adapter_id if adapter is not None else None,
        )
        inventory_names = (
            _resolve_inventory_names(session, tenant_id, profile, adapter)
            if adapter is not None
            else {"ad_units": {}, "placements": {}}
        )
        inventory_picker = _build_inventory_picker_payload(session, tenant_id, adapter, profile.inventory_config)
        known_property_tags = sorted({tag for prop in authorized_properties for tag in (prop.tags or [])})

    return render_template(
        "edit_inventory_profile.html",
        tenant_id=tenant_id,
        tenant=tenant,
        profile=profile,
        authorized_properties=authorized_properties,
        property_tags=property_tags_list,
        tenant_domains=tenant_domains,
        tag_rows=_default_property_tag_rows(tenant_domains),
        bundle_summary=bundle_summary,
        blast_radius=[],
        inventory_names=inventory_names,
        inventory_picker=inventory_picker,
        inventory_picker_limit=INVENTORY_PICKER_LIMIT,
        known_property_tags=known_property_tags,
        products_using=[],
        form_mode="create",
        active_tab="inventory_profiles",
    )


@inventory_profiles_bp.route("/<int:profile_id>/edit", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("update_inventory_profile")
def edit_inventory_profile(tenant_id: str, profile_id: int):
    """Edit existing inventory profile."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            flash("Inventory bundle not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        if request.method == "POST":
            try:
                # Parse form data (same as add)
                form_data = request.form.to_dict()

                # Update fields
                name = form_data.get("name", "").strip()
                if name:
                    profile.name = name

                description = form_data.get("description", "").strip()
                profile.description = description if description else None

                # Update inventory config
                inventory_config = {
                    "ad_units": json.loads(form_data.get("targeted_ad_unit_ids", "[]")),
                    "placements": json.loads(form_data.get("targeted_placement_ids", "[]")),
                    "include_descendants": form_data.get("include_descendants") == "on",
                }
                profile.inventory_config = inventory_config

                submitted_formats = json.loads(form_data.get("formats", "[]"))
                unclassified_special_units = _unclassified_gam_special_size_units(session, tenant_id, inventory_config)
                if unclassified_special_units:
                    flash("Classify GAM 1x1 special inventory before saving this bundle.", "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

                formats = _derive_canonical_format_ids_from_inventory(session, tenant_id, inventory_config)
                if not formats:
                    formats = submitted_formats
                if not formats:
                    flash("Pick inventory with synced creative sizes before saving this bundle.", "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )
                profile.format_ids = formats

                # Update publisher properties based on property_mode
                property_mode = form_data.get("property_mode", "tags")

                tenant_obj = session.get(Tenant, tenant_id)
                if not tenant_obj or not tenant_obj.primary_domain:
                    flash("Tenant primary_domain not configured", "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

                publisher_domain = tenant_obj.primary_domain
                allowed_domains = _authorized_publisher_domains(session, tenant_id, tenant_obj)
                authorized_property_ids_by_domain = _authorized_property_ids_by_domain(session, tenant_id)

                if property_mode == "tags":
                    # Multi-domain (#532): each row in the editor is a
                    # ``(publisher_domain, tags)`` pair. Inputs arrive as
                    # parallel ``domain[]`` + ``property_tags[]`` lists.
                    # Falling back to the single ``property_tags`` field
                    # keeps single-row callers (legacy POSTs, the GAM
                    # product config form) working.
                    try:
                        profile.publisher_properties = _parse_tag_publisher_properties(
                            request.form, publisher_domain, allowed_domains
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                elif property_mode == "property_ids":
                    # by_id mode: Parse selected_property_ids checkboxes
                    selected_property_ids = request.form.getlist("selected_property_ids")
                    if not selected_property_ids:
                        flash("At least one property must be selected", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    # Verify property_ids exist in authorized properties
                    stmt = select(AuthorizedProperty.property_id).filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id.in_(selected_property_ids),
                    )
                    existing_ids = set(session.scalars(stmt).all())
                    invalid_ids = set(selected_property_ids) - existing_ids
                    if invalid_ids:
                        flash(f"Invalid property IDs: {', '.join(invalid_ids)}", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    profile.publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_ids": selected_property_ids,
                            "selection_type": "by_id",
                        }
                    ]

                elif property_mode == "full":
                    # Legacy full mode: Parse JSON from textarea
                    publisher_properties_json = form_data.get("publisher_properties", "").strip()
                    if publisher_properties_json:
                        try:
                            publisher_properties = _parse_full_publisher_properties_json(
                                publisher_properties_json,
                                allowed_domains,
                                authorized_property_ids_by_domain,
                            )
                            profile.publisher_properties = publisher_properties
                        except ValueError as exc:
                            flash(str(exc), "error")
                            return redirect(
                                url_for(
                                    "inventory_profiles.edit_inventory_profile",
                                    tenant_id=tenant_id,
                                    profile_id=profile_id,
                                )
                            )

                # Update targeting template
                targeting_json = form_data.get("targeting_template", "").strip()
                if targeting_json:
                    try:
                        targeting_template = json.loads(targeting_json)
                        profile.targeting_template = targeting_template
                    except json.JSONDecodeError as e:
                        flash(f"Invalid targeting template JSON: {e}", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )
                else:
                    profile.targeting_template = None

                # Count products using this profile before commit
                product_count = (
                    session.scalar(
                        select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id)
                    )
                    or 0
                )

                # Reconcile InventoryBundleReference for the new bundle config (#485).
                recompute_bundle_references(session, tenant_id)
                session.commit()

                # Success message with warning about future updates
                flash(f"Inventory bundle '{profile.name}' updated successfully!", "success")
                if product_count > 0:
                    flash(
                        f"Note: This profile is used by {product_count} product(s). "
                        "Changes will affect future media buys using these products. "
                        "Existing campaigns in GAM will not be modified.",
                        "info",
                    )
                return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

            except Exception as e:
                logger.error(f"Error updating inventory profile: {e}", exc_info=True)
                flash(f"Error updating inventory bundle: {str(e)}", "error")
                session.rollback()

        # GET: Show form with existing data
        # Get tenant and check primary_domain upfront
        tenant = session.get(Tenant, tenant_id)
        if not tenant or not tenant.primary_domain:
            flash("Tenant primary_domain must be configured before editing inventory bundles", "warning")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        authorized_properties = session.scalars(
            select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)
        ).all()

        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

        # Domains the tenant can author against (#532). Verified
        # PublisherPartner rows are enough for all-property/tag bundle binding;
        # specific property IDs still require hydrated AuthorizedProperty rows.
        tenant_domains = _bundle_authorable_domains(session, tenant_id, tenant)

        # Initial tag-mode rows for progressive-enhancement render (#532).
        # If the bundle is in tag mode, one row per existing publisher_properties
        # entry; else a single row pinned to the primary domain so a brand-new
        # bundle still ships with a sane default.
        tag_rows: list[dict] = []
        for prop in profile.publisher_properties or []:
            if prop.get("property_tags"):
                tag_rows.append(
                    {
                        "domain": prop.get("publisher_domain", tenant.primary_domain),
                        "tags": ", ".join(prop.get("property_tags") or []),
                    }
                )
        if not tag_rows:
            tag_rows = _default_property_tag_rows(tenant_domains)

        product_count = (
            session.scalar(select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id))
            or 0
        )
        # Adapter dispatch (#521) — label + name resolution. Stub adapters
        # (FW/SS) return empty name maps so chips render the raw id.
        adapter = adapter_for_tenant(tenant.ad_server)
        adapter_label = (
            adapter.label if adapter is not None else (tenant.ad_server or "your ad server").replace("_", " ").title()
        )
        bundle_summary = _build_bundle_summary(
            profile,
            product_count,
            adapter_label,
            adapter_id=adapter.adapter_id if adapter is not None else None,
        )
        blast_radius = _compute_blast_radius(session, tenant_id, profile)

        inventory_names = (
            _resolve_inventory_names(session, tenant_id, profile, adapter)
            if adapter is not None
            else {"ad_units": {}, "placements": {}}
        )
        inventory_picker = _build_inventory_picker_payload(session, tenant_id, adapter, profile.inventory_config)
        known_property_tags = sorted({tag for prop in authorized_properties for tag in (prop.tags or [])})
        products_using = _list_products_using(session, tenant_id, profile_id)

        # Render inside the session so JSON columns (``profile.format_ids``,
        # ``profile.inventory_config``, etc.) are accessible from the template.
        # Outside the ``with`` the instance is detached and SQLAlchemy raises
        # on lazy-loaded attribute access — Jinja swallows that to Undefined,
        # which then ``tojson`` chokes on. (Verified locally with profile_id=7.)
        return render_template(
            "edit_inventory_profile.html",
            tenant_id=tenant_id,
            tenant=tenant,
            profile=profile,
            authorized_properties=authorized_properties,
            property_tags=property_tags_list,
            tenant_domains=tenant_domains,
            tag_rows=tag_rows,
            bundle_summary=bundle_summary,
            blast_radius=blast_radius,
            inventory_names=inventory_names,
            inventory_picker=inventory_picker,
            inventory_picker_limit=INVENTORY_PICKER_LIMIT,
            known_property_tags=known_property_tags,
            products_using=products_using,
            active_tab="inventory_profiles",
        )


def _resolve_reuse_source(session, tenant_id: str, external_id: str, kind: str, adapter) -> dict | None:
    """Look up the ad_unit or placement being reused (#524).

    Dispatches through the bundle adapter (#521). When the entity isn't in
    the adapter's synced inventory (deleted upstream, or the tenant is on
    a stub adapter), ``found=False`` and ``name`` falls back to the raw id
    so the operator can still see what they clicked.
    """
    if kind not in {"placement", "ad_unit"}:
        return None

    row = adapter.find_inventory_item(session, tenant_id, kind, external_id)
    if row is None:
        return {
            "external_id": external_id,
            "kind": kind,
            "name": external_id,
            "meta": "Not found in synced inventory",
            "found": False,
        }
    return {
        "external_id": row.external_id,
        "kind": row.entity_type,
        "name": row.name,
        "meta": row.meta,
        "found": True,
    }


def _bundle_membership_picklist(session, tenant_id: str, external_id: str, kind: str) -> list[dict]:
    """Per-bundle picklist payload for the Reuse page (#524).

    Each entry: ``{id, name, description, profile_id, already, summary,
    products_using}``. ``already`` is true when this bundle already contains
    the entity — those rows render as "already includes" in the template.
    """
    from src.core.database.repositories.inventory_profile import InventoryProfileRepository

    bundles = InventoryProfileRepository(session, tenant_id).list_all()

    # Product counts in a single query to avoid N+1.
    product_counts = dict(
        session.execute(
            select(Product.inventory_profile_id, func.count(Product.product_id))
            .where(Product.tenant_id == tenant_id)
            .group_by(Product.inventory_profile_id)
        ).all()
    )

    config_key = "ad_units" if kind == "ad_unit" else "placements"
    out: list[dict] = []
    for b in bundles:
        cfg = b.inventory_config or {}
        ids_in_bundle = cfg.get(config_key) or []
        already = external_id in ids_in_bundle
        au_count = len(cfg.get("ad_units") or [])
        pl_count = len(cfg.get("placements") or [])
        fmt_count = len(b.format_ids or [])
        shape_parts = []
        if pl_count:
            shape_parts.append(f"{pl_count} {'placement' if pl_count == 1 else 'placements'}")
        if au_count:
            shape_parts.append(f"{au_count} {'ad unit' if au_count == 1 else 'ad units'}")
        out.append(
            {
                "id": b.id,
                "name": b.name,
                "description": b.description or "",
                "profile_id": b.profile_id,
                "already": already,
                "summary": ", ".join(shape_parts) if shape_parts else "no inventory",
                "format_count": fmt_count,
                "products_using": int(product_counts.get(b.id, 0)),
            }
        )
    # Already-includes at the top so the operator sees what's done first.
    out.sort(key=lambda r: (not r["already"], r["name"].lower()))
    return out


@inventory_profiles_bp.route("/reuse")
@require_tenant_access()
def reuse_inventory_bundles(tenant_id: str):
    """Render the reverse-add picker: one inventory item → many bundles (#524).

    Query params:
        item: external_id of the ad_unit or placement being reused.
        kind: ``placement`` | ``ad_unit``.
    """
    external_id = (request.args.get("item") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    if not external_id or kind not in {"placement", "ad_unit"}:
        flash("Missing item or kind — pick a row from the bundles list.", "error")
        return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        adapter = adapter_for_tenant(tenant.ad_server) if tenant else None
        source = (
            _resolve_reuse_source(session, tenant_id, external_id, kind, adapter)
            if adapter is not None
            else {
                "external_id": external_id,
                "kind": kind,
                "name": external_id,
                "meta": "Not found in synced inventory",
                "found": False,
            }
        )
        bundles = _bundle_membership_picklist(session, tenant_id, external_id, kind)
        adapter_label = (
            adapter.label
            if adapter is not None
            else (tenant.ad_server if tenant and tenant.ad_server else "your ad server").replace("_", " ").title()
        )

        return render_template(
            "reuse_inventory_bundles.html",
            tenant_id=tenant_id,
            tenant=tenant,
            adapter_label=adapter_label,
            source=source,
            bundles=bundles,
            active_tab="inventory_profiles",
        )


@inventory_profiles_bp.route("/reuse", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("reuse_inventory_to_bundles")
def reuse_inventory_bundles_save(tenant_id: str):
    """Apply the reverse-add diff: insert the item into selected bundles (#524).

    Only adds — never removes. The operator who wants to remove an item
    from a bundle does so from that bundle's editor. This avoids accidental
    bulk-deletes from the reuse page.
    """
    from src.core.database.repositories.inventory_profile import InventoryProfileRepository

    external_id = (request.form.get("item") or "").strip()
    kind = (request.form.get("kind") or "").strip()
    if not external_id or kind not in {"placement", "ad_unit"}:
        flash("Missing item or kind on add-to-bundles submission.", "error")
        return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

    selected_ids = [int(b) for b in request.form.getlist("bundle_ids") if b.strip().isdigit()]
    if not selected_ids:
        flash("Pick at least one bundle to add this to.", "warning")
        return redirect(
            url_for(
                "inventory_profiles.reuse_inventory_bundles",
                tenant_id=tenant_id,
                item=external_id,
                kind=kind,
            )
        )

    config_key = "ad_units" if kind == "ad_unit" else "placements"
    added_to: list[str] = []
    skipped_count = 0
    with get_db_session() as session:
        repo = InventoryProfileRepository(session, tenant_id)
        for pk in selected_ids:
            bundle = repo.get_by_pk(pk)
            if bundle is None:
                # Cross-tenant or stale id — silently skip; nothing to log.
                continue
            cfg = dict(bundle.inventory_config or {})
            current = list(cfg.get(config_key) or [])
            if external_id in current:
                skipped_count += 1
                continue
            current.append(external_id)
            cfg[config_key] = current
            bundle.inventory_config = cfg
            added_to.append(bundle.name)
        recompute_bundle_references(session, tenant_id)
        session.commit()

    if added_to:
        flash(
            f"Added to {len(added_to)} {'bundle' if len(added_to) == 1 else 'bundles'}: {', '.join(added_to)}.",
            "success",
        )
    if skipped_count:
        flash(
            f"{skipped_count} {'bundle' if skipped_count == 1 else 'bundles'} already had this item — left alone.",
            "info",
        )
    return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))


@inventory_profiles_bp.route("/<int:profile_id>/duplicate", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("duplicate_inventory_profile")
def duplicate_inventory_profile(tenant_id: str, profile_id: int):
    """Duplicate an inventory bundle and open the copy in the editor.

    Copies bundle-shaped fields (inventory, formats, properties, targeting, constraints,
    description) but does NOT copy GAM preset bindings — those are 1:1 with a single bundle
    and would create ambiguous sync targets.
    """
    with get_db_session() as session:
        source = session.get(InventoryProfile, profile_id)
        if not source or source.tenant_id != tenant_id:
            flash("Inventory bundle not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        new_name = f"{source.name} (copy)"
        new_profile_id = _unique_profile_id(session, tenant_id, _generate_profile_id(new_name))

        copy = InventoryProfile(
            tenant_id=tenant_id,
            profile_id=new_profile_id,
            name=new_name,
            description=source.description,
            inventory_config=source.inventory_config,
            format_ids=source.format_ids,
            publisher_properties=source.publisher_properties,
            targeting_template=source.targeting_template,
            constraints=source.constraints,
        )
        session.add(copy)
        session.flush()
        new_id = copy.id
        # Pick up the new bundle in the bundle-reference index (#485).
        recompute_bundle_references(session, tenant_id)
        session.commit()

        flash(f"Duplicated '{source.name}' — editing the copy now.", "success")
        # ``duplicated=1`` lets the editor autofocus + select the name field
        # so renaming is a single keystroke. User-engagement reviewer (#528)
        # called this the highest-leverage friction fix on the page.
        return redirect(
            url_for(
                "inventory_profiles.edit_inventory_profile",
                tenant_id=tenant_id,
                profile_id=new_id,
                duplicated=1,
            )
        )


@inventory_profiles_bp.route("/<int:profile_id>/delete", methods=["DELETE", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("delete_inventory_profile")
def delete_inventory_profile(tenant_id: str, profile_id: int):
    """Delete inventory profile."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            if request.method == "DELETE":
                return jsonify({"error": "Inventory bundle not found"}), 404
            flash("Inventory bundle not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        # Check if any products use this profile
        product_count = (
            session.scalar(select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id))
            or 0
        )

        if product_count > 0:
            error_msg = f"Cannot delete inventory bundle - used by {product_count} product(s)"
            if request.method == "DELETE":
                return jsonify({"error": error_msg}), 400
            flash(error_msg, "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        profile_name = profile.name
        session.delete(profile)
        # Demote any ad units / placements that were exclusive to this bundle
        # back to ``pending`` (#485).
        recompute_bundle_references(session, tenant_id)
        session.commit()

        if request.method == "DELETE":
            return jsonify({"success": True, "message": f"Inventory bundle '{profile_name}' deleted successfully"})

        flash(f"Inventory bundle '{profile_name}' deleted successfully", "success")
        return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))


@inventory_profiles_bp.route("/<int:profile_id>/api")
@require_tenant_access(api_mode=True)
def get_inventory_profile_api(tenant_id: str, profile_id: int):
    """Get full inventory profile data for editing (API endpoint)."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            return jsonify({"error": "Inventory bundle not found"}), 404

        # Derive property_mode from publisher_properties shape — the same
        # logic the template uses (#528). Hardcoding "all" was a bug:
        # agents pulling the API would mis-render bundles that were saved
        # in tag or property-id mode.
        property_tags: list[str] = []
        property_id_count = 0
        for prop in profile.publisher_properties or []:
            property_tags.extend(prop.get("property_tags") or [])
            property_id_count += len(prop.get("property_ids") or [])
        property_mode = "tags" if property_tags or not property_id_count else "property_ids"

        return jsonify(
            {
                "id": profile.id,
                "profile_id": profile.profile_id,
                "name": profile.name,
                "description": profile.description,
                "inventory_config": profile.inventory_config,
                "targeted_ad_unit_ids": ",".join(profile.inventory_config.get("ad_units", [])),
                "targeted_placement_ids": ",".join(profile.inventory_config.get("placements", [])),
                "include_descendants": profile.inventory_config.get("include_descendants", True),
                "formats": profile.format_ids,
                "publisher_properties": profile.publisher_properties,
                "property_mode": property_mode,
                "targeting_template": profile.targeting_template,
            }
        )


@inventory_profiles_bp.route("/<int:profile_id>/preview")
@require_tenant_access()
def preview_inventory_profile(tenant_id: str, profile_id: int):
    """HTML preview of the buyer-facing bundle card.

    The "shipped something" moment for operators (#531) — clicking Preview on
    the edit page or the list-page overflow now lands on a real page that
    renders the bundle's buyer-facing shape, not the JSON dump the route used
    to return.

    Old JSON consumers (the GAM product config form) go through the sibling
    ``/api/preview`` route below.
    """
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)
        if not profile or profile.tenant_id != tenant_id:
            flash("Inventory bundle not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        tenant = session.get(Tenant, tenant_id)
        adapter = adapter_for_tenant(tenant.ad_server) if tenant else None
        adapter_label = (
            adapter.label
            if adapter is not None
            else (tenant.ad_server if tenant and tenant.ad_server else "your ad server").replace("_", " ").title()
        )

        # Resolve external IDs to human names so the buyer-shape preview
        # mirrors what the chips on the editor now show (#530). Stub
        # adapters (FW/SS) return empty maps until their sync surfaces wire in.
        inventory_names = (
            _resolve_inventory_names(session, tenant_id, profile, adapter)
            if adapter is not None
            else {"ad_units": {}, "placements": {}}
        )

        # Property summary as a structured payload the template can render
        # (vs the comma-separated string the JSON endpoint produces).
        publisher_properties = profile.publisher_properties or []
        property_tags: list[str] = []
        property_id_count = 0
        for prop in publisher_properties:
            property_tags.extend(prop.get("property_tags") or [])
            property_id_count += len(prop.get("property_ids") or [])
        property_mode = "tags" if property_tags or not property_id_count else "ids"

        return render_template(
            "preview_inventory_profile.html",
            tenant_id=tenant_id,
            tenant=tenant,
            profile=profile,
            adapter_label=adapter_label,
            inventory_names=inventory_names,
            property_mode=property_mode,
            property_tags=sorted(set(property_tags)),
            property_id_count=property_id_count,
            publisher_properties=publisher_properties,
            format_groups=_format_display_groups(profile.format_ids or []),
            active_tab="inventory_profiles",
        )


@inventory_profiles_bp.route("/<int:profile_id>/api/preview")
@require_tenant_access(api_mode=True)
def preview_inventory_profile_api(tenant_id: str, profile_id: int):
    """JSON preview payload for callers that need machine-readable shape.

    Carved out of the original ``/preview`` route (#531) so the user-facing
    URL can serve HTML. The GAM product-config JS still hits this; the
    response shape is unchanged.
    """
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            return jsonify({"error": "Inventory bundle not found"}), 404

        return jsonify(
            {
                "id": profile.id,
                "profile_id": profile.profile_id,
                "name": profile.name,
                "description": profile.description,
                "ad_unit_count": len(profile.inventory_config.get("ad_units", [])),
                "placement_count": len(profile.inventory_config.get("placements", [])),
                "format_count": len(profile.format_ids),
                "format_summary": _get_format_summary(profile.format_ids, tenant_id),
                "property_summary": _get_property_summary(profile.publisher_properties),
            }
        )


@inventory_profiles_bp.route("/api/list")
@require_tenant_access(api_mode=True)
def list_inventory_profiles_api(tenant_id: str):
    """Get all inventory profiles for this tenant as JSON (API endpoint for unified page)."""
    with get_db_session() as session:
        # Get all profiles with product counts in a single query (prevents N+1)
        stmt = (
            select(InventoryProfile, func.count(Product.product_id).label("product_count"))
            .outerjoin(Product, InventoryProfile.id == Product.inventory_profile_id)
            .where(InventoryProfile.tenant_id == tenant_id)
            .group_by(InventoryProfile.id)
            .order_by(InventoryProfile.name)
        )

        results = session.execute(stmt).all()

        # Build profile data with summaries
        profiles_data = []
        for profile, product_count in results:
            profiles_data.append(
                {
                    "id": profile.id,
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "description": profile.description,
                    "inventory_summary": _get_inventory_summary(profile.inventory_config),
                    "format_summary": _get_format_summary(profile.format_ids, tenant_id),
                    "property_summary": _get_property_summary(profile.publisher_properties),
                    "product_count": product_count,
                    "created_at": profile.created_at.isoformat() if profile.created_at else None,
                    "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
                }
            )

        return jsonify({"profiles": profiles_data, "total": len(profiles_data)})


def register_blueprint(app: Flask):
    """Register inventory profiles blueprint."""
    app.register_blueprint(inventory_profiles_bp, url_prefix="/admin/tenant/<tenant_id>/inventory-profiles")
