"""Structural guard: the A2A list_creatives skill handler forwards every param
its raw wrapper accepts.

The A2A transport dispatches list_creatives through
``AdCPRequestHandler._handle_list_creatives_skill``, which hand-maps the wire
``parameters`` dict into keyword arguments on ``list_creatives_raw`` (imported in
the A2A server as ``core_list_creatives_tool``). That dict->kwargs mapping is
maintained separately from the REST route's ``ListCreativesBody`` forwarding
(guarded by ``test_architecture_rest_body_completeness.py``) and the MCP
``list_creatives`` signature, so a param present in one copy can be dropped from
another — the drift that silently skipped ``media_buy_ids`` (and the length cap
that runs on the merged filters) on A2A until #1511.

This guard fails if the handler drops a ``list_creatives_raw`` parameter that is
not explicitly allowlisted with a justification. It is a signature-derived parity
check (the same shape ``test_capped_fields_match_pinned_schema_array_filters``
uses for the cap tuple), so a future param added to ``list_creatives_raw`` and
dropped from the A2A handler fails CI instead of silently regressing.

It complements ``test_architecture_boundary_completeness.py``, which guards the
``_impl`` -> ``*_raw`` wrapper hop but not the A2A skill-handler dict layer that
sits above the wrapper.

Scope: the ``list_creatives`` handler — the one #1511 touches. Extending the same
convention to the other ``_handle_*_skill`` handlers is a follow-up: each needs
its own raw-wrapper pairing and an allowlist audit of intentional omissions.
"""

from __future__ import annotations

import ast

from src.core.tools.creatives.listing import list_creatives_raw
from tests.unit._architecture_helpers import (
    forwarding_gaps,
    iter_call_expressions,
    parse_module,
    repo_root,
    stale_forwarding_allowlist_entries,
)

_A2A_SERVER = repo_root() / "src" / "a2a_server" / "adcp_a2a_server.py"
_HANDLER = "_handle_list_creatives_skill"

# The name ``list_creatives_raw`` is imported under inside the A2A server.
_RAW_CALLEE = "core_list_creatives_tool"

# Params resolved/injected at the transport boundary, never forwarded from the
# wire ``parameters`` dict: ``ctx`` is FastMCP-only; ``identity`` is resolved at
# the A2A boundary and passed explicitly.
_TRANSPORT_PARAMS = {"ctx", "identity"}

# Allowlisted omissions: {param_name: justification}. Can only SHRINK — every
# entry needs a real reason, never a blanket escape.
_ALLOWLIST: dict[str, str] = {}


def _handler_forwarded_kwargs() -> set[str]:
    """Keyword names the A2A list_creatives handler passes to the raw wrapper.

    File-level AST (``parse_module`` reads the source file; ``inspect.getsource``
    is banned in tests): find the handler function in the A2A server and read the
    kwargs on its ``core_list_creatives_tool(...)`` call.
    """
    tree = parse_module(_A2A_SERVER)
    handler_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == _HANDLER
    ]
    assert len(handler_nodes) == 1, f"expected exactly one {_HANDLER} in {_A2A_SERVER.name}, found {len(handler_nodes)}"

    forwarded: set[str] = set()
    for call in iter_call_expressions(handler_nodes[0], _RAW_CALLEE):
        forwarded.update(kw.arg for kw in call.keywords if kw.arg is not None)
    assert forwarded, f"non-vacuity: no {_RAW_CALLEE}(...) call found in {_HANDLER}"
    return forwarded


def test_a2a_list_creatives_handler_forwards_all_raw_params():
    """``_handle_list_creatives_skill`` must forward every param ``list_creatives_raw`` accepts."""
    missing = forwarding_gaps(
        raw_fn=list_creatives_raw,
        declared=_handler_forwarded_kwargs(),
        plumbing=_TRANSPORT_PARAMS,
        allowlist=_ALLOWLIST,
    )
    assert not missing, (
        "A2A _handle_list_creatives_skill drops parameters list_creatives_raw() accepts — "
        f"A2A buyers lose these silently: {sorted(missing)}. Forward them in the handler, "
        "or allowlist with a justification."
    )


def test_a2a_forwarding_allowlist_has_no_stale_entries():
    """Allowlist entries must be real raw-wrapper params still missing from the handler."""
    stale = stale_forwarding_allowlist_entries(
        raw_fn=list_creatives_raw,
        declared=_handler_forwarded_kwargs(),
        plumbing=_TRANSPORT_PARAMS,
        allowlist=_ALLOWLIST,
        declared_desc="forwarded by the handler",
    )
    assert not stale, "Stale A2A-forwarding allowlist entries:\n" + "\n".join(stale)
