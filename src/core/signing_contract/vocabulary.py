"""The bounded OPERATION VOCABULARY — every name a request can resolve to.

Lives in the leaf, not in :mod:`src.core.signing.operations`, because
:mod:`src.core.metrics` reads it to bound an attacker-chosen Prometheus label and the
signing facade imports the ASGI middleware, which imports metrics. While this lived
behind ``src.core.signing``'s ``__init__``, that was a cycle — the third of three the
``_LAZY_EXPORTS`` deferral was hiding (salesagent-n78j0.3).

THE LEAF RULE, stated precisely: nothing here imports :mod:`src.core.signing`,
:mod:`src.core.config`, :mod:`src.core.metrics` or :mod:`src.core.database` AT MODULE
LEVEL. The transport registries below are read through FUNCTION-LOCAL imports at request
time, which is what keeps the import graph acyclic while still deriving the vocabulary
from the registries that actually serve traffic rather than from a hand-written list.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: The only two ``/api/v1`` endpoint functions whose name is not the operation they
#: serve (``src/routes/api_v1.py:252,259`` — one GET and one POST binding of the same
#: AdCP operation). Any further divergence is a rename, and
#: ``tests/unit/test_architecture_signing_operations.py`` fails the build on it rather
#: than letting it resolve to a name no declaration can carry.
_REST_ENDPOINT_ALIASES: dict[str, str] = {
    "get_capabilities": "get_adcp_capabilities",
    "post_capabilities": "get_adcp_capabilities",
}


@lru_cache(maxsize=1)
def _rest_registry() -> tuple[tuple[frozenset[str], re.Pattern[str], str], ...]:
    """``(methods, path regex, operation)`` for every ``/api/v1`` route.

    Derived from ``api_v1.router.routes``, so a new route is named the moment it is
    registered and a renamed one fails the guard instead of silently resolving to
    nothing. The route's OWN ``path_regex`` does the matching — re-implementing path
    templating here is how the two would drift.

    Imported inside the function: this module is imported by the ASGI middleware,
    which ``src/app.py`` registers while the router is still being assembled.
    """
    from src.routes.api_v1 import router

    entries: list[tuple[frozenset[str], re.Pattern[str], str]] = []
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        path_regex = getattr(route, "path_regex", None)
        if endpoint is None or path_regex is None:
            continue
        operation = _REST_ENDPOINT_ALIASES.get(endpoint.__name__, endpoint.__name__)
        entries.append((frozenset(getattr(route, "methods", None) or ()), path_regex, operation))
    return tuple(entries)


def operation_for_rest_route(method: str, path: str) -> str:
    """The AdCP operation a ``/api/v1`` (method, path) pair invokes, or ``""``.

    Public because ``src/routes/rest_compat_middleware.py`` derives its own 3-entry
    gate's VALUES from it (R-M2) — one table, two readers.
    """
    for methods, path_regex, operation in _rest_registry():
        if path_regex.match(path) and (not methods or method in methods):
            return operation
    return ""


# ---------------------------------------------------------------------------
#
# ``operation`` is a Prometheus LABEL on all three request-signature counters
# (``src/core/metrics.py``), and on two of the three transports its value comes
# VERBATIM out of the request body — ``params.name`` for an MCP ``tools/call``,
# ``data.skill`` for an A2A ``message/send``. The verifier runs ABOVE
# authentication, so recording it raw would let an anonymous
# ``POST /mcp {"method":"tools/call","params":{"name":"<anything>"}}`` mint one new
# time series per request, forever, in a long-running multi-tenant process. The
# label is therefore bounded against the closed set below exactly the way ``code``
# is bounded against the SDK's error taxonomy: anything outside it collapses to
# ``"other"``, so cardinality is a function of the vocabulary and not of the caller.
#
# DERIVED, never hand-listed. A hand-written copy would be a second source of truth
# for the same surface, and it fails the way second copies always fail: silently, on
# the day a tool is added, by demoting that tool's real traffic into the bucket that
# exists to alarm on attacker-supplied names.
# ``tests/unit/test_architecture_signing_operations.py`` drives every registered MCP
# tool, every ``/api/v1`` route and every A2A skill through the sanitizer and fails
# the build if one of them does not survive verbatim.


def sdk_operation_names() -> frozenset[str]:
    """The AdCP operation names the pinned SDK defines.

    A CROSS-CHECK leg, never the authority (module docstring): the SDK list can
    diverge from the spec and is missing two operations we genuinely implement. It
    is in the union so that an operation the SDK knows about is a bounded label the
    moment we start serving it, ahead of any of our own registries naming it.
    """
    from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS

    return frozenset(definition["name"] for definition in ADCP_TOOL_DEFINITIONS)


@lru_cache(maxsize=1)
def resolved_operation_names() -> frozenset[str]:
    """Every value :attr:`ResolvedOperation.operation` can carry, derived.

    The union of the four registries this resolver names requests from — the SDK's
    definitions, the ``_register_tool`` list in ``src/core/main.py``, the
    ``/api/v1`` route table and the A2A skill dispatch table — plus
    :data:`UNNAMED_OPERATION`'s ``""``, which the table in the module docstring
    gives a request named in the PROTOCOL namespace or carrying no body at all.
    ``""`` is ONE series and is deliberately kept distinct: folding it into
    ``"other"`` would bury every MCP handshake in the bucket whose whole job is to
    make an attacker-supplied name visible.

    The imports are inside the function for the reason ``_rest_registry`` gives:
    this module is imported by the ASGI middleware, which ``src/app.py`` registers
    while the transports are still being assembled. Cached — all four registries are
    fixed once the process has finished importing, and the first call is at request
    time.
    """
    from src.a2a_server.adcp_a2a_server import SKILL_HANDLERS
    from src.core.main import MCP_TOOL_NAMES

    return (
        # The unnamed sentinel — what the resolver returns for a transport session
        # frame. A MEMBER of the vocabulary, not a collapse: the integration suite
        # reads the empty label as proof a session frame was not named as an
        # operation. Spelled literally because UNNAMED_OPERATION lives in
        # src.core.signing.operations, which is ABOVE this leaf.
        frozenset({""})
        | sdk_operation_names()
        | frozenset(MCP_TOOL_NAMES)
        | frozenset(SKILL_HANDLERS)
        | frozenset(operation for _methods, _regex, operation in _rest_registry())
    )
