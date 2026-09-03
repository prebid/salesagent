"""Authenticity guard for TransportResult.wire_response.

The UC-005 format_id federation-contract scenario asserts the ``{agent_url, id}``
object shape on ``wire_response`` for REST/A2A/MCP. That is only meaningful if
``wire_response`` carries the *real* serialized bytes rather than a re-serialization
of the already-validated typed payload — otherwise the wire assertions would be
tautological again (the typed payload can never be a bare string by construction).

These tests pin that contract against ``list_creative_formats`` so a future refactor
cannot quietly substitute a reconstruction. IMPL has no wire by definition.

MCP has no envelope-only markers (GH #1710): before that fix, the MCP wrapper
handed ``ToolResult`` the raw pydantic response object, which
FastMCP serializes via ``pydantic_core.to_jsonable_python()`` — bypassing
``AdCPBaseModel``'s ``exclude_none=True`` default, so unset optional fields
(``task_id``, ``adcp_version``) leaked onto the wire as ``null``. Those leaked
nulls were incidentally usable as "this must be real wire, not a reconstruction"
markers. The fix makes the MCP wrapper pass ``response.model_dump(mode="json")``
instead — the *same* call A2A/REST already use — so MCP's ``structured_content``
is now BYTE-IDENTICAL to a plain payload dump: there is no separate MCP envelope
layer, by design (FastMCP's ``structured_content`` IS the tool's typed output).
So the MCP authenticity check below asserts round-trip fidelity (a fabricated or
partial dict wouldn't parse back into the response type and re-dump identically)
rather than envelope-only-key presence.

``TransportResult.has_wire`` (#1802) extends the same guarantee one level down.
Wire-presence used to be INFERRED at the read site from a lookup miss keyed on
``Transport.IMPL``; the classes below pin the replacement: the dispatcher that
produced a result DECLARES, positively and at construction, whether the bytes
crossed a real wire, and the ``wire_field`` / ``wire_dict`` readers branch on
that declaration instead of on which transport enum happens to be in play.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.core.schemas import ListCreativeFormatsResponse
from tests.bdd.steps._outcome_helpers import wire_dict, wire_field
from tests.bdd.steps.generic.when_request import _call_via
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport, TransportResult


@pytest.mark.requires_db
class TestWireResponseIsRealWire:
    """wire_response surfaces the real serialized success-path wire, per transport."""

    # Envelope-only keys present only because A2A wraps the payload — absent
    # from a bare payload reconstruction and from the REST HTTP body. MCP has no
    # envelope-only keys anymore (see module docstring): its wire is checked
    # separately via round-trip fidelity.
    ENVELOPE_MARKERS = {
        Transport.A2A: ("success", "message"),
    }

    def test_rest_wire_response_is_the_http_body(self, integration_db):
        """REST wire_response is the actual HTTP JSON body (provenance check).

        REST serializes the payload directly, so wire_response == payload.model_dump();
        asserting == raw_response.json() therefore pins *provenance* (the field is the
        real HTTP response body), not a reconstruction-difference. Symmetrically, the
        bare HTTP body must NOT carry the A2A transport-envelope markers.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.REST)
            assert result.wire_response == result.raw_response.json()
            assert "formats" in result.wire_response
            for marker in (m for markers in self.ENVELOPE_MARKERS.values() for m in markers):
                assert marker not in result.wire_response, (
                    f"REST wire (bare HTTP body) unexpectedly carries envelope field {marker!r}"
                )

    def test_a2a_wire_carries_envelope_fields(self, integration_db):
        """A2A wire carries transport-envelope fields a payload reconstruction would lack.

        A payload model_dump() exposes only the response model's fields (formats,
        creative_agents, pagination, ...). The A2A envelope adds success/message
        (injected by ``_serialize_for_a2a``, not part of the response model) —
        asserting these makes the oracle distinguish real serialized wire from a
        reconstruction.
        """
        with CreativeFormatsEnv() as env:
            for transport, markers in self.ENVELOPE_MARKERS.items():
                result = env.call_via(transport)
                assert isinstance(result.wire_response, dict), f"{transport}: wire_response not a dict"
                assert "formats" in result.wire_response, f"{transport}: wire_response missing formats"
                for key in markers:
                    assert key in result.wire_response, (
                        f"{transport}: wire_response missing envelope field {key!r} — "
                        "looks like a payload reconstruction, not real wire"
                    )

    def test_mcp_wire_round_trips_through_the_response_type(self, integration_db):
        """MCP wire is a byte-faithful serialization of a valid response instance.

        MCP has no envelope-only markers to assert (see module docstring): its
        structured_content is now exactly ``response.model_dump(mode="json")``. A
        fabricated/partial reconstruction would either fail to construct
        ``ListCreativeFormatsResponse`` (missing/wrong-typed required fields) or
        fail to re-dump identically (extra/dropped/differently-shaped fields), so
        round-trip equality is the meaningful authenticity signal left post-fix.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.MCP)
        assert isinstance(result.wire_response, dict), "MCP: wire_response not a dict"
        assert "formats" in result.wire_response, "MCP: wire_response missing formats"
        reparsed = ListCreativeFormatsResponse(**result.wire_response)
        assert reparsed.model_dump(mode="json") == result.wire_response, (
            "MCP wire_response does not round-trip through ListCreativeFormatsResponse — "
            "looks like a fabricated/partial reconstruction, not real wire"
        )

    def test_impl_has_no_wire(self, integration_db):
        """IMPL is an in-process call — no wire by definition."""
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
            assert result.wire_response is None


# ── has_wire: the dispatcher-declared wire-presence predicate (#1802) ──────────────

_HARNESS_DIR = pathlib.Path(__file__).resolve().parents[1] / "harness"
_DISPATCHERS_PY = _HARNESS_DIR / "dispatchers.py"
_CLIENT_PY = _HARNESS_DIR / "client.py"
#: The dispatch seam — the modules that DECLARE wire-presence for a real
#: delivery. It is two modules, not one: #1858 relocated the transport-generic
#: UNWRAP bodies out of ``dispatchers.py`` into ``client.py``, which is how the
#: table below silently stopped covering nine of its sites. Every construction
#: in BOTH is graded per-site by ``EXPECTED_SITES``.
_SEAM_MODULES = (_DISPATCHERS_PY, _CLIENT_PY)
_STEPS_DIR = pathlib.Path(__file__).resolve().parents[1] / "bdd" / "steps"
_OUTCOME_HELPERS_PY = _STEPS_DIR / "_outcome_helpers.py"


def _transport_result_sites(module_path: pathlib.Path) -> dict[tuple[str, str, int], ast.Call]:
    """Map every ``TransportResult(...)`` construction to (module, owner, ordinal).

    ``owner`` is the dotted chain of enclosing class/function names —
    ``"A2ADispatcher.dispatch"``, ``"unwrap_rest_response"`` — or ``"<module>"``
    for a construction at module level, so a site hiding outside any def still
    gets a key and the sole-constructor scan below cannot miss it.

    The key is deliberately NOT a line number: a fix that inserts a keyword at
    every site would shift them all. Ordinal-within-owner (source order) is
    stable under kwarg insertion. The ordinal is scoped to the enclosing
    function rather than to the whole module on purpose: a module-wide ordinal
    would let a newly inserted site renumber every later one onto a neighbour's
    reason string, which can go green by coincidence when the neighbours share a
    value.
    """
    tree = ast.parse(module_path.read_text())
    owner: dict[ast.Call, str] = {}

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "TransportResult":
                owner[child] = ".".join(scope) or "<module>"
            walk(child, scope)

    walk(tree, ())

    sites: dict[tuple[str, str, int], ast.Call] = {}
    per_owner: dict[str, int] = {}
    for call in sorted(owner, key=lambda c: (c.lineno, c.col_offset)):
        name = owner[call]
        ordinal = per_owner.get(name, 0)
        per_owner[name] = ordinal + 1
        sites[(module_path.name, name, ordinal)] = call
    return sites


class TestHasWireIsDeclaredAtEveryConstructionSite:
    """Every TransportResult construction declares has_wire, correctly, PER SITE.

    Per SITE, never per transport (Design Amendment v2, A2): ``has_wire=True``
    only where the construction is downstream of an actual send/receive. Several
    constructions owned by a WIRE transport still return before any bytes move —
    the three catch-all error unwraps (``unwrap_mcp_error`` / ``unwrap_a2a_error``
    / ``unwrap_rest_error`` each wrap a whole delivery, so they can fire pre-send)
    and ``RestE2EDispatcher``'s missing-``e2e_config`` guard (ahead of every httpx
    call). Grading these "by transport" would re-freeze, one layer up in the pin,
    exactly the identity inference this lane deletes.

    The seam spans TWO modules (``_SEAM_MODULES``). #1858 moved the
    transport-generic UNWRAP bodies — the four ``unwrap_rest_response`` branches,
    the shared ``_unwrap_tool_success``, and the three catch-all error unwraps —
    out of ``dispatchers.py`` into ``tests/harness/client.py``. Neither file
    conflicted on the merge, so the table went stale without anyone editing it:
    that is precisely the silent-drift failure this class exists to make loud, so
    it grades both modules rather than the one that happened to hold the sites
    first.

    ``McpE2EDispatcher`` and ``A2AE2EDispatcher`` are absent on purpose (A3):
    they construct no TransportResult at all — they delegate to
    ``_dispatch_core``, which returns through client.py's sites below.
    """

    # (module, owner, ordinal-in-owner) -> (expected has_wire, why this SITE is what it is)
    EXPECTED_SITES: dict[tuple[str, str, int], tuple[bool, str]] = {
        # ── tests/harness/dispatchers.py — the per-transport dispatch entry points ──
        ("dispatchers.py", "ImplDispatcher.dispatch", 0): (
            False,
            "in-process _impl raised — no wire by definition",
        ),
        ("dispatchers.py", "ImplDispatcher.dispatch", 1): (
            False,
            "in-process _impl returned — no wire by definition",
        ),
        ("dispatchers.py", "A2ADispatcher.dispatch", 0): (
            True,
            "success, downstream of the A2A artifact DataPart capture",
        ),
        ("dispatchers.py", "McpDispatcher.dispatch", 0): (
            True,
            "success, downstream of the structured_content capture",
        ),
        ("dispatchers.py", "RestE2EDispatcher.dispatch", 0): (
            False,
            "missing env.e2e_config — a pure config error, ahead of every httpx call",
        ),
        # ── tests/harness/client.py — the transport-generic UNWRAP bodies (#1858) ──
        ("client.py", "_unwrap_tool_success", 0): (
            True,
            "MCP/A2A success — downstream of DELIVER, the structured_content / artifact DataPart already came back",
        ),
        ("client.py", "unwrap_rest_response", 0): (
            True,
            "non-JSON >= 400 body — the HTTP response was received, only the AdCP envelope is unrecoverable",
        ),
        ("client.py", "unwrap_rest_response", 1): (
            True,
            "structured >= 400 body — the real HTTP response was received",
        ),
        ("client.py", "unwrap_rest_response", 2): (
            True,
            "parse failure on an already-received 2xx response — the wire happened, the harness-side parse did not",
        ),
        ("client.py", "unwrap_rest_response", 3): (True, "2xx success — the real HTTP JSON body"),
        ("client.py", "unwrap_mcp_error", 0): (
            False,
            "catch-all wrapping the whole MCP delivery — can fire before any bytes move (the STRADDLE case: "
            "it still hands back the REAL envelope it recovered)",
        ),
        ("client.py", "unwrap_a2a_error", 0): (
            False,
            "catch-all wrapping the whole A2A delivery — can fire before any bytes move (STRADDLE, as above)",
        ),
        ("client.py", "unwrap_rest_error", 0): (
            False,
            "REST DELIVER exception — no HTTP body existed at all, so nothing crossed the wire",
        ),
    }

    #: Modules OUTSIDE the seam that construct a TransportResult as TEST INPUT:
    #: a fabricated result fed to a reader or a step, not a dispatcher declaring
    #: what its own delivery did. Their has_wire is chosen to model the state
    #: under test, so grading it against "did bytes actually move" would pin a
    #: falsehood — hence an exemption rather than a table row. Named file by
    #: file, never by directory or glob: a construction in any OTHER module
    #: fails the scan below and must be tabled above or added here with its
    #: reason. This mapping may shrink; it must never grow silently.
    FIXTURE_CONSTRUCTORS: dict[str, str] = {
        "tests/integration/test_harness_wire_response.py": (
            "this module fabricates results to grade wire_field/wire_dict against a known declaration"
        ),
        "tests/unit/test_bdd_uc006_storyboard_dispatch_fault_is_not_xfail.py": (
            "mutation grader: fabricates the ctx an injected REST 500 leaves behind (has_wire=True — that "
            "body did come back over HTTP) and drives every UC-006 storyboard Then step through it"
        ),
    }

    def test_the_seam_is_still_the_only_constructor(self):
        """The per-site table is exhaustive only while the seam is the sole declarer.

        Two-directional: an unknown constructor means the table stopped covering
        every construction (the #1858 drift), and a listed file that constructs
        nothing means the exemption is stale and must be deleted.
        """
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        seam = {str(path.relative_to(repo_root)) for path in _SEAM_MODULES}
        allowed = seam | set(self.FIXTURE_CONSTRUCTORS)
        constructors = {
            str(path.relative_to(repo_root))
            for path in repo_root.glob("tests/**/*.py")
            if _transport_result_sites(path)
        }
        assert sorted(constructors - allowed) == [], (
            f"TransportResult is constructed outside the seam {sorted(seam)}: "
            f"{sorted(constructors - allowed)}. The per-site has_wire table above no longer covers every "
            "construction — table the new sites, or, if they are test fixtures rather than dispatch "
            "declarations, name the module in FIXTURE_CONSTRUCTORS with its reason."
        )
        assert sorted(allowed - constructors) == [], (
            f"listed as a TransportResult constructor but constructs none: {sorted(allowed - constructors)}. "
            "Remove the stale entry so the exemption list keeps shrinking."
        )

    def test_every_site_is_in_the_table(self):
        """No site may be added or removed without a per-site has_wire decision."""
        found = sorted(key for module in _SEAM_MODULES for key in _transport_result_sites(module))
        assert found == sorted(self.EXPECTED_SITES), (
            f"TransportResult construction sites across the seam changed: {found}. "
            "Every site needs an explicit per-site has_wire decision in EXPECTED_SITES."
        )

    def test_every_site_declares_has_wire_explicitly_and_correctly(self):
        """has_wire is passed as a literal at every site, with the value that SITE earns."""
        sites = {key: call for module in _SEAM_MODULES for key, call in _transport_result_sites(module).items()}
        declared: dict[tuple[str, str, int], object] = {}
        for key, call in sites.items():
            where = f"{key[1]} site #{key[2]} ({key[0]}:{call.lineno})"
            kwarg = next((kw for kw in call.keywords if kw.arg == "has_wire"), None)
            assert kwarg is not None, (
                f"{where} does not pass has_wire. "
                "Wire-presence is DECLARED at construction — no site may rely on a default."
            )
            assert isinstance(kwarg.value, ast.Constant) and isinstance(kwarg.value.value, bool), (
                f"{where} passes a computed has_wire ({ast.dump(kwarg.value)}); "
                "it must be a literal True/False decided per site."
            )
            declared[key] = kwarg.value.value

        expected = {key: value for key, (value, _) in self.EXPECTED_SITES.items()}
        assert declared == expected, "\n".join(
            f"{key[1]} site #{key[2]} ({key[0]}:{sites[key].lineno}): "
            f"has_wire={declared[key]!r}, expected {expected[key]!r} — {self.EXPECTED_SITES[key][1]}"
            for key in sorted(expected)
            if declared.get(key) != expected[key]
        )


class TestHasWireIsRequiredAtConstruction:
    """has_wire is a required keyword-only field: omitting it is a TypeError.

    A defaulted field is not a declaration (Design Amendment v2, A1). Omitting the
    kwarg would yield ``has_wire=False``, which routes the readers to the
    production serializer — a wire-shape assertion then passes vacuously against a
    ``model_dump``. A forgetful 14th site must fail at construction, not go green.
    """

    def test_omitting_has_wire_raises_type_error(self):
        with pytest.raises(TypeError, match="has_wire"):
            TransportResult(payload=None)

    def test_has_wire_cannot_be_passed_positionally(self):
        """Keyword-only: the declaration is always spelled out at the call site."""
        field_names = [f.name for f in TransportResult.__dataclass_fields__.values()]  # type: ignore[attr-defined]
        assert "has_wire" in field_names
        assert TransportResult.__dataclass_fields__["has_wire"].kw_only is True, (
            "has_wire must be keyword-only so every construction spells the declaration out"
        )


@pytest.mark.requires_db
class TestDispatchersDeclareHasWireOnRealDispatch:
    """The declaration survives a real dispatch — not just the source text."""

    @pytest.mark.parametrize("transport", [Transport.REST, Transport.A2A, Transport.MCP])
    def test_wire_transports_declare_a_wire_and_carry_one(self, transport, integration_db):
        with CreativeFormatsEnv() as env:
            result = env.call_via(transport)
        assert result.has_wire is True, f"{transport}: success dispatch did not declare has_wire"
        assert result.wire_response is not None, (
            f"{transport}: declared has_wire but stashed no wire_response — harness bug, not a no-wire result"
        )

    def test_impl_declares_no_wire(self, integration_db):
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
        assert result.has_wire is False, "IMPL is an in-process call — it must declare no wire"
        assert result.wire_response is None


@pytest.mark.requires_db
class TestWireReadersBranchOnTheDeclaration:
    """wire_field/wire_dict decide from the result's own predicate, not the transport enum.

    The ctx dicts below carry NO ``transport`` key on purpose: that is the state of
    every scenario tagged @rest/@mcp/@a2a (those are not parametrized —
    tests/bdd/conftest.py ``ctx``), where today's predicate silently disables
    itself and lets a stash-miss serialize the typed payload instead.
    """

    @pytest.fixture()
    def payload(self, integration_db):
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.IMPL)
        assert result.payload is not None
        return result.payload

    def test_readers_raise_when_a_declared_wire_was_not_stashed(self, payload):
        """has_wire with no wire_response is a harness bug — raise, never serialize."""
        result = TransportResult(payload=payload, wire_response=None, has_wire=True)
        ctx = {"result": result, "response": payload, "wire_response": None}
        for reader, args in ((wire_field, (ctx, "formats")), (wire_dict, (ctx,))):
            with pytest.raises(AssertionError, match="wire_response"):
                reader(*args)

    def test_readers_return_the_stashed_wire_not_a_reserialization(self, payload):
        """A declared wire is read from the wire, even where it differs from model_dump."""
        stashed = {"formats": [{"format_id": {"agent_url": "https://x.test", "id": "sentinel"}}]}
        assert stashed != payload.model_dump(mode="json")
        result = TransportResult(payload=payload, wire_response=stashed, has_wire=True)
        ctx = {"result": result, "response": payload, "wire_response": stashed}
        assert wire_dict(ctx) == stashed
        assert wire_field(ctx, "formats") == stashed["formats"]

    def test_readers_use_the_production_serializer_only_when_no_wire_is_declared(self, payload):
        """has_wire=False is the ONLY path onto the serializer."""
        result = TransportResult(payload=payload, wire_response=None, has_wire=False)
        ctx = {"result": result, "response": payload, "wire_response": None}
        serialized = payload.model_dump(mode="json")
        assert wire_dict(ctx) == serialized
        assert wire_field(ctx, "formats") == serialized["formats"]

    def test_readers_raise_when_no_transport_result_was_stashed(self, payload):
        """No TransportResult in ctx means the When step bypassed both dispatch seams.

        The diagnostic must surface the recorded ``ctx['error']``: both seams end in
        ``except Exception as exc: ctx['error'] = exc`` WITHOUT setting ctx['result'],
        so a dispatch that THREW lands here and must not be misdiagnosed as "never
        dispatched".
        """
        ctx = {"response": payload, "error": RuntimeError("boom-from-dispatch")}
        for reader, args in ((wire_field, (ctx, "formats")), (wire_dict, (ctx,))):
            with pytest.raises(AssertionError, match="boom-from-dispatch"):
                reader(*args)


class TestWirePresenceIsNeverInferredFromTransportIdentity:
    """The symptom-patch detector: has_wire must REPLACE the identity inference.

    If has_wire exists but the readers still consult ``ctx["transport"]`` (or the
    private uc003 copy survives), the root — inference from transport identity —
    was not removed.
    """

    def test_wire_readers_contain_no_transport_identity_check(self):
        tree = ast.parse(_OUTCOME_HELPERS_PY.read_text())
        readers = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in ("wire_field", "wire_dict")
        }
        assert sorted(readers) == ["wire_dict", "wire_field"], (
            f"readers missing from _outcome_helpers: {sorted(readers)}"
        )
        for name, node in readers.items():
            transport_reads = [
                ast.dump(n)
                for n in ast.walk(node)
                if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "Transport")
                or (isinstance(n, ast.Constant) and n.value == "transport")
            ]
            assert transport_reads == [], (
                f"{name} still decides wire-presence from transport identity: {transport_reads}. "
                "The predicate belongs to the TransportResult, not to the enum."
            )

    def test_no_step_module_keys_behavior_on_transport_impl(self):
        offenders = [
            f"{path.relative_to(_STEPS_DIR)}:{lineno}"
            for path in sorted(_STEPS_DIR.glob("**/*.py"))
            for lineno, line in enumerate(path.read_text().splitlines(), start=1)
            if "Transport.IMPL" in line
        ]
        assert offenders == [], (
            f"tests/bdd/steps/ still keys behavior on Transport.IMPL: {offenders}. "
            "Transport.IMPL is being deleted (GH #1802-uc004/1210); a positive, "
            "dispatcher-declared predicate survives that removal unchanged."
        )


@pytest.mark.requires_db
class TestBothDispatchSeamsStashTheTransportResult:
    """Both seams stash ctx["result"] — the readers' precondition.

    ``dispatch_request`` already does. ``when_request._call_via`` (the seam UC-005
    dispatches through, including its literal @a2a/@mcp/@rest When steps) does not
    yet, so without it the readers would have no TransportResult to branch on
    exactly where the transport key is also absent.
    """

    @pytest.mark.parametrize("transport", ["rest", "a2a", "mcp"])
    def test_call_via_stashes_the_transport_result(self, transport, integration_db):
        with CreativeFormatsEnv() as env:
            ctx: dict = {"env": env}
            _call_via(ctx, transport)
            assert "error" not in ctx, f"{transport}: dispatch failed: {ctx.get('error')!r}"
            result = ctx.get("result")
            assert isinstance(result, TransportResult), (
                f"{transport}: when_request._call_via did not stash ctx['result'] "
                "— the wire readers have no declaration to branch on"
            )
            assert result.has_wire is True
