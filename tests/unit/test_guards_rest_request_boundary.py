"""Guard: transport boundaries build strict request models only inside adcp_validation_boundary.

Regression guard for the salesagent-0pry family (#1417): REST routes in
``src/routes/`` constructed strict ``*Request`` models from the loose wire body
OUTSIDE ``adcp_validation_boundary``, so buyer-invalid input surfaced as a
suggestion-less VALIDATION_ERROR envelope carrying the raw pydantic dump instead
of the boundary's buyer message + field + top-level suggestion (error.json).

This guard AST-scans the boundary layers and fails on any request construction —
``XxxRequest(...)``, ``XxxRequest.model_validate(...)``, or a request-builder
call (``create_*_request(...)`` / ``build_*_request(...)``) — that is not
lexically inside a ``with adcp_validation_boundary(...)`` block.

Scope is ``src/routes/`` (the REST boundary layer) and ``src/a2a_server/``
(the A2A skill-handler boundary layer, added by salesagent-klkg after five
skill handlers were found constructing requests bare — the exact disease this
guard exists to catch). Ships with ZERO violations; no allowlist (repo hard
rule: allowlists never grow).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [REPO_ROOT / "src" / "routes", REPO_ROOT / "src" / "a2a_server"]


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


def _request_construction_name(node: ast.Call) -> str | None:
    """Name of the strict-request construction this call performs, or None.

    Matches:
    - ``XxxRequest(...)`` — direct construction (bare ``Request`` excluded:
      that's the framework class used in type hints, never a wire model)
    - ``XxxRequest.model_validate(...)``
    - ``create_xxx_request(...)`` / ``build_xxx_request(...)`` — builder
      helpers that construct the request internally (the get_products form)
    """
    fn = node.func
    if isinstance(fn, ast.Name):
        name = fn.id
        if name.endswith("Request") and name != "Request":
            return name
        if (name.startswith("create_") or name.startswith("build_")) and name.endswith("_request"):
            return name
    if isinstance(fn, ast.Attribute):
        if fn.attr == "model_validate":
            base = fn.value
            if isinstance(base, ast.Name) and base.id.endswith("Request") and base.id != "Request":
                return f"{base.id}.model_validate"
        name = fn.attr
        if (name.startswith("create_") or name.startswith("build_")) and name.endswith("_request"):
            return name
    return None


class _UnboundedRequestFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.boundary_depth = 0
        self.offenders: list[tuple[int, str]] = []

    def visit_With(self, node: ast.With) -> None:
        if _boundary_with(node):
            self.boundary_depth += 1
            self.generic_visit(node)
            self.boundary_depth -= 1
        else:
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _request_construction_name(node)
        if name and self.boundary_depth == 0:
            self.offenders.append((node.lineno, name))
        self.generic_visit(node)


def find_unbounded_request_constructions(tree: ast.AST) -> list[tuple[int, str]]:
    finder = _UnboundedRequestFinder()
    finder.visit(tree)
    return finder.offenders


def test_no_unbounded_request_construction_at_transport_boundaries():
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for lineno, name in find_unbounded_request_constructions(tree):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not violations, (
        "Strict request construction outside `with adcp_validation_boundary(context=...)` "
        "at a transport boundary (REST route / A2A skill handler) — buyer-invalid input "
        "would surface as a suggestion-less envelope with the raw pydantic message "
        "(salesagent-0pry / salesagent-klkg, #1417). Wrap the construction. "
        "Violations:\n  " + "\n  ".join(violations)
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
