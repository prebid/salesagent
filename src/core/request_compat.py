"""AdCP backward-compatibility request normalization.

Translates deprecated field names to current equivalents before validation.
Mirrors the JS adcp-client's normalizeRequestParams() logic.
Shared by all transports (MCP, A2A, REST).

Stub: implementation pending (salesagent-jnry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizationResult:
    """Result of normalizing request parameters."""

    params: dict[str, Any]
    inferred_version: str = "3.0"
    translations_applied: list[str] = field(default_factory=list)


def normalize_request_params(
    tool_name: str,
    params: dict[str, Any],
) -> NormalizationResult:
    """Translate deprecated fields to current equivalents.

    Stub — returns params unchanged. Implementation in salesagent-jnry.
    """
    return NormalizationResult(params=dict(params))
