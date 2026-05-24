"""
Targeting capabilities configuration.

Defines which targeting dimensions are available for overlay vs managed-only access.
This is critical for AEE (Ad Effectiveness Engine) integration.

AdCP TargetingOverlay defines: geo_countries, geo_regions, geo_metros,
geo_postal_areas, frequency_cap, property_list, axe_include_segment,
axe_exclude_segment.  Everything else here is a seller extension — standard
ad-server dimensions (device, OS, browser, media type, audience) that AdCP
does not yet define but that adapters actively support.  These are candidates
for upstream inclusion in AdCP.
"""

from typing import TYPE_CHECKING, Any

from src.core.exceptions import AdCPValidationError
from src.core.schemas import Error, Targeting, TargetingCapability

if TYPE_CHECKING:
    from src.core.database.models import Product

# Define targeting capabilities for the platform
TARGETING_CAPABILITIES: dict[str, TargetingCapability] = {
    # ── AdCP-defined dimensions ──────────────────────────────────────────
    # These map directly to fields on adcp.types.TargetingOverlay.
    "geo_country": TargetingCapability(
        dimension="geo_country", access="overlay", description="Country-level targeting using ISO 3166-1 alpha-2 codes"
    ),
    "geo_region": TargetingCapability(dimension="geo_region", access="overlay", description="State/province targeting"),
    "geo_metro": TargetingCapability(dimension="geo_metro", access="overlay", description="Metro/DMA targeting"),
    "geo_zip": TargetingCapability(dimension="geo_zip", access="overlay", description="Postal code targeting"),
    "frequency_cap": TargetingCapability(
        dimension="frequency_cap", access="overlay", description="Impression frequency limits"
    ),
    "device_platform": TargetingCapability(
        dimension="device_platform",
        access="overlay",
        description="OS-level platform targeting (Sec-CH-UA-Platform values)",
        allowed_values=[
            "ios",
            "android",
            "windows",
            "macos",
            "linux",
            "chromeos",
            "tvos",
            "tizen",
            "webos",
            "fire_os",
            "roku_os",
        ],
    ),
    # ── Seller extensions ────────────────────────────────────────────────
    # Standard ad-server dimensions not yet in AdCP TargetingOverlay.
    # Adapters (GAM, Kevel, Triton, Xandr) actively consume these.
    # Candidates for upstream AdCP inclusion.
    "device_type": TargetingCapability(
        dimension="device_type",
        access="overlay",
        description="Device type targeting",
        allowed_values=["mobile", "desktop", "tablet", "ctv", "dooh", "audio"],
    ),
    "device_make": TargetingCapability(
        dimension="device_make", access="overlay", description="Device manufacturer targeting"
    ),
    "os": TargetingCapability(dimension="os", access="overlay", description="Operating system targeting"),
    "browser": TargetingCapability(dimension="browser", access="overlay", description="Browser targeting"),
    "content_category": TargetingCapability(
        dimension="content_category", access="overlay", description="IAB content category targeting"
    ),
    "content_language": TargetingCapability(
        dimension="content_language", access="overlay", description="Content language targeting"
    ),
    "content_rating": TargetingCapability(
        dimension="content_rating", access="overlay", description="Content rating targeting"
    ),
    "media_type": TargetingCapability(
        dimension="media_type",
        access="overlay",
        description="Media type targeting",
        allowed_values=["video", "display", "native", "audio", "dooh"],
    ),
    "audience_segment": TargetingCapability(
        dimension="audience_segment", access="overlay", description="Third-party audience segments"
    ),
    "custom": TargetingCapability(dimension="custom", access="both", description="Platform-specific custom targeting"),
    # ── Removed dimensions ───────────────────────────────────────────────
    "geo_city": TargetingCapability(
        dimension="geo_city",
        access="removed",
        description="City-level targeting (removed in v3, no adapter supports it)",
    ),
    # ── Managed-only (AEE signal integration) ────────────────────────────
    "key_value_pairs": TargetingCapability(
        dimension="key_value_pairs",
        access="managed_only",
        description="Key-value pairs for AEE signal integration",
        axe_signal=True,
    ),
    "aee_segment": TargetingCapability(
        dimension="aee_segment", access="managed_only", description="AEE-computed audience segments", axe_signal=True
    ),
    "aee_score": TargetingCapability(
        dimension="aee_score", access="managed_only", description="AEE effectiveness scores", axe_signal=True
    ),
    "aee_context": TargetingCapability(
        dimension="aee_context", access="managed_only", description="AEE contextual signals", axe_signal=True
    ),
}


def get_overlay_dimensions() -> list[str]:
    """Get list of dimensions available for overlay targeting."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access in ["overlay", "both"]]


def get_managed_only_dimensions() -> list[str]:
    """Get list of dimensions that are managed-only."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access == "managed_only"]


def get_removed_dimensions() -> list[str]:
    """Get list of dimensions that have been removed."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access == "removed"]


def get_aee_signal_dimensions() -> list[str]:
    """Get list of dimensions used for AEE signals."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.axe_signal]


# Explicit mapping from Targeting field names to capability dimension names.
# Used by validate_overlay_targeting() to check access control (managed-only
# vs overlay) on known fields.  Both inclusion and exclusion variants map to
# the same capability dimension.
#
# AdCP TargetingOverlay defines only the geo fields, frequency_cap, axe
# segments, and property_list.  The device/OS/browser/media/audience fields
# are seller extensions carried forward from the original seller engine —
# standard ad-server dimensions that adapters actively support but AdCP has
# not yet adopted.  See module docstring for details.
FIELD_TO_DIMENSION: dict[str, str] = {
    # ── AdCP-defined fields (from adcp.types.TargetingOverlay) ───────────
    "geo_countries": "geo_country",
    "geo_regions": "geo_region",
    "geo_metros": "geo_metro",
    "geo_postal_areas": "geo_zip",
    "frequency_cap": "frequency_cap",
    # ── Geo exclusion extensions (PR #1006, not yet in AdCP) ─────────────
    "geo_countries_exclude": "geo_country",
    "geo_regions_exclude": "geo_region",
    "geo_metros_exclude": "geo_metro",
    "geo_postal_areas_exclude": "geo_zip",
    # ── AdCP device_platform (OS-level, converted to device_type internally) ──
    "device_platform": "device_platform",
    # ── Seller extensions (not in AdCP, consumed by adapters) ────────────
    "device_type_any_of": "device_type",
    "device_type_none_of": "device_type",
    "os_any_of": "os",
    "os_none_of": "os",
    "browser_any_of": "browser",
    "browser_none_of": "browser",
    "content_cat_any_of": "content_category",
    "content_cat_none_of": "content_category",
    "media_type_any_of": "media_type",
    "media_type_none_of": "media_type",
    "audiences_any_of": "audience_segment",
    "audiences_none_of": "audience_segment",
    "custom": "custom",
    # ── Removed dimensions ───────────────────────────────────────────────
    "geo_city_any_of": "geo_city",
    "geo_city_none_of": "geo_city",
    # ── Managed-only (not exposed via overlay) ───────────────────────────
    "key_value_pairs": "key_value_pairs",
}


def validate_unknown_targeting_fields(targeting_obj: Any) -> list[str]:
    """Reject unknown fields in a Targeting object via model_extra inspection.

    Pydantic's extra='allow' accepts any field — unknown buyer fields (typos,
    bogus names) land in model_extra.  This function checks model_extra and
    reports them as unknown targeting fields.

    This is separate from validate_overlay_targeting() which checks access
    control (managed-only vs overlay) on *known* fields.

    Returns list of violation messages for unknown fields.
    """
    model_extra = getattr(targeting_obj, "model_extra", None)
    if not model_extra:
        return []
    return [f"{key} is not a recognized targeting field" for key in model_extra]


def supports_property_list_filtering(adapter: object | None) -> bool:
    """Return True iff the bound adapter compiles ``targeting_overlay.property_list``.

    Today no adapter sets ``supports_property_list_filtering=True``; the
    declaration in ``get_adcp_capabilities`` is the canonical "False until an
    adapter actually compiles it" anchor. When Kevel's siteId resolver lands,
    Kevel's adapter class will set this ClassVar to True and the helper will
    start returning True for tenants on Kevel. Other adapters hard-reject, at
    which point this advisory path is unreachable for them. Centralizing the
    check here keeps the wire declaration (capabilities) and the per-call
    advisory (this module) in lockstep with one source of truth.
    """
    if adapter is None:
        return False
    return bool(getattr(adapter.__class__, "supports_property_list_filtering", False))


# ─── property_list targeting helpers ────────────────────────────────────
#
# Spec scope: these helpers only handle ``property_list``. The AdCP 3.0.7
# spec governs ``property_list`` via a per-product flag
# (``Product.property_targeting_allowed``) and a per-capability declaration
# (``MediaBuyFeatures.property_list_filtering``). ``collection_list`` and
# ``collection_list_exclude`` use a different mechanism — capability-level
# only — declared in ``get_adcp_capabilities`` per
# ``core/targeting.json:collection_list``: "Seller must declare support in
# get_adcp_capabilities." There is no per-product flag for collection_list,
# so the asymmetry below is spec-defined, not an oversight. Collection-list
# capability infrastructure lands separately.


def build_property_list_unsupported_advisories(
    packages: list[Any] | None,
    capability_supported: bool,
) -> list[Error]:
    """Build per-package ``UNSUPPORTED_FEATURE`` advisories for property_list use.

    AdCP spec 3.0.7 ``error-handling.mdx`` describes non-fatal errors as
    "populate only the payload... MUST NOT populate ``adcp_error``" — i.e.
    advisories ride on the success envelope. Buyers see the silent-drop
    window during the rollout of property_list round-trip and adapter
    compilation, without the request being rejected.

    Returns Error objects for each package whose ``targeting_overlay.property_list``
    is set when the bound adapter does not compile the field. Caller appends
    to ``CreateMediaBuySuccess.errors`` / ``UpdateMediaBuySuccess.errors``.
    """
    if capability_supported or not packages:
        return []

    advisories: list[Error] = []
    for index, package in enumerate(packages):
        overlay = getattr(package, "targeting_overlay", None)
        if overlay is None or getattr(overlay, "property_list", None) is None:
            continue
        advisories.append(
            Error(
                code="UNSUPPORTED_FEATURE",
                message=(
                    "property_list_filtering is declared off for this seller. "
                    "The list_id is persisted on the package but will not affect "
                    "targeting until adapter compilation lands."
                ),
                field=f"packages[{index}].targeting_overlay.property_list",
                suggestion=(
                    "Continue to send property_list; the seller will activate it "
                    "once the adapter compiles list_ids into native targeting."
                ),
            )
        )
    return advisories


def property_list_unsupported_advisories(
    packages: list[Any] | None,
    adapter: object | None,
) -> list[Error] | None:
    """High-level wrapper: build advisories or return ``None`` when none apply.

    Single entry point for both create and update paths; mirrors
    ``MediaBuyFeatures.property_list_filtering`` source-of-truth via
    ``supports_property_list_filtering()`` so the per-call advisory and the
    capability declaration cannot drift. ``None`` (not ``[]``) so the
    optional ``errors`` field round-trips cleanly through
    ``model_dump(exclude_none=True)``.
    """
    advisories = build_property_list_unsupported_advisories(packages, supports_property_list_filtering(adapter))
    return advisories or None


def validate_property_targeting_allowed(product: "Product | None", targeting_overlay: Targeting | None) -> str | None:
    """Reject property_list targeting against products that disallow it.

    AdCP 3.0.6 (core/targeting.json:191): "Sellers SHOULD return a validation
    error if the product has property_targeting_allowed: false."

    Used at both create_media_buy and update_media_buy validation sites; pulled
    here so the rule lives in one place. Pair with
    ``raise_if_property_targeting_violations`` to convert collected violations
    into the wire-shape AdCPValidationError.

    Returns a violation message string, or None when targeting is allowed or
    when the product is missing (caller is responsible for surfacing the
    not-found error via a separate path; this helper must not crash on None).
    """
    if product is None:
        return None
    if (
        targeting_overlay is not None
        and targeting_overlay.property_list is not None
        and not product.property_targeting_allowed
    ):
        return f"Product {product.product_id} does not allow property_list targeting (property_targeting_allowed=false)"
    return None


def raise_if_property_targeting_violations(violations: list[str]) -> None:
    """Raise ``AdCPValidationError`` when any property_targeting violations were collected.

    Centralizes the wire-error envelope shape for property_list rejection so
    create and update paths emit byte-identical error responses (same code,
    same field, same details shape). Caller collects ``violations`` using
    ``validate_property_targeting_allowed()`` — product resolution differs
    between create's in-memory ``product_map`` and update's
    ``uow.products.get_by_id`` lookup, so the collection stays at the call
    site; only the raise shape is shared.
    """
    if violations:
        raise AdCPValidationError(
            f"Targeting validation failed: {'; '.join(violations)}",
            field="packages[].targeting_overlay.property_list",
            details={"violations": violations},
        )


def validate_overlay_targeting(targeting: Targeting) -> list[str]:
    """Validate that targeting only uses allowed overlay dimensions.

    Checks the Targeting model's fields directly instead of iterating a
    serialized dict.  This makes the validation actually effective — the
    previous dict-based approach missed managed-only fields (excluded by
    model_dump) and removed fields (consumed by the normalizer).

    Returns list of violations (managed-only or removed dimensions used).
    """
    violations = []

    # Managed-only: key_value_pairs is a seller extension, not settable via overlay
    if targeting.key_value_pairs is not None:
        violations.append("key_value_pairs is managed-only and cannot be set via overlay")

    # Removed: city targeting was removed in v3. The normalizer consumes
    # geo_city_any_of/geo_city_none_of and sets had_city_targeting=True.
    if targeting.had_city_targeting:
        violations.append("City targeting is not supported (targeting dimension 'geo_city' has been removed)")

    return violations


# Geo inclusion/exclusion field pairs for same-value overlap detection.
# Per adcp PR #1010: sellers SHOULD reject when the same value appears in both
# the inclusion and exclusion field at the same level.
_GEO_SIMPLE_PAIRS: list[tuple[str, str]] = [
    ("geo_countries", "geo_countries_exclude"),
    ("geo_regions", "geo_regions_exclude"),
]
_GEO_STRUCTURED_PAIRS: list[tuple[str, str]] = [
    ("geo_metros", "geo_metros_exclude"),
    ("geo_postal_areas", "geo_postal_areas_exclude"),
]


def _extract_simple_values(items: list) -> set[str]:
    """Extract string values from a list of GeoCountry/GeoRegion (RootModel[str]) or plain strings."""
    return {getattr(item, "root", item) for item in items}


def _extract_system_values(items: list) -> dict[str, set[str]]:
    """Extract {system: set(values)} from a list of GeoMetro/GeoPostalArea objects or dicts."""
    from adcp.types import GeoMetro, GeoPostalArea

    from src.core.validation_helpers import resolve_enum_value

    by_system: dict[str, set[str]] = {}
    for item in items:
        if isinstance(item, (GeoMetro, GeoPostalArea)):
            system = resolve_enum_value(item.system)
            vals = set(item.values)
        elif isinstance(item, dict):
            system = resolve_enum_value(item.get("system", ""))
            vals = set(item.get("values", []))
        else:
            continue
        by_system.setdefault(system, set()).update(vals)
    return by_system


def validate_geo_overlap(targeting: Targeting) -> list[str]:
    """Reject same-value overlap between geo inclusion and exclusion fields.

    Per AdCP spec (adcp PR #1010): sellers SHOULD reject requests where the
    same value appears in both the inclusion and exclusion field at the same
    level (e.g., geo_countries: ["US"] with geo_countries_exclude: ["US"]).

    Returns list of violation messages.
    """
    violations: list[str] = []

    # Simple fields: countries, regions (RootModel[str] or plain strings)
    for include_field, exclude_field in _GEO_SIMPLE_PAIRS:
        include_vals = getattr(targeting, include_field, None)
        exclude_vals = getattr(targeting, exclude_field, None)
        if not include_vals or not exclude_vals:
            continue
        inc_set = _extract_simple_values(include_vals)
        exc_set = _extract_simple_values(exclude_vals)
        overlap = sorted(inc_set & exc_set)
        if overlap:
            violations.append(
                f"{include_field}/{exclude_field} conflict: "
                f"values {', '.join(overlap)} appear in both inclusion and exclusion"
            )

    # Structured fields: metros, postal_areas (system + values)
    for include_field, exclude_field in _GEO_STRUCTURED_PAIRS:
        include_vals = getattr(targeting, include_field, None)
        exclude_vals = getattr(targeting, exclude_field, None)
        if not include_vals or not exclude_vals:
            continue
        inc_by_system = _extract_system_values(include_vals)
        exc_by_system = _extract_system_values(exclude_vals)
        for system in sorted(set(inc_by_system) & set(exc_by_system)):
            overlap = sorted(inc_by_system[system] & exc_by_system[system])
            if overlap:
                violations.append(
                    f"{include_field}/{exclude_field} conflict in system '{system}': "
                    f"values {', '.join(overlap)} appear in both inclusion and exclusion"
                )

    return violations
