"""AdCP backward-compatibility request normalization.

Translates deprecated field names to current equivalents before validation.
Mirrors the JS adcp-client's normalizeRequestParams() logic.
Shared by all transports (MCP, A2A, REST).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Fields whose presence signals a v2.5 caller.
V25_SIGNALS: frozenset[str] = frozenset({"brand_manifest", "promoted_offerings", "campaign_ref"})

# Tools where brand_manifest → brand translation applies.
_BRAND_TOOLS: frozenset[str] = frozenset({"get_products", "create_media_buy"})


@dataclass
class NormalizationResult:
    """Result of normalizing request parameters."""

    params: dict[str, Any]
    inferred_version: str = "3.0"
    translations_applied: list[str] = field(default_factory=list)


def _translate_brand_manifest(value: Any) -> dict[str, str] | None:
    """Convert brand_manifest (URL string or {url: str}) to BrandReference {domain}.

    Returns None if the value cannot be parsed into a valid domain.
    """
    if value is None:
        return None

    url: str | None = None
    if isinstance(value, str):
        url = value
    elif isinstance(value, dict):
        url = value.get("url")

    if not url or not isinstance(url, str):
        return None

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname:
            return {"domain": hostname}
    except Exception:  # noqa: BLE001
        pass
    return None


def _normalize_packages(packages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize deprecated fields inside package dicts.

    Handles:
    - optimization_goal (scalar) → optimization_goals (array)
    - catalog (scalar) → catalogs (array)
    """
    translations: list[str] = []
    result = []
    for pkg in packages:
        pkg = dict(pkg)

        if "optimization_goal" in pkg:
            if "optimization_goals" not in pkg or not pkg["optimization_goals"]:
                pkg["optimization_goals"] = [pkg["optimization_goal"]]
                translations.append("optimization_goal → optimization_goals")
            del pkg["optimization_goal"]

        if "catalog" in pkg:
            if "catalogs" not in pkg or not pkg["catalogs"]:
                pkg["catalogs"] = [pkg["catalog"]]
                translations.append("catalog → catalogs")
            del pkg["catalog"]

        result.append(pkg)
    return result, translations


def normalize_request_params(
    tool_name: str,
    params: dict[str, Any],
) -> NormalizationResult:
    """Translate deprecated fields to current equivalents.

    Args:
        tool_name: The MCP/A2A tool name (e.g., "get_products", "create_media_buy").
        params: Raw request parameters dict.

    Returns:
        NormalizationResult with normalized params, inferred version, and
        list of translations applied.
    """
    result = dict(params)
    translations: list[str] = []

    # --- Version inference ---
    inferred = "2.5" if V25_SIGNALS & result.keys() else "3.0"

    # --- Top-level translations (all tools) ---

    # account_id (string) → account: {account_id: str}
    if "account_id" in result:
        if "account" not in result:
            result["account"] = {"account_id": result["account_id"]}
            translations.append("account_id → account")
        del result["account_id"]

    # campaign_ref → buyer_campaign_ref
    if "campaign_ref" in result:
        if "buyer_campaign_ref" not in result:
            result["buyer_campaign_ref"] = result["campaign_ref"]
            translations.append("campaign_ref → buyer_campaign_ref")
        del result["campaign_ref"]

    # --- Tool-scoped translations ---

    # brand_manifest → brand (get_products, create_media_buy only)
    if "brand_manifest" in result:
        if tool_name in _BRAND_TOOLS and "brand" not in result:
            brand_ref = _translate_brand_manifest(result["brand_manifest"])
            if brand_ref is not None:
                result["brand"] = brand_ref
                translations.append("brand_manifest → brand")
        del result["brand_manifest"]

    # promoted_offerings → catalogs (get_products)
    if "promoted_offerings" in result:
        if "catalogs" not in result:
            result["catalogs"] = result["promoted_offerings"]
            translations.append("promoted_offerings → catalogs")
        del result["promoted_offerings"]

    # --- Package-level translations ---
    if "packages" in result and isinstance(result["packages"], list):
        result["packages"], pkg_translations = _normalize_packages(result["packages"])
        translations.extend(pkg_translations)

    if translations:
        logger.info(
            "Normalized %s request (v%s): %s",
            tool_name,
            inferred,
            ", ".join(translations),
        )

    return NormalizationResult(
        params=result,
        inferred_version=inferred,
        translations_applied=translations,
    )


def strip_unknown_params(
    params: dict[str, Any],
    known_params: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Remove fields not in known_params set.

    Args:
        params: Request parameters dict (already normalized).
        known_params: Set of parameter names the tool function accepts.
            Typically from tool.parameters["properties"].keys().

    Returns:
        Tuple of (cleaned dict with only known keys, sorted list of stripped key names).
    """
    unknown = params.keys() - known_params
    if not unknown:
        return params, []
    cleaned = {k: v for k, v in params.items() if k in known_params}
    return cleaned, sorted(unknown)
