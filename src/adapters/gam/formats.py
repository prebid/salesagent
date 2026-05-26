"""GAM support matrix for AdCP canonical creative formats.

GAM has many serving and trafficking modes, but those are adapter details.
The public format IDs stay canonical reference-agent IDs; this module maps
those IDs to the GAM creative placeholders and creative classes we use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.standard_formats import get_standard_format


@dataclass(frozen=True)
class GAMFormatSupport:
    """How a canonical AdCP format can be trafficked in GAM."""

    format_id: str
    format_type: str
    creative_size_type: str
    creative_types: tuple[str, ...]
    requires_dimensions: bool = False
    default_size: tuple[int, int] | None = None
    requires_template: bool = False
    supports_fluid: bool = False
    notes: str = ""


GAM_FORMAT_SUPPORT: dict[str, GAMFormatSupport] = {
    "display_image": GAMFormatSupport(
        format_id="display_image",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("ImageRedirectCreative",),
        requires_dimensions=True,
        notes="Hosted image creative with canonical width/height parameters.",
    ),
    "display_html": GAMFormatSupport(
        format_id="display_html",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("CustomCreative",),
        requires_dimensions=True,
        notes="HTML/custom creative with canonical width/height parameters.",
    ),
    "display_js": GAMFormatSupport(
        format_id="display_js",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("ThirdPartyCreative",),
        requires_dimensions=True,
        notes="Third-party JavaScript tag with canonical width/height parameters.",
    ),
    "product_carousel_display": GAMFormatSupport(
        format_id="product_carousel_display",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("TemplateCreative", "CustomCreative"),
        requires_template=True,
        notes="Canonical product carousel mapped to a configured GAM template/custom creative.",
    ),
    "image_slideshow_5s_each": GAMFormatSupport(
        format_id="image_slideshow_5s_each",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("TemplateCreative", "CustomCreative"),
        requires_template=True,
        notes="Canonical slideshow mapped to a configured GAM template/custom creative.",
    ),
    "mobile_story_vertical": GAMFormatSupport(
        format_id="mobile_story_vertical",
        format_type="display",
        creative_size_type="PIXEL",
        creative_types=("TemplateCreative", "CustomCreative"),
        requires_template=True,
        notes="Canonical vertical story mapped to a configured GAM template/custom creative.",
    ),
    "video_standard": GAMFormatSupport(
        format_id="video_standard",
        format_type="video",
        creative_size_type="PIXEL",
        creative_types=("VideoRedirectCreative",),
        default_size=(640, 480),
        notes="Hosted video creative; width/height parameters override the default video player size.",
    ),
    "video_vast": GAMFormatSupport(
        format_id="video_vast",
        format_type="video",
        creative_size_type="PIXEL",
        creative_types=("VASTRedirect",),
        default_size=(640, 480),
        notes="VAST tag configured at the GAM line item/creative association layer.",
    ),
    "video_playlist_6s_bumpers": GAMFormatSupport(
        format_id="video_playlist_6s_bumpers",
        format_type="video",
        creative_size_type="PIXEL",
        creative_types=("VASTRedirect", "VideoRedirectCreative"),
        default_size=(640, 480),
        requires_template=True,
        notes="Canonical playlist mapped to a configured GAM video template or VAST workflow.",
    ),
    "native_standard": GAMFormatSupport(
        format_id="native_standard",
        format_type="native",
        creative_size_type="NATIVE",
        creative_types=("TemplateCreative",),
        default_size=(1, 1),
        requires_template=True,
        supports_fluid=True,
        notes="GAM native uses a native creative template; fluid/native placeholders are 1x1.",
    ),
}

GAM_CANONICAL_FORMAT_IDS = tuple(GAM_FORMAT_SUPPORT)


def gam_format_support(format_id: str) -> GAMFormatSupport | None:
    """Return the GAM support entry for a canonical format ID."""
    return GAM_FORMAT_SUPPORT.get(format_id)


def gam_format_type(format_id: str) -> str:
    """Return the broad media type GAM uses for a canonical format ID."""
    support = gam_format_support(format_id)
    if support:
        return support.format_type
    if format_id.startswith("audio_"):
        return "audio"
    if "video" in format_id:
        return "video"
    if "native" in format_id:
        return "native"
    return "display"


def gam_supported_format_models() -> list:
    """Return canonical Format models annotated with internal GAM metadata."""
    formats = []
    for format_id, support in GAM_FORMAT_SUPPORT.items():
        fmt = get_standard_format(format_id)
        if fmt is None:
            continue
        formats.append(
            fmt.model_copy(
                update={
                    "platform_config": {
                        **(fmt.platform_config or {}),
                        "gam": {
                            "creative_size_type": support.creative_size_type,
                            "creative_types": list(support.creative_types),
                            "requires_dimensions": support.requires_dimensions,
                            "requires_template": support.requires_template,
                            "supports_fluid": support.supports_fluid,
                            "notes": support.notes,
                        },
                    }
                }
            )
        )
    return formats


def canonical_format_dict(format_id: str, **params: Any) -> dict[str, Any]:
    """Build a structured canonical FormatId dict for GAM product config."""
    result: dict[str, Any] = {"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": format_id}
    result.update({key: value for key, value in params.items() if value is not None})
    return result


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _format_id_from_item(format_item: Any) -> str:
    if isinstance(format_item, dict):
        return str(format_item.get("id") or format_item.get("format_id") or "")
    return str(getattr(format_item, "id", format_item))


def _dimensions_from_item(format_item: Any) -> tuple[int, int] | None:
    width = _field(format_item, "width")
    height = _field(format_item, "height")
    if width and height:
        return int(width), int(height)

    format_id = _format_id_from_item(format_item)
    match = re.search(r"(\d+)x(\d+)", format_id)
    if match:
        return int(match.group(1)), int(match.group(2))

    return None


def _template_id_for_format(format_id: str, impl_config: dict[str, Any] | None, gam_cfg: dict[str, Any]) -> Any:
    placeholder_cfg = gam_cfg.get("creative_placeholder", {}) if isinstance(gam_cfg, dict) else {}
    if isinstance(placeholder_cfg, dict) and placeholder_cfg.get("creative_template_id"):
        return placeholder_cfg["creative_template_id"]
    if isinstance(gam_cfg, dict) and gam_cfg.get("creative_template_id"):
        return gam_cfg["creative_template_id"]
    if not impl_config:
        return None

    template_ids = impl_config.get("creative_template_ids")
    if isinstance(template_ids, dict) and template_ids.get(format_id):
        return template_ids[format_id]

    native_template_ids = impl_config.get("native_template_ids")
    if isinstance(native_template_ids, dict) and native_template_ids.get(format_id):
        return native_template_ids[format_id]

    if format_id == "native_standard":
        return impl_config.get("native_template_id") or impl_config.get("creative_template_id")

    return impl_config.get(f"{format_id}_creative_template_id")


def build_gam_creative_placeholder(
    format_item: Any,
    *,
    format_obj: Any | None = None,
    impl_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a GAM CreativePlaceholder for a canonical AdCP format reference."""
    format_id = _format_id_from_item(format_item)
    support = gam_format_support(format_id)
    if support is None:
        if format_id.startswith("audio_"):
            raise ValueError(f"Audio format '{format_id}' is not supported by GAM as a standalone line item format.")
        raise ValueError(f"Format '{format_id}' is not mapped to a GAM-supported canonical format.")

    platform_cfg = getattr(format_obj, "platform_config", None) or {}
    gam_cfg = platform_cfg.get("gam", {}) if isinstance(platform_cfg, dict) else {}
    placeholder_cfg = gam_cfg.get("creative_placeholder", {}) if isinstance(gam_cfg, dict) else {}

    placeholder: dict[str, Any] = {"expectedCreativeCount": 1}

    template_id = _template_id_for_format(format_id, impl_config, gam_cfg)
    if template_id:
        placeholder["creativeTemplateId"] = int(template_id)

    dimensions = _dimensions_from_item(format_item)
    if not dimensions and isinstance(placeholder_cfg, dict):
        width = placeholder_cfg.get("width")
        height = placeholder_cfg.get("height")
        if width and height:
            dimensions = (int(width), int(height))

    if support.creative_size_type == "NATIVE":
        dimensions = dimensions or support.default_size or (1, 1)
        placeholder["creativeSizeType"] = "NATIVE"
        placeholder["size"] = {"width": dimensions[0], "height": dimensions[1], "isAspectRatio": False}
        return placeholder

    if support.requires_template and template_id and not dimensions:
        dimensions = support.default_size or (1, 1)

    if not dimensions and support.default_size:
        dimensions = support.default_size

    if not dimensions:
        raise ValueError(
            f"Format '{format_id}' needs width/height for GAM. "
            "Use a parameterized canonical FormatId with width and height, "
            "or configure platform_config.gam.creative_placeholder."
        )

    placeholder["size"] = {"width": dimensions[0], "height": dimensions[1]}
    placeholder["creativeSizeType"] = (
        placeholder_cfg.get("creative_size_type", support.creative_size_type)
        if isinstance(placeholder_cfg, dict)
        else support.creative_size_type
    )
    return placeholder


__all__ = [
    "GAM_CANONICAL_FORMAT_IDS",
    "GAM_FORMAT_SUPPORT",
    "GAMFormatSupport",
    "build_gam_creative_placeholder",
    "canonical_format_dict",
    "gam_format_support",
    "gam_format_type",
    "gam_supported_format_models",
]
