"""Guard: transport boundaries build strict request models only inside adcp_validation_boundary.

Regression guard for the #1417 family (#1417): REST routes in
``src/routes/`` constructed strict ``*Request`` models from the loose wire body
OUTSIDE ``adcp_validation_boundary``, so buyer-invalid input surfaced as a
suggestion-less VALIDATION_ERROR envelope carrying the raw pydantic dump instead
of the boundary's buyer message + field + top-level suggestion (error.json).

This guard AST-scans the boundary layers and fails on any request construction —
``XxxRequest(...)``, ``XxxRequest.model_validate(...)`` /
``.model_validate_json(...)`` / ``.parse_obj(...)``, a request-builder call
(``create_*_request(...)`` / ``build_*_request(...)``), or a raise-capable
``to_*`` coercion-helper call (#1417) — that is not lexically inside
a ``with adcp_validation_boundary(...)`` block.

The raise-capable ``to_*`` set is DERIVED from ``src/core/schema_helpers.py``:
every module-level ``to_*`` function whose body does not open its own
``adcp_validation_boundary``. A helper that grows an internal boundary (the
``coerce_creative_filters`` pattern) drops out of the matched set
automatically; a new boundary-less ``to_xyz`` helper is matched at its call
sites without touching this guard.

Scope is ``src/routes/`` (the REST boundary layer) and ``src/a2a_server/``
(the A2A skill-handler boundary layer, added by #1417 after five
skill handlers were found constructing requests bare — the exact disease this
guard exists to catch). Ships with ZERO violations; no allowlist (repo hard
rule: allowlists never grow).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [REPO_ROOT / "src" / "routes", REPO_ROOT / "src" / "a2a_server"]
SCHEMA_HELPERS = REPO_ROOT / "src" / "core" / "schema_helpers.py"

_VALIDATE_METHODS = {"model_validate", "model_validate_json", "parse_obj"}


def _has_boundary_with(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.With) and _boundary_with(sub) for sub in ast.walk(node))


def unguarded_coercion_helpers(schema_helpers_tree: ast.AST) -> frozenset[str]:
    """Module-level ``to_*`` helpers in schema_helpers with NO internal boundary.

    These construct typed models from buyer wire input and can raise a bare
    ``ValidationError`` — calling them outside ``adcp_validation_boundary``
    is the #1417 disease. Helpers with an internal boundary (the
    ``coerce_creative_filters`` pattern) are safe from any call site, as are
    helpers that DELEGATE to a module-local function carrying the boundary
    (the shared ``_coerce_wire_object`` coercer — one level of delegation,
    module-local only).
    """
    module_body = getattr(schema_helpers_tree, "body", [])
    functions = {node.name: node for node in module_body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
    boundary_carriers = {name for name, node in functions.items() if _has_boundary_with(node)}

    def _delegates_to_carrier(node: ast.AST) -> bool:
        return any(
            isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in boundary_carriers
            for sub in ast.walk(node)
        )

    return frozenset(
        name
        for name, node in functions.items()
        if name.startswith("to_") and name not in boundary_carriers and not _delegates_to_carrier(node)
    )


def _boundary_with(node: ast.With) -> bool:
    return any(
        isinstance(item.context_expr, ast.Call)
        and (
            (isinstance(item.context_expr.func, ast.Name) and item.context_expr.func.id == "adcp_validation_boundary")
            or (
                isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "adcp_validation_boundary"
            )
        )
        for item in node.items
    )


def _request_construction_name(node: ast.Call, coercion_helpers: frozenset[str] = frozenset()) -> str | None:
    """Name of the strict-request construction this call performs, or None.

    Matches:
    - ``XxxRequest(...)`` — direct construction (bare ``Request`` excluded:
      that's the framework class used in type hints, never a wire model)
    - ``XxxRequest.model_validate(...)`` / ``.model_validate_json(...)`` /
      ``.parse_obj(...)``
    - ``create_xxx_request(...)`` / ``build_xxx_request(...)`` — builder
      helpers that construct the request internally (the get_products form)
    - a call to any name in ``coercion_helpers`` — the boundary-less ``to_*``
      schema_helpers coercions (#1417)
    """
    fn = node.func
    if isinstance(fn, ast.Name):
        name = fn.id
        if name.endswith("Request") and name != "Request":
            return name
        if (name.startswith("create_") or name.startswith("build_")) and name.endswith("_request"):
            return name
        if name in coercion_helpers:
            return name
    if isinstance(fn, ast.Attribute):
        if fn.attr in _VALIDATE_METHODS:
            base = fn.value
            if isinstance(base, ast.Name) and base.id.endswith("Request") and base.id != "Request":
                return f"{base.id}.{fn.attr}"
        name = fn.attr
        if (name.startswith("create_") or name.startswith("build_")) and name.endswith("_request"):
            return name
        if name in coercion_helpers:
            return name
    return None


class _UnboundedRequestFinder(ast.NodeVisitor):
    def __init__(self, coercion_helpers: frozenset[str] = frozenset()) -> None:
        self.boundary_depth = 0
        self.coercion_helpers = coercion_helpers
        self.offenders: list[tuple[int, str]] = []

    def visit_With(self, node: ast.With) -> None:
        if _boundary_with(node):
            self.boundary_depth += 1
            self.generic_visit(node)
            self.boundary_depth -= 1
        else:
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _request_construction_name(node, self.coercion_helpers)
        if name and self.boundary_depth == 0:
            self.offenders.append((node.lineno, name))
        self.generic_visit(node)


def find_unbounded_request_constructions(
    tree: ast.AST, coercion_helpers: frozenset[str] = frozenset()
) -> list[tuple[int, str]]:
    finder = _UnboundedRequestFinder(coercion_helpers)
    finder.visit(tree)
    return finder.offenders


def test_no_unbounded_request_construction_at_transport_boundaries():
    coercion_helpers = unguarded_coercion_helpers(ast.parse(SCHEMA_HELPERS.read_text(), filename=str(SCHEMA_HELPERS)))
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for lineno, name in find_unbounded_request_constructions(tree, coercion_helpers):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not violations, (
        "Strict request construction or raise-capable to_* coercion outside "
        "`with adcp_validation_boundary(context=...)` at a transport boundary "
        "(REST route / A2A skill handler) — buyer-invalid input would surface as a "
        "suggestion-less envelope with the raw pydantic message (#1417). Wrap the "
        "call site, or give the "
        "helper an internal boundary (the coerce_creative_filters pattern) so it "
        "drops out of the matched set. Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[tuple[int, str]]:
    return find_unbounded_request_constructions(ast.parse(snippet))


class TestGuardDetector:
    def test_positive_direct_construction(self):
        assert _detect("req = ListAccountsRequest(**body.model_dump())")

    def test_positive_model_validate_form(self):
        assert _detect("req = GetMediaBuysRequest.model_validate(params)")

    def test_positive_builder_call_form(self):
        # The get_products form: the Request is constructed inside a helper,
        # so a name-ends-with-Request matcher alone would miss it.
        assert _detect("req = products_module.create_get_products_request(brief=body.brief)")

    def test_positive_conditional_expression(self):
        # `X(**f) if f else None` — construction nested in an IfExp still counts.
        assert _detect("req = ListAuthorizedPropertiesRequest(**fields) if fields else None")

    def test_negative_wrapped_construction(self):
        assert not _detect(
            "with adcp_validation_boundary(context='list_accounts request'):\n"
            "    req = ListAccountsRequest(**body.model_dump())"
        )

    def test_negative_wrapped_builder_call(self):
        assert not _detect(
            "with adcp_validation_boundary(context='get_products request'):\n"
            "    req = create_get_products_request(brief=brief)"
        )

    def test_negative_bare_framework_request_type(self):
        # FastAPI's `Request` class is a type hint / framework object, not a
        # strict wire model — constructing or referencing it is not the disease.
        assert not _detect("payload = Request(scope)")

    def test_negative_non_request_call(self):
        assert not _detect("resp = response.model_dump(mode='json')")

    # -- #1417: to_* coercion helpers + extra validate forms --

    def test_positive_model_validate_json_form(self):
        assert _detect("req = GetMediaBuysRequest.model_validate_json(raw)")

    def test_positive_parse_obj_form(self):
        assert _detect("req = GetMediaBuysRequest.parse_obj(params)")

    def test_positive_unwrapped_coercion_helper_call(self):
        helpers = frozenset({"to_context_object"})
        assert find_unbounded_request_constructions(ast.parse("ctx = to_context_object(body.context)"), helpers)

    def test_positive_unwrapped_helper_as_call_argument(self):
        # The oygh shape: the helper call nested inside another call's args.
        helpers = frozenset({"to_context_object"})
        assert find_unbounded_request_constructions(
            ast.parse("resp = module.raw(context=to_context_object(body.context))"), helpers
        )

    def test_negative_wrapped_coercion_helper_call(self):
        helpers = frozenset({"to_reporting_webhook"})
        assert not find_unbounded_request_constructions(
            ast.parse(
                "with adcp_validation_boundary(context='create_media_buy request'):\n"
                "    hook = to_reporting_webhook(body.reporting_webhook)"
            ),
            helpers,
        )

    def test_negative_helper_not_in_derived_set(self):
        # A to_* name outside the derived set (e.g. one with an internal
        # boundary) is not matched.
        assert not _detect("ctx = to_context_object(body.context)")


class TestUnguardedCoercionHelperDerivation:
    def test_boundary_less_helper_included(self):
        tree = ast.parse("def to_context_object(v):\n    return ContextObject(**v)")
        assert unguarded_coercion_helpers(tree) == frozenset({"to_context_object"})

    def test_helper_with_internal_boundary_excluded(self):
        # The coerce_creative_filters pattern: internal boundary → safe from
        # any call site → drops out of the matched set.
        tree = ast.parse(
            "def to_safe_thing(v):\n"
            "    with adcp_validation_boundary(context='thing'):\n"
            "        return Thing.model_validate(v)"
        )
        assert unguarded_coercion_helpers(tree) == frozenset()

    def test_helper_delegating_to_boundary_carrier_excluded(self):
        # The _coerce_wire_object pattern: the boundary lives in ONE shared
        # module-local coercer; to_* helpers that delegate to it are safe.
        tree = ast.parse(
            "def _coerce_wire_object(v, cls, context):\n"
            "    with adcp_validation_boundary(context=context):\n"
            "        return cls.model_validate(v)\n"
            "\n"
            "def to_thing(v):\n"
            "    return _coerce_wire_object(v, Thing, 'thing value')\n"
            "\n"
            "def to_bare_thing(v):\n"
            "    return Thing(**v)\n"
        )
        assert unguarded_coercion_helpers(tree) == frozenset({"to_bare_thing"})

    def test_non_to_functions_ignored(self):
        tree = ast.parse("def coerce_creative_filters(v):\n    return v\n\ndef helper(v):\n    return v")
        assert unguarded_coercion_helpers(tree) == frozenset()
