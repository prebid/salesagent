"""BDD binding for the locally-added MCP tool-discovery feature.

Grades that ``tools/list`` publishes each tool's request DTO JSON Schema as the registry
declares it, ``$defs`` and ``$ref`` intact.

Local rather than a BR-* storyboard on purpose: AdCP grades which top-level fields a tool
declares, not the schema's form; MCP 2025-11-25 fixes the form (JSON Schema 2020-12).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-mcp-tool-discovery.feature")
