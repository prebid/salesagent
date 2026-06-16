"""Backward-compatible re-exports — import from ``tests.unit._architecture_helpers``."""

from __future__ import annotations

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    SCAN_DIRS,
    collect_error_aliases,
    iter_module_trees,
    rel,
    safe_parse,
    walk_with_enclosing_function,
)

__all__ = [
    "REPO_ROOT",
    "SCAN_DIRS",
    "collect_error_aliases",
    "iter_module_trees",
    "rel",
    "safe_parse",
    "walk_with_enclosing_function",
]
