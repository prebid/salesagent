"""STUB (L0-05 Red) — empty sentinel so tests fail semantically, not with ImportError.

Green commit replaces this module with the real TemplatesDep / BaseCtxDep /
tojson_filter per .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md
§D8-native.3.
"""

from __future__ import annotations

from typing import Any

# Sentinels — Green re-exports real TemplatesDep / BaseCtxDep / helpers.
TemplatesDep: Any = None
BaseCtxDep: Any = None


def get_templates(request: Any) -> Any:
    return None


def get_base_context(request: Any, messages: Any = None) -> dict[str, Any]:
    return {}


def tojson_filter(value: Any, indent: int | None = None) -> str:
    return ""
