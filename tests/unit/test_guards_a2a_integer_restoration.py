"""Structural guard for the A2A wire integer-type fix.

Pins two invariants that keep the fix (see
``restore_a2a_integer_types`` in ``src/a2a_server/adcp_a2a_server.py``) from
silently regressing:

1. ``_dict_to_value`` (adcp_a2a_server.py) is the ONLY site in ``src/`` that
   constructs a ``google.protobuf.Struct``/``Value`` for A2A wire data. A
   second, parallel construction site would bypass the integer-restoration
   fix for whatever it builds.
2. Every real ``/a2a`` JSON-RPC route registered on the FastAPI app is
   wrapped with the integer-restoring ASGI wrapper -- a future refactor of
   ``src/app.py``'s route wiring could easily drop the wrapper and silently
   reintroduce the float-widening bug on the real wire.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _struct_value_construction_sites() -> list[str]:
    """Every ``src/`` call to struct_pb2.Value(...) or struct_pb2.Struct(...),
    as ``path:lineno``, found via AST (not regex, so a reformatted call site
    can't slip past)."""
    sites: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr_name = func.attr if isinstance(func, ast.Attribute) else None
            if attr_name in {"Value", "Struct"} and isinstance(func, ast.Attribute):
                value_source = func.value
                if isinstance(value_source, ast.Name) and value_source.id == "struct_pb2":
                    sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return sites


class TestOnlyOneStructValueConstructionSite:
    def test_dict_to_value_is_the_only_struct_value_construction_site(self):
        sites = _struct_value_construction_sites()
        allowed_file = "src/a2a_server/adcp_a2a_server.py"
        stray = [s for s in sites if not s.startswith(allowed_file)]
        assert not stray, (
            "found a struct_pb2.Value/Struct() construction site outside "
            f"{allowed_file}: {stray}. A2A wire data must be built through "
            "_dict_to_value so integer-typed fields stay covered by "
            "restore_a2a_integer_types -- a parallel "
            "construction site bypasses that fix."
        )
        assert sites, "expected at least the known _dict_to_value construction sites -- scan may be broken"

    def test_scan_would_catch_a_stray_construction_site(self, tmp_path, monkeypatch):
        """Meta-test: prove the AST scan actually detects a stray site, not just
        that today's tree happens to be clean."""
        fake_src = REPO_ROOT / "src" / "_tmp_guard_meta_test_stray.py"
        fake_src.write_text("from google.protobuf import struct_pb2\nv = struct_pb2.Value()\n")
        try:
            sites = _struct_value_construction_sites()
            assert any("_tmp_guard_meta_test_stray.py" in s for s in sites), (
                "the AST scan failed to detect a deliberately-planted stray "
                "struct_pb2.Value() construction site -- the guard is vacuous"
            )
        finally:
            fake_src.unlink()


class TestA2ARoutesWrapWithIntegerRestoration:
    def test_all_a2a_rpc_routes_are_integer_restoration_wrapped(self):
        from src.app import _a2a_rpc_routes

        assert _a2a_rpc_routes, "expected at least one /a2a JSON-RPC route"
        unwrapped = [
            route.path
            for route in _a2a_rpc_routes
            if not getattr(route.endpoint, "__a2a_integer_restoration_wrapped__", False)
        ]
        assert not unwrapped, (
            f"these /a2a routes are missing the integer-restoration wrapper: {unwrapped} -- "
            "the real HTTP wire would silently widen integer fields to floats again"
        )

    def test_unwrapped_route_would_be_caught(self):
        """Meta-test: an endpoint without the marker attribute must fail the check
        above's condition -- proves the guard isn't vacuously true."""

        async def _unmarked_endpoint(request):
            return None

        assert not getattr(_unmarked_endpoint, "__a2a_integer_restoration_wrapped__", False)
