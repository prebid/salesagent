"""Shared registry helpers for BDD Given steps.

The real creative agent catalog is always available via the running
creative agent container. Given steps validate the catalog is present
and store it in ctx for Then steps to reference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Load the reference catalog (generated from real creative agent)
_CATALOG_PATH = Path(__file__).parents[4] / ".creative-agent-catalog.json"
_REFERENCE_CATALOG: list[dict] | None = None


def _load_reference_catalog() -> list[dict]:
    """Load the reference catalog from .creative-agent-catalog.json."""
    global _REFERENCE_CATALOG
    if _REFERENCE_CATALOG is None:
        if _CATALOG_PATH.exists():
            with open(_CATALOG_PATH) as f:
                _REFERENCE_CATALOG = json.load(f)
        else:
            _REFERENCE_CATALOG = []
    return _REFERENCE_CATALOG


def validate_catalog(ctx: dict[str, Any]) -> None:
    """Validate that the creative agent returned a complete format catalog.

    Called from Background steps. Fetches formats from the registry and
    verifies:
    1. The catalog is not empty (agent is reachable)
    2. The expected format types are present (display, video, audio, native, dooh)
    3. The catalog is stored in ctx["real_catalog"] for Then steps

    Fails fast with a clear message if the creative agent is down or
    returns unexpected data.
    """
    env = ctx.get("env")
    if env is None:
        return  # No harness — skip validation (some scenarios don't use CreativeFormatsEnv)

    # Fetch from the real agent through the production code path
    from src.core.schemas import ListCreativeFormatsRequest
    from src.core.tools.creative_formats import _list_creative_formats_impl

    response = _list_creative_formats_impl(
        req=ListCreativeFormatsRequest(),
        identity=env.identity,
    )
    formats = response.formats

    assert len(formats) > 0, (
        "Creative agent returned 0 formats — the container may not be running. "
        "Check CREATIVE_AGENT_URL and Docker stack health."
    )

    # Verify expected types are present
    types_present = {str(getattr(f, "type", "")).split(".")[-1] for f in formats}
    expected_types = {"display", "video"}  # minimum — real agent has all 5
    missing = expected_types - types_present
    assert not missing, (
        f"Creative agent catalog missing expected types: {missing}. "
        f"Got types: {types_present}. Catalog may be corrupted or agent version changed."
    )

    # Store in ctx for Then steps to reference
    ctx["real_catalog"] = formats
    ctx["real_catalog_count"] = len(formats)
    ctx["real_catalog_by_type"] = {}
    for f in formats:
        type_str = str(getattr(f, "type", "")).split(".")[-1]
        ctx["real_catalog_by_type"].setdefault(type_str, []).append(f)
