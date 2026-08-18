"""Guard: ``ToolResult(structured_content=...)`` must only be built by the shared helper.

Regression guard for GH #1710: FastMCP's ``ToolResult.__init__``
(``fastmcp/tools/base.py``) always serializes non-dict ``structured_content`` via
``pydantic_core.to_jsonable_python()`` when the value isn't already a plain dict. That
serialization path BYPASSES:

- our ``model_dump()`` overrides (Pattern #4 nested serialization -- e.g.
  ``SyncCreativesResponse.model_dump()``'s per-creative child dump), and
- ``adcp.types.base.AdCPBaseModel.model_dump()``'s ``exclude_none=True`` default,
  which A2A/REST get for free via ``response.model_dump(mode="json")``.

The result: spec-optional fields left unset (e.g. per-creative ``status``,
``adcp_version``) silently serialize as invalid wire ``null`` on MCP ONLY --
byte-different from A2A/REST for the identical response object, and invalid
against the pinned response schema (typed fields don't accept ``null``).

Fix: ``src/core/tools/_mcp.py``'s ``mcp_result()`` helper owns the one
``ToolResult(structured_content=response.model_dump(mode="json"))`` call in the
codebase; every wrapper calls it instead of constructing ``ToolResult`` directly.

Rule (boundary check, not a shape scan): any ``ToolResult(...)`` call anywhere in
``src/`` OTHER than inside ``src/core/tools/_mcp.py`` that passes a
``structured_content=`` keyword argument is a violation -- the abstraction must be
used, not merely shaped correctly. A call that keeps ``model_dump()`` but drops
``mode="json"``, or a per-model override that force-includes a null, is caught
because it can no longer exist outside the helper at all.

Ships with ZERO violations; no allowlist (repo hard rule: allowlists never grow).
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT, iter_module_trees

SRC_ROOT = REPO_ROOT / "src"


def find_structured_content_calls_outside_helper(src_files: dict[str, ast.AST]) -> list[str]:
    """``file:line`` for every ``ToolResult(structured_content=...)`` call outside the helper module."""
    offenders: list[str] = []
    for path, tree in sorted(src_files.items()):
        if path == "src/core/tools/_mcp.py":
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name != "ToolResult":
                continue
            for kw in node.keywords:
                if kw.arg == "structured_content":
                    offenders.append(f"{path}:{node.lineno}")
    return offenders


def test_no_toolresult_structured_content_outside_helper():
    src_files = {path: tree for tree, path in iter_module_trees([SRC_ROOT])}
    violations = find_structured_content_calls_outside_helper(src_files)
    assert not violations, (
        "ToolResult(structured_content=...) must only be constructed by "
        "src/core/tools/_mcp.py's mcp_result() helper (GH #1710) -- call "
        "mcp_result(response) instead of building ToolResult directly. Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(src_snippets: dict[str, str]) -> list[str]:
    return find_structured_content_calls_outside_helper({k: ast.parse(v) for k, v in src_snippets.items()})


class TestGuardDetector:
    def test_positive_raw_model(self):
        assert _detect({"src/t.py": "ToolResult(content=str(response), structured_content=response)"})

    def test_positive_model_dump_call(self):
        # Even the "correct shape" is a violation outside the helper -- the
        # abstraction itself, not just its output shape, must be used.
        assert _detect(
            {"src/t.py": 'ToolResult(content=str(response), structured_content=response.model_dump(mode="json"))'}
        )

    def test_negative_helper_module_itself(self):
        assert not _detect(
            {
                "src/core/tools/_mcp.py": (
                    'ToolResult(content=str(response), structured_content=response.model_dump(mode="json"))'
                )
            }
        )

    def test_negative_no_structured_content_kwarg(self):
        assert not _detect({"src/t.py": "ToolResult(content=str(response))"})

    def test_negative_unrelated_call(self):
        # A differently-named call is not ToolResult and must not be flagged.
        assert not _detect({"src/t.py": "SomethingElse(content=str(response), structured_content=response)"})
