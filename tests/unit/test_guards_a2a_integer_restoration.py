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

RFC 9421 MERGE NOTE. Invariant 1 was deleted on this branch 2026-08-31 and is restored
here, because all three stated grounds for deleting it are answered rather than argued:

- "its meta-test wrote a real module into ``src/`` while the sibling test scanned
  ``src/``, and under xdist those race" -- the incoming branch had already fixed exactly
  that (e6f79e71): the specimen is planted in ``tmp_path``, and ``_struct_value_
  construction_sites`` takes the root as a parameter so the meta-test can point the scan
  at the sandbox. Nothing is written into the source tree.
- "its allowlist was the FILE, so a second construction site inside
  ``adcp_a2a_server.py`` was exempt from the check meant to catch it" -- the allowlist is
  now the two ENCLOSING FUNCTIONS that build wire data (``_dict_to_value`` for a
  ``Part.data``, ``_dict_to_struct`` for a ``Task.metadata``), so a third site anywhere
  -- that file included -- is a violation.
- "it graded a code LOCATION as a stand-in for the behaviour" -- the behaviour it stands
  in for is not graded anywhere else. ``test_a2a_route_integer_restoration.py`` grades
  the ASGI wrapper at the HTTP boundary, and the class below grades that every route
  carries it; neither can see wire data built through a SECOND ``struct_pb2`` call that
  never passes through ``restore_a2a_integer_types``. Deleting this left that ungraded.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The construction sites the integer-restoration fix actually covers: ``_dict_to_value``
#: builds a ``Part.data`` payload and ``_dict_to_struct`` a ``Task.metadata`` one, and both
#: answers leave through ``restore_a2a_integer_types``. Named by ENCLOSING FUNCTION rather
#: than by file, so a THIRD site added elsewhere in ``adcp_a2a_server.py`` -- the likeliest
#: place for one to appear -- is a violation instead of being exempt, which is the hole the
#: file-wide allowlist left.
_ALLOWED_FILE = "src/a2a_server/adcp_a2a_server.py"
_ALLOWED_FUNCS = ("_dict_to_value", "_dict_to_struct")


def _struct_value_construction_sites(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every ``src/`` call to ``struct_pb2.Value(...)``/``Struct(...)``, as
    ``path:lineno:enclosing_function``, found via AST so a reformatted call site cannot
    slip past a regex.

    *repo_root* is a parameter ONLY so the meta-test can point the scan at a sandbox. It
    must not become a way to narrow the real scan.
    """
    sites: list[str] = []
    for path in sorted((repo_root / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        enclosing: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    enclosing[line] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            src_name = node.func.value
            if node.func.attr in {"Value", "Struct"} and isinstance(src_name, ast.Name) and src_name.id == "struct_pb2":
                where = enclosing.get(node.lineno, "<module>")
                sites.append(f"{path.relative_to(repo_root)}:{node.lineno}:{where}")
    return sites


class TestOnlyOneStructValueConstructionSite:
    def test_dict_to_value_is_the_only_struct_value_construction_site(self):
        sites = _struct_value_construction_sites()
        allowed = {f"{_ALLOWED_FILE}::{f}" for f in _ALLOWED_FUNCS}
        stray = [
            s
            for s in sites
            if f"{_ALLOWED_FILE}::{s.rsplit(':', 1)[1]}" not in allowed or not s.startswith(_ALLOWED_FILE)
        ]
        assert not stray, (
            f"found a struct_pb2.Value/Struct() construction site outside {sorted(allowed)}: "
            f"{stray}. A2A wire data must be built through _dict_to_value so integer-typed fields stay "
            "covered by restore_a2a_integer_types -- a parallel construction site bypasses that fix."
        )
        assert sites, "expected at least the known _dict_to_value construction sites -- scan may be broken"

    def test_scan_would_catch_a_stray_construction_site(self, tmp_path):
        """Meta-test: prove the AST scan detects a stray site, not just that today's tree
        happens to be clean.

        PLANTED IN A SANDBOX, never in the real ``src/``: the unit suite runs under xdist,
        and a specimen written into ``src/`` is found by whichever sibling worker happens
        to be running the real scan at the time -- and by every OTHER ``src/``-scanning
        guard in this repo, of which there are many.
        """
        planted = tmp_path / "src"
        planted.mkdir()
        (planted / "stray.py").write_text("from google.protobuf import struct_pb2\nv = struct_pb2.Value()\n")

        sites = _struct_value_construction_sites(tmp_path)

        assert any("stray.py" in s for s in sites), (
            "the AST scan failed to detect a deliberately-planted stray "
            "struct_pb2.Value() construction site -- the guard is vacuous"
        )


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


class TestIntegerSetMatchesTheSchemas:
    """A2A_WIRE_INTEGER_FIELDS is a hand-maintained set; pin it to the schemas.

    The set drives a NAME-KEYED coercion: any whole-numbered float arriving at one of
    these keys is turned back into an ``int``. That is safe only while every listed name
    really is integer-typed somewhere we control or the spec declares. Nothing checked
    that before -- an 18-name hand-list justified by a prose comment (prkv.5 Lane D D10).
    """

    @staticmethod
    def _pinned_property_types() -> dict[str, set[str]]:
        """property name -> every JSON `type` the pinned 3.1 schemas declare for it."""
        import importlib.util
        import json
        import pathlib

        root = pathlib.Path(importlib.util.find_spec("adcp").origin).parent / "_schemas/3.1"
        types: dict[str, set[str]] = {}

        def walk(node, key=None):
            if isinstance(node, dict):
                declared = node.get("type")
                if key and isinstance(declared, str):
                    types.setdefault(key, set()).add(declared)
                for k, v in node.items():
                    if k == "properties" and isinstance(v, dict):
                        for pk, pv in v.items():
                            walk(pv, pk)
                    else:
                        walk(v, key if k in ("items", "allOf", "anyOf", "oneOf", "$defs", "definitions") else None)
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)

        for f in root.rglob("*.json"):
            try:
                walk(json.loads(f.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return types

    @staticmethod
    def _app_integer_fields() -> set[str]:
        import inspect

        from pydantic import BaseModel

        import src.core.schemas as app

        out: set[str] = set()
        for name in dir(app):
            obj = getattr(app, name)
            if inspect.isclass(obj) and issubclass(obj, BaseModel):
                for field, info in obj.model_fields.items():
                    annotation = str(info.annotation)
                    if "int" in annotation and "float" not in annotation:
                        out.add(field)
        return out

    def test_every_listed_name_is_integer_typed_somewhere_authoritative(self) -> None:
        """Each name is integer per the PIN or per our own response models (SDK union app).

        Eleven of the eighteen are this server's own count fields (sync/assign counts,
        delivery totals) and correctly do not appear in the pinned schemas at all; they
        are graded against the app models instead. A name in NEITHER place is a name
        nobody can justify, and coercion would be firing on a guess.
        """
        from src.a2a_server.adcp_a2a_server import A2A_WIRE_INTEGER_FIELDS

        pinned = self._pinned_property_types()
        spec_ints = {n for n, ts in pinned.items() if "integer" in ts}
        app_ints = self._app_integer_fields()

        unjustified = sorted(A2A_WIRE_INTEGER_FIELDS - spec_ints - app_ints)
        assert not unjustified, (
            f"A2A_WIRE_INTEGER_FIELDS lists {unjustified}, which no pinned schema and no app "
            f"response model declares as an integer. Coercion fires on those names anyway, so "
            f"either the field is gone or the name is wrong."
        )

    def test_no_new_name_is_ambiguous_between_integer_and_number(self) -> None:
        """A listed name ALSO declared ``number`` in the pin is a coercion hazard.

        The coercion is keyed by NAME, not by the field's own declaration, so where the pin
        declares one property integer and a same-named property elsewhere ``number``, a
        whole-valued float of the SECOND kind gets silently retyped to int -- emitting an
        int where the spec says number. The two below are pre-existing and recorded, not
        endorsed; this only stops the set from growing more of them.
        """
        from src.a2a_server.adcp_a2a_server import A2A_WIRE_INTEGER_FIELDS

        pinned = self._pinned_property_types()
        number_names = {n for n, ts in pinned.items() if "number" in ts}
        known_ambiguous = frozenset({"impressions", "limit"})

        ambiguous = A2A_WIRE_INTEGER_FIELDS & number_names
        new = sorted(ambiguous - known_ambiguous)
        assert not new, (
            f"{new} are listed for int coercion but the pinned schemas also declare them "
            f"`number` somewhere. A whole-valued float at that key would be retyped to int "
            f"on the wire. Narrow the coercion (key it on the response type, not the bare "
            f"name) rather than widening this set."
        )
        stale = sorted(known_ambiguous - ambiguous)
        assert not stale, f"known_ambiguous lists {stale}, no longer ambiguous -- delete the entry"
