"""L0-02 guard: the set of MCP tools (name + signature) is frozen.

The MCP protocol exposes every tool's ``name``, ``parameters`` JSON
schema, and ``return_type`` to clients. A silent rename, a missing
required field, or an added optional parameter are all breaking
changes from a client's perspective — this test captures them all.

At L0 the fixture is a snapshot of the current tool set. Future intended
changes update the fixture in the same commit.
"""

from __future__ import annotations

import asyncio
import json
import typing
from pathlib import Path
from typing import Any

from tests.migration._fixtures import canonical_json_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "mcp-tool-inventory.json"


def _canonical_return_type(rt: Any) -> str:
    """Return a deterministic string representation of an MCP tool return type.

    Some tools declare explicit pydantic models; others leave the annotation
    as ``typing.Any`` or ``inspect._empty`` (no annotation). We normalize
    both to ``"<unannotated>"`` so the fixture is stable across cosmetic
    changes.
    """
    import inspect  # local import keeps the module's public surface small

    if rt is inspect.Signature.empty or rt is inspect.Parameter.empty:
        return "<unannotated>"
    if rt is Any or rt is typing.Any:
        return "<unannotated>"
    module = getattr(rt, "__module__", "")
    qualname = getattr(rt, "__qualname__", None) or getattr(rt, "__name__", None) or repr(rt)
    if module in {"builtins", ""}:
        return str(qualname)
    return f"{module}.{qualname}"


def _build_current_inventory() -> dict[str, dict[str, Any]]:
    """Return the current MCP tool inventory as a canonical dict."""
    from src.core.main import mcp

    async def _list() -> list[Any]:
        return list(await mcp.list_tools())

    tools = asyncio.run(_list())
    inventory: dict[str, dict[str, Any]] = {}
    for tool in tools:
        params = tool.parameters or {}
        properties = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        optional = sorted(k for k in properties.keys() if k not in required)
        inventory[tool.name] = {
            "name": tool.name,
            "required_params": sorted(required),
            "optional_params": optional,
            "return_type": _canonical_return_type(tool.return_type),
        }
    return inventory


def _read_expected_inventory() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestMcpToolInventoryFrozen:
    """Exposed MCP tool set (name, required/optional params, return type) is frozen."""

    def test_inventory_matches_baseline(self):
        observed = _build_current_inventory()
        expected = _read_expected_inventory()

        observed_bytes = canonical_json_bytes(observed)
        expected_bytes = canonical_json_bytes(expected)

        assert observed_bytes == expected_bytes, (
            "MCP tool inventory changed.\n"
            f"  expected tools: {sorted(expected.keys())}\n"
            f"  observed tools: {sorted(observed.keys())}\n"
            f"If this change is intentional, regenerate {FIXTURE.name} and commit with a note "
            "explaining which tools/signatures were added, removed, or changed."
        )

    def test_planted_drift_is_detected(self):
        """Meta-test: mutating the in-memory inventory must fail the comparison."""
        observed = _build_current_inventory()
        expected = _read_expected_inventory()

        # 1. Add a fake tool.
        mutated_add = {
            **observed,
            "bogus_tool": {
                "name": "bogus_tool",
                "required_params": [],
                "optional_params": [],
                "return_type": "<unannotated>",
            },
        }
        assert canonical_json_bytes(mutated_add) != canonical_json_bytes(
            expected
        ), "adding a bogus tool did not change the canonical bytes — meta-test setup is wrong"

        # 2. Remove a real tool.
        a_tool = next(iter(expected.keys()))
        mutated_remove = {k: v for k, v in observed.items() if k != a_tool}
        assert canonical_json_bytes(mutated_remove) != canonical_json_bytes(
            expected
        ), "removing an existing tool did not change the canonical bytes — meta-test setup is wrong"
