"""Shared registry helpers for BDD Given steps.

Pushes ctx["registry_formats"] (list of real Format objects) into the
CreativeFormatsEnv harness.  Also provides ``load_real_catalog()`` which
reads the 49-format catalog from ``.creative-agent-catalog.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.schemas import Format

# Module-scope cache: loaded once, reused by every scenario.
_REAL_CATALOG: list[Format] | None = None


def load_real_catalog() -> list[Format]:
    """Load the real 49-format creative-agent catalog (cached at module scope).

    Reads ``.creative-agent-catalog.json`` from the project root and converts
    each entry to a real ``Format`` object.
    """
    global _REAL_CATALOG  # noqa: PLW0603
    if _REAL_CATALOG is not None:
        return _REAL_CATALOG

    catalog_path = Path(__file__).resolve().parents[4] / ".creative-agent-catalog.json"
    data = json.loads(catalog_path.read_text())
    _REAL_CATALOG = [Format(**entry) for entry in data]
    return _REAL_CATALOG


def sync_registry(ctx: dict[str, Any]) -> None:
    """Push ctx['registry_formats'] Format objects into the harness.

    Called after any step that modifies ctx["registry_formats"].
    """
    ctx["env"].set_registry_formats(ctx.get("registry_formats", []))
