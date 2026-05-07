"""Backfill ``asset_type`` discriminator on CreativeAsset construction.

adcp 4.4 made ``asset_type`` a required discriminator on every entry inside
``CreativeAsset.assets``. The wire shape change is an ergonomic regression
for pre-v3 callers and the existing test fixtures that pass shape-implicit
dicts like ``{"banner": {"url": "..."}}`` or ``{"image": {"content": "..."}}``.

We already infer ``asset_type`` inside ``_sync_creatives_impl`` for inbound
sync payloads. Callers that construct ``CreativeAsset`` directly (most of
the test surface, plus a few internal flows) bypass that path and hit the
strict library validator unchanged.

Wrapping ``CreativeAsset.__init__`` at import time applies the same inference
on every construction so the whole codebase agrees on one normalisation
rule. The wrapper is a no-op when ``asset_type`` is already present.

Importing this module installs the patch as a side-effect; the import site
should be at a load-bearing point (``src.core.schemas.__init__``) so the
patch is in place before any test or production code constructs
``CreativeAsset``.
"""

from __future__ import annotations

from typing import Any

from adcp.types.generated_poc.core.creative_asset import CreativeAsset

# Inline copy of the inference rule (not imported from
# ``src.core.tools.creatives._sync``) to avoid a circular import — schemas
# are the deepest leaf of the import graph; tools/* depends on schemas, not
# the other way around.
_KNOWN_ASSET_TYPES = frozenset(
    {
        "image",
        "video",
        "audio",
        "vast",
        "text",
        "url",
        "html",
        "javascript",
        "webhook",
        "css",
        "daast",
        "markdown",
        "brief",
        "catalog",
    }
)


#: Asset types that require width+height in adcp 4.4. When a caller declares
#: one but omits dimensions, demote to ``url`` (which has no dim requirement)
#: rather than letting the strict library validator reject the payload.
_DIM_REQUIRED_TYPES = frozenset({"image", "video"})


def _demote_dim_required_without_dims(value: dict[str, Any]) -> dict[str, Any]:
    """Demote ``image``/``video`` asset to ``url`` when dimensions are missing.

    Mirrors the wire-level fixup in ``core.middleware.spec_defaults`` so any
    code path that constructs ``CreativeAsset`` directly (tests, internal
    flows) gets the same backward-compat treatment as the MCP/A2A boundary.
    """
    asset_type = value.get("asset_type")
    if asset_type not in _DIM_REQUIRED_TYPES:
        return value
    if "width" in value and "height" in value:
        return value
    if "url" not in value:
        return value
    return {**value, "asset_type": "url"}


def _backfill_brief_name(value: dict[str, Any]) -> dict[str, Any]:
    """adcp 4.4 made ``name`` required on brief assets. Pre-v3 buyers and
    existing test fixtures pass ``{"role": ..., "content": ...}`` without
    a name; default to ``"brief"`` so the discriminated-union validator
    can pick the brief branch without rejecting the payload.
    """
    if value.get("asset_type") != "brief":
        return value
    if value.get("name"):
        return value
    return {**value, "name": "brief"}


def infer_asset_types(assets: dict[str, Any]) -> dict[str, Any]:
    """Backfill ``asset_type`` discriminator on raw asset values.

    Mirrored in ``src.core.tools.creatives._sync._infer_asset_types``; kept
    as a public helper here so production sync code and the
    ``CreativeAsset.__init__`` patch share one inference rule.
    """
    inferred: dict[str, Any] = {}
    for key, value in assets.items():
        if not isinstance(value, dict):
            inferred[key] = value
            continue
        if "asset_type" in value:
            normalised = _demote_dim_required_without_dims(value)
            inferred[key] = _backfill_brief_name(normalised)
            continue
        if key in _KNOWN_ASSET_TYPES:
            with_type = {"asset_type": key, **value}
            normalised = _demote_dim_required_without_dims(with_type)
            inferred[key] = _backfill_brief_name(normalised)
            continue
        has_content = "content" in value
        has_url = "url" in value
        has_dims = "width" in value and "height" in value
        if has_content and not has_url:
            inferred[key] = {"asset_type": "text", **value}
        elif has_url and has_dims:
            # Image assets require url + width + height in adcp 4.4. When
            # the caller supplies all three we can confidently infer image.
            inferred[key] = {"asset_type": "image", **value}
        elif has_url:
            # ``url`` asset is the safe default when only a URL is supplied —
            # only ``url`` is required, no width/height needed.
            inferred[key] = {"asset_type": "url", **value}
        else:
            inferred[key] = value
    return inferred


def _apply_patch() -> None:
    if getattr(CreativeAsset, "_asset_type_compat_applied", False):
        return

    original_init = CreativeAsset.__init__

    def patched_init(self, **data: Any) -> None:
        assets = data.get("assets")
        if isinstance(assets, dict):
            data["assets"] = infer_asset_types(assets)
        original_init(self, **data)

    CreativeAsset.__init__ = patched_init  # type: ignore[method-assign,assignment]
    CreativeAsset._asset_type_compat_applied = True  # type: ignore[attr-defined]


_apply_patch()
