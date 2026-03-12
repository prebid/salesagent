"""Shared registry helpers for BDD Given steps.

Pushes ctx["registry_formats"] (list of real Format objects) into the
CreativeFormatsEnv harness.
"""

from __future__ import annotations

from typing import Any


def sync_registry(ctx: dict[str, Any]) -> None:
    """Push ctx['registry_formats'] Format objects into the harness.

    Called after any step that modifies ctx["registry_formats"].
    """
    ctx["env"].set_registry_formats(ctx.get("registry_formats", []))
