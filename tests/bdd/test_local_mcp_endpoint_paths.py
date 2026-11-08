"""BDD binding for the MCP endpoint paths feature.

The paths are the seller's HTTP surface rather than a tool, so the scenarios read one response
from the env on every transport. See the feature for why the lifespan has to be running.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-mcp-endpoint-paths.feature")
