"""Structural guards for #1291 B2 on #1721's boundary — every operation name a
transport can serve is CLASSIFIED, and the metric label it becomes is bounded.

B2's deliverable is a classification, and a classification's failure mode is
silent. ``sanitize_operation`` collapses anything its closed vocabulary does not
admit into ``"other"`` (``src/core/metrics.py:226-240``), so a surface nobody
classified does not error — its real traffic QUIETLY LANDS IN THE BUCKET THAT
EXISTS TO ALARM ON ATTACKER-SUPPLIED NAMES. The fix is to derive the
classification from the production registry and fail ``make quality`` when
something new is unclassified.

What #1721 collapsed, and what that leaves this file
----------------------------------------------------
On the pre-merge branch there were FOUR transport-local name registries that
could disagree — the ``_register_tool(fn)`` calls in ``src/core/main.py``, the
``@router.post`` decorators in ``src/routes/api_v1.py``, ``SKILL_HANDLERS`` plus
the AgentCard's ``AgentSkill`` ids, and ``rest_compat_middleware._PATH_TO_TOOL``
— and a body-sniffing ``RegistryOperationResolver`` had to reconcile all four
per request, ABOVE authentication. Most of this file was that reconciliation.

#1721 deletes the problem rather than grading it. :data:`src.core.tools.registry.TOOLS`
is the single declaration all three transports are GENERATED from: MCP
registration loops it (``src/core/main.py:427-428``), the agent card is
``_derived_skills()`` over it and dispatch is ``TOOLS[skill_name]``
(``src/a2a_server/adcp_a2a_server.py:486-520``), and the REST router adds one
route per ``rest`` binding under ``name=<registry key>``
(``src/routes/api_v1.py``). The operation label is therefore a registry key BY
CONSTRUCTION on every transport, and the verifier runs inside
``_resolve_identity`` — after ``TOOLS[tool_name]`` resolved — rather than in a
middleware sniffing an anonymous body.

So these guards are DELETED here rather than ported, each because #1721 made the
drift it graded unrepresentable or moved it somewhere that already grades it:

* the resolver sweeps (``RegistryOperationResolver``, ``ResolvedOperation``,
  ``UNNAMED_OPERATION``) — ``src/core/signing/operations.py`` does not exist;
  the name is the registry key the boundary already resolved;
* ``TestA2ASkillListsAgree`` (R-M4) — there is no ``SKILL_HANDLERS`` to drift
  from the card, and card-versus-dispatch agreement is graded by
  ``tests/unit/test_a2a_transport_contract.py::TestAgentCardContract::test_agent_card_skills_match_dispatch``;
* ``TestRestCompatTableAgreesWithTheRouteTable`` (R-M2) —
  ``src/routes/rest_compat_middleware.py`` is deleted on #1721;
* ``TestBufferedReceiveReachesEveryArm`` (R-H2) —
  ``src/core/signing/request_verifier_middleware.py`` is deleted; nothing
  rewinds a receive channel because nothing reads the body before the boundary.

``adcp.server.mcp_tools.ADCP_TOOL_DEFINITIONS`` (63 names) stays what it always
was: the CROSS-CHECK, never the authority. Per the source hierarchy an SDK list
can diverge from the spec, so a registry row it does not name is allowlisted
with its reason rather than renamed.

What each surviving guard catches
---------------------------------
1. A new registry row the SDK does not define and nobody classified → its real
   traffic is filed under ``"other"``, indistinguishable from an attack. Fails
   here instead.
2. A hand-written ``/api/v1`` route slipping back in beside the derived ones →
   a REST surface whose name is a Python function name rather than an operation.
3. An operation name carrying a ``/`` → the JSON-RPC protocol-method shape. The
   two namespaces are matched as BARE STRINGS in one declaration
   (security.mdx @ v3.1.1 :1045-1059), so nothing but the shape keeps
   ``tasks/cancel`` out of ``required_for``.
4. A hand-listed metric vocabulary that misses a tool → that tool keeps working
   and its traffic silently demotes to ``"other"``.
5. ``sanitize_operation`` called at a CALL SITE rather than inside the recording
   helper → the second call site is the one that forgets.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import REPO_ROOT, parse_module, src_python_files

# --------------------------------------------------------------------------
# Classification allowlist — SHRINK-ONLY, and each entry states its reason
# --------------------------------------------------------------------------

#: Registry rows the pinned SDK's ``ADCP_TOOL_DEFINITIONS`` does not list. They
#: are ours to name and the SDK is only a cross-check, so they are allowlisted
#: rather than renamed or dropped. This list may only SHRINK — an SDK bump that
#: adds one removes it from here.
#:
#: ONE table, where the pre-merge branch had two. Its second table held surfaces
#: we exposed that were NOT AdCP operations — six A2A-only skills with no row
#: anywhere. #1721 has no such surface: ``registry.is_adcp_operation`` makes "is
#: there a row for it" and "is it an AdCP operation this seller implements" the
#: same question, so a name we serve is an operation by construction and the only
#: open question left is whether the pinned SDK happens to name it.
_SDK_UNLISTED_OPERATIONS: dict[str, str] = {
    "complete_task": (
        "task-completion tool; absent from ADCP_TOOL_DEFINITIONS at adcp==6.6.0. "
        "Reachable on all three transports via its registry row, so it is a legal "
        "required_for member and must be a bounded metric label."
    ),
}


# --------------------------------------------------------------------------
# The production registries, read from production
# --------------------------------------------------------------------------


def _sdk_operation_names() -> frozenset[str]:
    """The SDK leg — read from PRODUCTION's derivation, not re-derived here.

    ``src.core.signing.vocabulary.sdk_operation_names`` is one leg of the closed
    vocabulary that bounds the ``operation`` metric label, so a copy of it in this
    file would grade a set production does not use. That is the exact shape of the
    defect this epic already found once (a guard grading its own copy of the
    predicate under test), and it is not rebuilt here.

    Imported by dotted path: ``src/core/signing/__init__.py`` re-exports nothing,
    deliberately, which is what keeps the layer's import graph acyclic.
    """
    from src.core.signing.vocabulary import sdk_operation_names

    return sdk_operation_names()


def _registry_operation_names() -> frozenset[str]:
    """Every AdCP operation name this seller serves, from the one declaration.

    ``TOOLS`` is not one transport's view of the surface — MCP registration, the
    A2A card and dispatch, and the REST route table are all generated from it, so
    a row here is reachable on every transport that row enables and a name absent
    from it is reachable on none.
    """
    from src.core.tools.registry import TOOLS

    names = frozenset(TOOLS)
    assert names, "src.core.tools.registry.TOOLS is empty — this guard has gone dead"
    return names


def _rest_route_names() -> list[str]:
    """The ``name`` of every /api/v1 route.

    The derivation loop passes ``name=<registry key>``, so for a derived route the
    route name IS the operation name. A route added by hand carries its Python
    function's name instead, which is what
    :meth:`TestEveryServedNameIsClassified.test_no_rest_route_serves_an_unclassified_name`
    turns into a build failure.
    """
    from src.routes.api_v1 import router

    names = [route.name for route in router.routes]
    assert names, "no /api/v1 routes found — this guard has gone dead"
    return names


def _classified_names() -> frozenset[str]:
    """Every name production may legally emit as an ``operation``."""
    return _sdk_operation_names() | _registry_operation_names() | frozenset(_SDK_UNLISTED_OPERATIONS)


# --------------------------------------------------------------------------
# Every served name is classified
# --------------------------------------------------------------------------


class TestEveryServedNameIsClassified:
    """A name a transport can serve is either defined by the pinned SDK or is
    explicitly allowlisted here with its reason. An unclassified new tool fails
    the build rather than silently collapsing to ``"other"`` at the metric.
    """

    def test_no_registry_row_is_unclassified(self):
        unclassified = sorted(_registry_operation_names() - _sdk_operation_names() - set(_SDK_UNLISTED_OPERATIONS))

        assert unclassified == [], (
            f"registry rows that are neither in the SDK's ADCP_TOOL_DEFINITIONS nor classified "
            f"in this file: {unclassified}. Add each to _SDK_UNLISTED_OPERATIONS with the pinned "
            "SDK version and the reason we serve it anyway. Never leave one unclassified — an "
            "unrecognized name is recorded as 'other', the bucket whose whole job is to make an "
            "attacker-supplied name visible."
        )

    def test_no_rest_route_serves_an_unclassified_name(self):
        """REST is the one transport whose route table can still be written by
        hand: ``router.add_api_route`` takes any callable, and a ``@router.post``
        decorator beside the derivation loop would compile.

        A hand-written route names itself after its Python function — that name
        reaches ``serve`` and then the metric label, and it is not an operation.
        """
        unclassified = sorted(set(_rest_route_names()) - _classified_names())

        assert unclassified == [], (
            f"/api/v1 routes whose name is not a classified AdCP operation: {unclassified}. "
            "Routes are DERIVED from TOOLS (name=<registry key>); a route named after a Python "
            "function is a hand-written one that bypassed the registry, so the surface it serves "
            "is declared nowhere and graded by nothing."
        )

    def test_the_allowlist_carries_no_dead_entries(self):
        """Shrink-only means the entries must still describe something real."""
        dead = sorted(set(_SDK_UNLISTED_OPERATIONS) - _registry_operation_names())

        assert dead == [], (
            f"_SDK_UNLISTED_OPERATIONS entries matching no registry row: {dead}. A stale entry "
            "hides the fact that the surface moved — delete it."
        )

    def test_the_allowlist_only_holds_names_the_sdk_really_lacks(self):
        """The moment an SDK bump adds them, this list must shrink."""
        redundant = sorted(set(_SDK_UNLISTED_OPERATIONS) & _sdk_operation_names())

        assert redundant == [], (
            f"_SDK_UNLISTED_OPERATIONS entries the pinned SDK now defines: {redundant}. "
            "Remove them — the allowlist may only shrink."
        )


# --------------------------------------------------------------------------
# The two namespaces stay disjoint as bare strings
# --------------------------------------------------------------------------


class TestNamespacesStayDisjoint:
    """AdCP operation names carry no ``/``; JSON-RPC protocol methods do.

    That shape IS the separation (security.mdx @ v3.1.1 :1045-1059) — the two
    buckets are plain string lists in the same declaration, so nothing but the
    shape keeps ``tasks/cancel`` out of ``required_for``. Graded over the WHOLE
    classified vocabulary, which is what ``required_for`` is validated against;
    ``tests/unit/test_request_signing_namespace_split.py`` grades the refusal
    that vocabulary feeds.
    """

    def test_no_classified_operation_name_contains_a_slash(self):
        with_slash = sorted(name for name in _classified_names() if "/" in name)

        assert with_slash == [], (
            f"classified AdCP operation names containing '/': {with_slash}. That is the JSON-RPC "
            "protocol-method shape; the two namespaces are matched as bare strings and must not "
            "be able to collide — a collision makes one declaration silently grade the other's "
            "traffic."
        )


# --------------------------------------------------------------------------
# The operation label's closed set is DERIVED, and bounded inside the recorders
# --------------------------------------------------------------------------

#: The three ``src/core/metrics.py`` helpers that put ``operation`` on a Prometheus
#: counter. Each must bound the label ITSELF: a sanitizer applied at a call site is
#: a sanitizer that one call site forgets.
_OPERATION_RECORDERS = ("record_signature_verified", "record_signature_failed", "record_request_unsigned")

#: Names no registry serves, each one new Prometheus series for as long as the
#: process lives if recorded raw.
_UNSERVED_OPERATION_NAMES = (
    "create_media_buy\n",
    "../../etc/passwd",
    "sync_creatives; DROP TABLE",
    "a" * 512,
)


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str | None:
    """The name of the function whose body (transitively) contains *target*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
            child is target for child in ast.walk(node)
        ):
            return node.name
    return None


class TestTheOperationLabelIsBoundedByADerivedSet:
    """``operation`` is a Prometheus label, and an unbounded label is one new time
    series per distinct value in a long-running multi-tenant process.

    #1721 shrinks the hazard — the verifier runs inside ``_resolve_identity``,
    which ``invoke_tool`` reaches only after ``TOOLS[tool_name]`` resolved, so on
    the tool path the label is already a registry key — and the bound is kept
    anyway, for the reason ``src/core/signing/vocabulary.py`` states: the counters
    are also written from paths that record no tool at all, and a bound that exists
    only as an argument about reachability stops holding the day a caller changes.

    Two properties are graded, and they are the two ways the bound could be built
    wrong. FIRST: the closed set is DERIVED from the registry, so this guard reads
    production's vocabulary and drives it with the registry it reads independently
    — a hand-listed copy that misses a tool goes red here rather than silently
    demoting that tool's real traffic. SECOND: the bounding happens INSIDE the
    recording helpers, so no call site can bypass it.
    """

    def test_every_name_a_transport_can_serve_survives_the_sanitizer_verbatim(self):
        """The derivation half.

        A name a transport really serves must be recorded AS ITSELF — collapsing it
        to ``"other"`` would be an observability regression indistinguishable from
        an attack.
        """
        from src.core.metrics import sanitize_operation

        served = sorted(_registry_operation_names() | set(_rest_route_names()))
        collapsed = {name: sanitize_operation(name) for name in served if sanitize_operation(name) != name}

        assert collapsed == {}, (
            f"served operation names the metric label does not recognize: {collapsed}. "
            "src.core.signing.vocabulary.operation_label_names must DERIVE the closed set from "
            "TOOLS and the SDK definitions. A hand-written copy drifts silently: the tool keeps "
            "working and its traffic is filed under 'other', in the bucket that exists to make an "
            "unrecognized name visible."
        )

    def test_the_unnamed_sentinel_is_a_member_and_not_a_collapse(self):
        """``""`` is what a refusal raised BEFORE a tool was named records.

        Folding it into ``"other"`` would bury every such refusal in the bucket
        that exists to alarm on an unrecognized name, and would break the
        discriminator ``tests/integration/test_request_signature_operations.py``
        reads as proof that a request was not named as an operation.
        """
        from src.core.metrics import sanitize_operation

        assert sanitize_operation("") == "", (
            "the unnamed sentinel must survive as itself: it is one series, it is not "
            "caller-chosen, and the integration suite reads the empty label as proof that a "
            "request was refused before any operation was named"
        )

    @pytest.mark.parametrize("name", _UNSERVED_OPERATION_NAMES)
    def test_a_name_no_transport_serves_collapses(self, name):
        """The other side of the same coin — without this the test above passes
        for an identity function.
        """
        from src.core.metrics import sanitize_operation

        assert sanitize_operation(name) == "other", (
            f"{name!r} is not served by any transport, so it must collapse to the single bounded "
            "bucket; recorded verbatim it is one new Prometheus series per distinct value"
        )

    def test_every_sanitize_operation_call_in_src_is_inside_a_recording_helper(self):
        """The placement half, made unrepresentable rather than remembered.

        Three counters carry the label and each is incremented from more than one
        place, which is the standing proof that a sanitizer called at the call site
        is a sanitizer one call site forgets. So the only legal place to call it is
        inside the helpers themselves.
        """
        callers: set[str] = set()
        for path in sorted(src_python_files(REPO_ROOT)):
            tree = parse_module(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "sanitize_operation"
                ):
                    enclosing = _enclosing_function(tree, node) or "<module scope>"
                    callers.add(f"{path.relative_to(REPO_ROOT)}::{enclosing}")

        stray = sorted(key for key in callers if key.rsplit("::", 1)[1] not in _OPERATION_RECORDERS)
        missing = sorted(set(_OPERATION_RECORDERS) - {key.rsplit("::", 1)[1] for key in callers})

        assert missing == [], (
            f"recording helpers that do not bound their own operation label: {missing}. "
            "Each of src/core/metrics.py's three operation-labelled counters must call "
            "sanitize_operation itself."
        )
        assert stray == [], (
            f"sanitize_operation called outside the recording helpers: {stray}. Bounding at a "
            "call site is how the second call site gets missed; the helpers are the boundary."
        )
