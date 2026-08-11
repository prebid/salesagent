"""Guard: a tool's request fields must not diverge across its transports.

Every AdCP tool is exposed on three transports, and each one decides for itself which
request fields it forwards. When a transport NAMES each field, a field added later lands
on one surface and is silently absent from the others -- no error, no test failure, the
buyer's value simply vanishes. That is not hypothetical: ``sync_accounts`` shipped a
spec-REQUIRED, client-generated ``idempotency_key`` that MCP could not accept (it minted
its own uuid4 instead), A2A dropped, and the REST body did not declare
(salesagent-e8wt.1 / GH #1721 review F1).

The lesson the same finding forced is the reason this guard exists rather than a code
change alone: ``list_creative_formats`` ALREADY had a shared ``build_X_request()`` that
every transport called, and its A2A handler still omitted two of the builder's kwargs.
A builder does not remove the enumeration; it moves it one frame. MCP's enumeration is
irreducible (FastMCP needs a typed signature for tool introspection), so the invariant
cannot hold by construction on every transport -- it has to be pinned.

Compliant shapes, per transport:
  MCP  -- the wrapper's parameter names
  A2A  -- either ``select_request_fields(Model, parameters)`` / ``model_validate`` /
          ``{**parameters}`` (wholesale consumption, which cannot drift), or an
          enumeration that names every field the other transports carry
  REST -- the ``*Body`` model's declared fields

ALLOWLIST is shrink-only. Every entry cites a GitHub issue.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
A2A_SERVER = REPO_ROOT / "src/a2a_server/adcp_a2a_server.py"
API_V1 = REPO_ROOT / "src/routes/api_v1.py"
TOOLS_DIR = REPO_ROOT / "src/core/tools"

#: Parameters that are transport plumbing or identity, never buyer request fields.
NON_REQUEST_PARAMS = frozenset({"ctx", "self", "identity", "req", "adcp_version", "adcp_major_version"})

#: Names that mean "this handler consumes the whole parameter bag" -- immune by construction.
WHOLESALE_MARKERS = ("select_request_fields", "model_validate", "**parameters", "**params")

#: Known divergences, each citing the GitHub PR/issue that records them. SHRINK ONLY -- never add.
#: Format: (tool, field) -> "#<issue> reason"
ALLOWLIST: dict[tuple[str, str], str] = {
    ("list_creatives", "pagination"): (
        "#1721 review F1 scan row 12: pagination/sort exist only on the MCP surface. Not a "
        "dropped kwarg -- it needs a pinned-spec decision on whether they supersede "
        "page/limit/sort_by/sort_order before being added to A2A and the REST body."
    ),
    ("list_creatives", "sort"): "#1721 see pagination above.",
    ("update_media_buy", "creatives"): (
        "#1417 deliberately omitted from the REST body: update_media_buy_raw accepts these in "
        "its signature but drops them before _build_update_request, so declaring them on REST "
        "would advertise a silent no-op."
    ),
    ("update_media_buy", "targeting_overlay"): "#1417 see creatives above.",
    ("update_performance_index", "webhook_url"): (
        "#1721 review F1 scan follow-on: webhook_url is a DEAD MCP parameter -- the wrapper "
        "accepts it and _build_update_performance_index_request never takes it, so it is "
        "dropped on MCP too. Adding it to REST would spread the no-op; the fix is to implement "
        "async notification or remove the parameter, both wire-contract decisions of their own."
    ),
}


def _mcp_tool_params() -> dict[str, set[str]]:
    """Tool name -> the MCP wrapper's buyer-facing parameter names."""
    tools: dict[str, set[str]] = {}
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if node.name.startswith("_") or node.name.endswith("_raw"):
                continue
            args = [a.arg for a in node.args.args + node.args.kwonlyargs]
            if "ctx" not in args:
                continue
            tools[node.name] = {a for a in args if a not in NON_REQUEST_PARAMS}
    return tools


def _a2a_skill_params() -> tuple[dict[str, set[str]], set[str]]:
    """Tool name -> keys the A2A handler names, plus the set of wholesale handlers."""
    source = A2A_SERVER.read_text()
    tree = ast.parse(source)
    named: dict[str, set[str]] = {}
    wholesale: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        match = re.fullmatch(r"_handle_(\w+)_skill", node.name)
        if not match:
            continue
        tool = match.group(1)
        segment = ast.get_source_segment(source, node) or ""
        if any(marker in segment for marker in WHOLESALE_MARKERS):
            wholesale.add(tool)
        keys: set[str] = set()
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in {"get", "pop"}
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id in {"parameters", "params"}
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
            ):
                keys.add(str(sub.args[0].value))
        named[tool] = keys - NON_REQUEST_PARAMS
    return named, wholesale


def _rest_body_fields() -> dict[str, set[str]]:
    """``*Body`` class name -> declared field names."""
    tree = ast.parse(API_V1.read_text())
    bodies: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Body"):
            continue
        fields = {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
        bodies[node.name] = fields - NON_REQUEST_PARAMS
    return bodies


def _rest_path_params() -> dict[str, set[str]]:
    """Route function name -> parameter names carried in the URL path, not the body.

    ``PUT /media-buys/{media_buy_id}`` carries media_buy_id in the path. That is a real
    difference in FRAMING, not a dropped field -- the buyer can still send it -- so the
    path segment counts toward the REST surface.
    """
    tree = ast.parse(API_V1.read_text())
    routes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        params: set[str] = set()
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and decorator.args):
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                params |= set(re.findall(r"\{(\w+)\}", first.value))
        if params:
            routes[node.name] = params
    return routes


def _body_name(tool: str) -> str:
    return "".join(part.capitalize() for part in tool.split("_")) + "Body"


def collect_divergences() -> dict[tuple[str, str], list[str]]:
    """(tool, field) -> the transports MISSING that field, for every real divergence."""
    mcp = _mcp_tool_params()
    a2a_named, a2a_wholesale = _a2a_skill_params()
    rest = _rest_body_fields()
    path_params = _rest_path_params()

    divergences: dict[tuple[str, str], list[str]] = {}
    for tool in sorted(set(mcp) | set(a2a_named)):
        surfaces: dict[str, set[str]] = {}
        if tool in mcp:
            surfaces["mcp"] = mcp[tool]
        # A wholesale handler forwards everything, so it can never be the one that lags.
        if tool in a2a_named and tool not in a2a_wholesale:
            surfaces["a2a"] = a2a_named[tool]
        if _body_name(tool) in rest:
            surfaces["rest"] = rest[_body_name(tool)] | path_params.get(tool, set())
        if len(surfaces) < 2:
            continue
        for field in sorted(set().union(*surfaces.values())):
            missing = sorted(name for name, fields in surfaces.items() if field not in fields)
            if missing:
                divergences[(tool, field)] = missing
    return divergences


def test_no_request_field_diverges_across_transports() -> None:
    """No tool may carry a request field on one transport and not another."""
    divergences = collect_divergences()
    unexpected = {key: missing for key, missing in divergences.items() if key not in ALLOWLIST}
    assert not unexpected, (
        "Request fields diverge across transports. A field one transport accepts and another "
        "does not is silently dropped for the buyer.\n"
        + "\n".join(
            f"  {tool}.{field} -- missing on: {', '.join(missing)}" for (tool, field), missing in unexpected.items()
        )
        + "\n\nForward the field on every transport, or consume the parameter bag wholesale "
        "(select_request_fields / model_validate). Do NOT add to ALLOWLIST -- it only shrinks."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """An allowlisted divergence that no longer exists must be removed from ALLOWLIST."""
    divergences = collect_divergences()
    stale = sorted(key for key in ALLOWLIST if key not in divergences)
    assert not stale, f"ALLOWLIST entries no longer divergent -- delete them: {stale}"


def test_allowlist_entries_cite_a_github_issue() -> None:
    """Every allowlisted divergence is tracked debt, so it must name a GitHub issue."""
    uncited = sorted(key for key, reason in ALLOWLIST.items() if not re.search(r"#\d+", reason))
    assert not uncited, f"ALLOWLIST entries without a GitHub issue citation: {uncited}"


# ---------------------------------------------------------------------------
# Meta-tests: the detector itself must actually detect.
# ---------------------------------------------------------------------------


class TestDetectorMetaTests:
    """Positive, negative, and near-miss cases for the AST detectors."""

    @staticmethod
    def _a2a_named_keys(source: str) -> tuple[set[str], bool]:
        tree = ast.parse(source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef))
        segment = ast.get_source_segment(source, node) or ""
        wholesale = any(marker in segment for marker in WHOLESALE_MARKERS)
        keys = {
            str(sub.args[0].value)
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in {"get", "pop"}
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id in {"parameters", "params"}
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
        }
        return keys, wholesale

    def test_positive_enumerating_handler_keys_are_extracted(self) -> None:
        keys, wholesale = self._a2a_named_keys(
            "async def _handle_x_skill(self, parameters, identity):\n"
            "    return core(a=parameters.get('alpha'), b=parameters.get('beta'))\n"
        )
        assert keys == {"alpha", "beta"}
        assert wholesale is False

    def test_negative_wholesale_handler_is_recognised(self) -> None:
        _, wholesale = self._a2a_named_keys(
            "async def _handle_x_skill(self, parameters, identity):\n"
            "    return core(req=Model(**select_request_fields(Model, parameters)))\n"
        )
        assert wholesale is True

    def test_would_be_missed_case_handler_enumerating_all_but_one_kwarg(self) -> None:
        """The exact shape that defeated the builder: 3 of 4 kwargs named, 1 omitted.

        Nothing about this handler looks wrong locally -- it calls the shared builder and
        passes named kwargs. Only the cross-transport comparison sees the hole, which is
        why the guard compares field SETS and not the presence of a builder call.
        """
        keys, wholesale = self._a2a_named_keys(
            "async def _handle_x_skill(self, parameters, identity):\n"
            "    return core(build_x_request(\n"
            "        a=parameters.get('alpha'),\n"
            "        b=parameters.get('beta'),\n"
            "        c=parameters.get('gamma'),\n"
            "    ))\n"
        )
        assert wholesale is False, "a builder call must NOT be mistaken for wholesale consumption"
        assert "delta" not in keys, "the omitted kwarg must be absent, which is what makes it detectable"
        assert keys == {"alpha", "beta", "gamma"}

    def test_rest_body_field_extraction(self) -> None:
        tree = ast.parse("class FooBody(SalesAgentBaseModel):\n    alpha: str = ''\n    beta: int | None = None\n")
        node = tree.body[0]
        assert isinstance(node, ast.ClassDef)
        fields = {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
        assert fields == {"alpha", "beta"}

    def test_builder_kwargs_match_their_request_models(self) -> None:
        """The builders are splatted with ``**select_request_fields(Model, ...)``.

        That splat is only safe while every model field is a builder kwarg -- otherwise a
        newly declared field becomes a TypeError at request time instead of being forwarded.
        Pin it here rather than discovering it on the wire.
        """
        import inspect

        from src.core.schemas.account import ListAccountsRequest, SyncAccountsRequest
        from src.core.tools.accounts import build_list_accounts_request, build_sync_accounts_request

        for builder, model in (
            (build_list_accounts_request, ListAccountsRequest),
            (build_sync_accounts_request, SyncAccountsRequest),
        ):
            kwargs = set(inspect.signature(builder).parameters)
            missing = set(model.model_fields) - kwargs
            assert not missing, (
                f"{builder.__name__} cannot accept {sorted(missing)} declared on {model.__name__}; "
                "a select_request_fields splat carrying those fields would raise TypeError."
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
