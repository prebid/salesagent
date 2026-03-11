"""Shared registry helpers for BDD Given steps.

Both given_entities.py and given_config.py need to push ctx["registry_formats"]
dicts into the CreativeFormatsEnv harness as real Format objects. This module
provides the single implementation.
"""

from __future__ import annotations

from typing import Any


def sync_registry(ctx: dict[str, Any]) -> None:
    """Push ctx['registry_formats'] dicts into the harness as real Format objects.

    Called after any step that modifies ctx["registry_formats"].
    """
    env = ctx["env"]

    from tests.bdd.steps.domain.uc005_creative_formats import dicts_to_formats

    raw = ctx.get("registry_formats", [])
    formats = dicts_to_formats(raw)
    env.set_registry_formats(formats)
