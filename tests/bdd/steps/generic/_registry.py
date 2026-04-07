"""Shared registry helpers for BDD Given steps.

The real creative agent catalog is always available via the running
creative agent container. Format injection via set_registry_formats() has
been removed — tests rely on the real catalog instead of pre-warmed caches.
"""

from __future__ import annotations

from typing import Any


def sync_registry(ctx: dict[str, Any]) -> None:
    """No-op: registry formats come from the real creative agent catalog.

    Formerly pushed ctx['registry_formats'] into the harness via
    set_registry_formats(). That method has been removed — the real
    catalog is always available and cache injection is no longer needed.
    """
