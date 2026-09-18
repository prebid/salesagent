"""The bounded OPERATION VOCABULARY — every name a signature metric label can carry.

``operation`` is a Prometheus LABEL on all three request-signature counters
(:mod:`src.core.metrics`). Before #1721 its value came VERBATIM out of the request body
on two of the three transports — ``params.name`` for an MCP ``tools/call``,
``data.skill`` for an A2A ``message/send`` — and the verifier ran ABOVE authentication,
so recording it raw let an anonymous ``POST /mcp`` mint one new time series per request
forever, in a long-running multi-tenant process.

That hazard is SMALLER on #1721's boundary and the bound is kept anyway. The verifier now
runs inside ``_resolve_identity``, which ``invoke_tool`` reaches only after
``TOOLS[tool_name]`` resolved — so the label is already a registry key by construction on
every transport, and an unknown name never gets that far. The bound stays because the
counters are also written from paths that record no tool at all (a refusal raised before
a name is known records ``""``), and because a bound that exists only as an argument
about reachability stops holding the day a caller changes.

DERIVED, never hand-listed. A hand-written copy would be a second source of truth for the
same surface, and it fails the way second copies always fail: silently, on the day a tool
is added, by demoting that tool's real traffic into the bucket that exists to alarm on
attacker-supplied names.

THE LEAF RULE: nothing here imports :mod:`src.core.metrics`, :mod:`src.core.config` or
:mod:`src.core.database` at module level, and the registry is read through a
FUNCTION-LOCAL import at first call. ``src/app.py`` imports the metrics module while the
route table is still being assembled, so a module-level read would run against a
half-built application.
"""

from __future__ import annotations

from functools import lru_cache


def is_adcp_operation(name: str) -> bool:
    """Whether *name* is an AdCP operation THIS seller implements.

    Delegated to the registry, which owns the answer -- see
    :func:`src.core.tools.registry.is_adcp_operation`. Present here so a reader of the
    signing layer finds the predicate beside the vocabulary it bounds, and so nothing in
    this layer decides tool-ness by looking for a ``/``.
    """
    from src.core.tools.registry import is_adcp_operation as _registry_predicate

    return _registry_predicate(name)


def sdk_operation_names() -> frozenset[str]:
    """The AdCP operation names the pinned SDK defines.

    A CROSS-CHECK leg, never the authority (CLAUDE.md § spec-grounding gate): the SDK
    list can diverge from the spec and names operations this seller does not implement.
    It is in the union below so that an operation the SDK knows about is a bounded label
    the moment we start serving it, ahead of our own registry naming it.
    """
    from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS

    return frozenset(definition["name"] for definition in ADCP_TOOL_DEFINITIONS)


@lru_cache(maxsize=1)
def operation_label_names() -> frozenset[str]:
    """Every value the ``operation`` metric label may carry, derived.

    The registry's keys — which ARE the MCP tool names, the A2A skill ids and the REST
    route names, because #1721 generates all three from one row — unioned with the SDK's
    definitions, plus ``""``.

    ``""`` is ONE series and is deliberately kept distinct from ``"other"``: it is what a
    refusal raised before a tool was named records, and folding it into the bucket whose
    whole job is to make an attacker-supplied name visible would bury exactly that signal.

    Cached: the registry is a ``MappingProxyType`` over a module-private dict that nothing
    may mutate at runtime, and the first call is at request time.
    """
    from src.core.tools.registry import TOOLS

    return frozenset({""}) | sdk_operation_names() | frozenset(TOOLS)
