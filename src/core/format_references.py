"""Helpers for parsing and comparing AdCP FormatId references.

Form posts and legacy admin JavaScript can submit a few wire-compatible
shapes. This module keeps that compatibility at the boundary, then validates
through the typed AdCP/Pydantic FormatId model before callers store or compare
format references.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adcp.types import FormatId as LibraryFormatId
from pydantic import ValidationError

from src.core.canonical_formats import canonicalize_creative_agent_url, normalize_creative_agent_url
from src.core.schemas import FormatId

_FORMAT_ID_PARAM_KEYS = ("width", "height", "min_width", "max_width", "min_height", "max_height", "duration_ms")


def _format_ref_payload(format_ref: Any) -> Any:
    """Return a FormatId-like payload from flat or nested submitted data."""
    if not isinstance(format_ref, Mapping):
        return format_ref

    raw = dict(format_ref)
    nested = raw.get("format_id")

    if isinstance(nested, LibraryFormatId):
        payload = nested.model_dump(mode="python", exclude_none=True)
    elif isinstance(nested, Mapping):
        payload = dict(nested)
    else:
        payload = dict(raw)
        if nested is not None and "id" not in payload:
            payload["id"] = nested
        payload.pop("format_id", None)
        return payload

    for key in ("agent_url", "id", *_FORMAT_ID_PARAM_KEYS):
        if raw.get(key) is not None and payload.get(key) is None:
            payload[key] = raw[key]
    return payload


def format_id_from_ref(format_ref: Any) -> FormatId:
    """Parse a FormatId reference using the typed AdCP schema model.

    Accepts:
    - FormatId / adcp.types.FormatId objects
    - flat dicts: {"agent_url": "...", "id": "...", "width": 300}
    - legacy flat dicts: {"agent_url": "...", "format_id": "..."}
    - nested dicts: {"format_id": {"agent_url": "...", "id": "..."}, "width": 300}

    Raises:
        ValueError: if the value cannot be parsed as a typed FormatId.
    """
    payload = _format_ref_payload(format_ref)
    try:
        if isinstance(payload, FormatId):
            return payload
        return FormatId.model_validate(payload, from_attributes=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"Invalid FormatId reference: {format_ref!r}") from exc


def try_format_id_from_ref(format_ref: Any) -> FormatId | None:
    """Return a typed FormatId, or None for malformed boundary data."""
    try:
        return format_id_from_ref(format_ref)
    except ValueError:
        return None


def format_id_identity(format_id: FormatId | LibraryFormatId) -> tuple[str, str]:
    """Return the namespace-aware identity used for catalog validation."""
    return (normalize_creative_agent_url(format_id.agent_url), str(format_id.id))


def format_id_storage_dict(format_id: FormatId | LibraryFormatId) -> dict[str, Any]:
    """Serialize a typed FormatId for JSON storage with stable reference-agent URLs."""
    typed_format_id = (
        format_id if isinstance(format_id, FormatId) else FormatId.model_validate(format_id, from_attributes=True)
    )
    data = typed_format_id.model_dump(mode="json", exclude_none=True)
    data["agent_url"] = canonicalize_creative_agent_url(data["agent_url"])
    return data
