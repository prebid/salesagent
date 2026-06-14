"""The vendor ext namespace for prebid-specific extension fields.

AdCP ``ext`` is an open ExtensionObject; vendor fields must be namespaced under a
vendor/platform key. This module is the single source of the ``prebid`` key and
its read/write helpers, so the namespace literal is not hand-copied across the
capability declaration, the advisory builder, and the response reader (where it
had already started to drift into three coupled literals).
"""

from __future__ import annotations

from typing import Any

PREBID_EXT_NAMESPACE = "prebid"


def prebid_ext(**fields: Any) -> dict[str, dict[str, Any]]:
    """Wrap vendor ``fields`` under the prebid ext namespace."""
    return {PREBID_EXT_NAMESPACE: fields}


def prebid_vendor(ext: Any) -> dict[str, Any] | None:
    """Read the prebid vendor dict from an ``ext`` that may be a model or a JSON dict.

    ``ext`` is an ExtensionObject model on the construction path but a plain dict
    after a JSON round-trip; both forms resolve to the vendor dict (or ``None``).
    """
    if ext is None:
        return None
    vendor = ext.get(PREBID_EXT_NAMESPACE) if isinstance(ext, dict) else getattr(ext, PREBID_EXT_NAMESPACE, None)
    return vendor if isinstance(vendor, dict) else None
