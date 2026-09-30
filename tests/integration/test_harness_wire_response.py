"""Authenticity guard for TransportResult.wire_response.

The UC-005 format_id federation-contract scenario asserts the ``{agent_url, id}``
object shape on ``wire_response`` for REST/A2A/MCP. That is only meaningful if
``wire_response`` carries the *real* serialized bytes rather than a re-serialization
of the already-validated typed payload — otherwise the wire assertions would be
tautological again (the typed payload can never be a bare string by construction).

These tests pin that contract against ``list_creative_formats`` so a future refactor
cannot quietly substitute a reconstruction. Every ``Transport`` member now dispatches
over a real wire: the in-process ``IMPL`` pseudo-transport and its dispatcher are
deleted, because a direct ``_impl`` call is not a transport and modelling it as one
gave every "assert on the wire" rule an escape hatch. Calling ``_impl`` directly is
still correct — through ``env.call_impl(...)``, whose result is a typed DTO and is
graded as one. Since ``98f925f35`` no reader may paper over a missing wire either:
the ``model_dump`` fallback that let a no-wire result satisfy a wire assertion is
deleted, so a success-path read on a wire-less result RAISES, and
``test_readers_never_fall_back_to_the_production_serializer`` below is what keeps
that fallback gone.

NO transport has envelope-only markers any more, and that is the contract, not an
inconvenience. ``src/core/tools/_wire.py::to_wire`` is the one function that turns a
response model into a body, and all three transports call it: it is
``response.model_dump(mode="json")`` and nothing else. MCP puts the result in
``ToolResult.structured_content``, A2A in an artifact ``DataPart``, REST returns it as
the HTTP body — those containers are the only thing that differs. A2A used to stamp
``message`` and ``success`` INTO the body after dumping it; ``success`` is deleted (the
pinned 3.1 tree declares it on no response schema of our fourteen tools), and ``message``
is a DECLARED field on every response model, inherited from ``adcp.types.ProtocolEnvelope``
and filled by the implementation — so it now rides on all three transports, REST included,
which never emitted it before.

That leaves the three wire-authenticity checks below asserting, instead of
envelope-only-key presence: *provenance* — the captured wire is this very run's payload
dump — *cross-transport agreement* — the three bodies are byte-identical, which a
reconstruction assembled per transport would not be — and *round-trip fidelity* — a
fabricated or partial dict wouldn't parse back into the response type and re-dump
identically.

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

    #: Deleted by ``to_wire``, and asserted absent everywhere below. A2A used to
    #: write it into every buyer's payload from the ``errors`` field; the pinned 3.1
    #: tree declares a ``success`` property on three response schemas, none of them
    #: one of our fourteen tools, so it was a key AdCP does not define. A buyer
    #: derives it from ``errors``, which they already have.
    NON_SPEC_KEY = "success"

    def test_rest_wire_response_is_the_http_body(self, integration_db):
        """REST wire_response is the actual HTTP JSON body (provenance check).

        REST serializes the payload through ``to_wire``, so wire_response ==
        payload.model_dump(mode="json"); asserting == raw_response.json() therefore
        pins *provenance* (the field is the real HTTP response body), not a
        reconstruction-difference.

        ``message`` is asserted here because REST is the transport that never emitted
        it. It is a declared envelope field (``core/protocol-envelope.json``, inherited
        via ``adcp.types.ProtocolEnvelope``) that the implementation fills, so it is
        payload and rides the HTTP body like any other field — and equality with the
        typed payload's own ``message`` is what says the body carries the run's value
        rather than a sentence some wrapper re-derived.
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.REST)
            assert result.wire_response == result.raw_response.json()
            assert "formats" in result.wire_response
            assert result.payload is not None, "REST: no typed payload captured"
            assert result.wire_response["message"] == result.payload.message, (
                "REST body's message diverged from the response model's — "
                f"wire={result.wire_response.get('message')!r} payload={result.payload.message!r}"
            )
            assert self.NON_SPEC_KEY not in result.wire_response, (
                f"REST wire carries {self.NON_SPEC_KEY!r}, a key AdCP declares on no tool response"
            )

    def test_a2a_wire_carries_envelope_fields(self, integration_db):
        """A2A's body is the declared protocol envelope, and it AGREES with the others.

        A2A used to be the transport with keys of its own: it dumped the response and
        then wrote ``message`` (from ``str(response)``) and ``success`` (from ``errors``)
        into the body, so one response object produced three different documents. Both
        stamps are gone — ``success`` deleted, ``message`` a declared field the
        implementation fills — so the assertion that means something now is the opposite
        one: the three bodies are the SAME bytes.

        That equality is also what makes this a wire-authenticity check without any
        envelope-only marker to point at. A harness that reconstructed each transport's
        body from the typed payload would have to reconstruct all three identically, off
        one dispatch each, including ``message`` and the envelope defaults.
        """
        with CreativeFormatsEnv() as env:
            bodies = {
                transport: env.call_via(transport) for transport in (Transport.A2A, Transport.REST, Transport.MCP)
            }

        for transport, result in bodies.items():
            assert isinstance(result.wire_response, dict), f"{transport}: wire_response not a dict"
            assert "formats" in result.wire_response, f"{transport}: wire_response missing formats"
            assert result.payload is not None, f"{transport}: no typed payload captured"
            assert result.wire_response["message"] == result.payload.message, (
                f"{transport}: body's message diverged from the response model's — "
                f"wire={result.wire_response.get('message')!r} payload={result.payload.message!r}"
            )
            assert self.NON_SPEC_KEY not in result.wire_response, (
                f"{transport}: wire carries {self.NON_SPEC_KEY!r}, a key AdCP declares on no tool response"
            )

        a2a, rest, mcp = (bodies[t].wire_response for t in (Transport.A2A, Transport.REST, Transport.MCP))
        assert a2a == rest, "A2A body diverged from REST's — to_wire is supposed to be the only producer"
        assert a2a == mcp, "A2A body diverged from MCP's — to_wire is supposed to be the only producer"

    def test_mcp_wire_is_the_serialized_payload(self, integration_db):
        """MCP wire_response equals payload.model_dump(mode="json") (provenance check).

        MCP's ToolResult.structured_content is built by ``mcp_result`` calling
        ``to_wire`` — the same call REST and A2A make — so no transport has a
        wrapper-only envelope key left to distinguish its body from a
        reconstruction. Exact equality
        with the payload's own serialization IS the authenticity check here: a
        harness bug that captured wire_response from a different source (e.g.
        re-serializing the typed payload with different kwargs, or stashing a stale
        value) would diverge from this.

        Complements ``test_mcp_wire_round_trips_through_the_response_type``: this
        one pins *provenance* (same run, same object), that one pins *fidelity*
        (the wire is a complete, valid instance of the response type).
        """
        with CreativeFormatsEnv() as env:
            result = env.call_via(Transport.MCP)
            assert isinstance(result.wire_response, dict), "MCP: wire_response not a dict"
            assert result.payload is not None, "MCP: no typed payload captured"
            assert result.wire_response == result.payload.model_dump(mode="json"), (
                "MCP wire_response diverged from payload.model_dump(mode='json') — "
                "structured_content may no longer be sourced from the real wire"
            )

    def test_mcp_wire_round_trips_through_the_response_type(self, integration_db):
        """MCP wire is a byte-faithful serialization of a valid response instance.

        No transport has envelope-only markers to assert (see module docstring): every
        body is exactly ``to_wire(response)``. A
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


# ── has_wire: the dispatcher-declared wire-presence predicate (#1802) ──────────────

_HARNESS_DIR = pathlib.Path(__file__).resolve().parents[1] / "harness"
_DISPATCHERS_PY = _HARNESS_DIR / "dispatchers.py"
_CLIENT_PY = _HARNESS_DIR / "client.py"
_RAW_WIRE_PY = _HARNESS_DIR / "raw_wire.py"
#: The dispatch seam — the modules that DECLARE wire-presence for a real
#: delivery. #1858 relocated the transport-generic UNWRAP bodies out of
#: ``dispatchers.py`` into ``client.py``, which is how the table below silently
#: stopped covering nine of its sites; ``raw_wire.py`` is the third member, the
#: sender for a document the client cannot shape (a body that is not JSON, a name
#: the registry lacks). Every construction in all three is graded per-site by
#: ``EXPECTED_SITES``.
_SEAM_MODULES = (_DISPATCHERS_PY, _CLIENT_PY, _RAW_WIRE_PY)
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

    That is also why the table survived ``Transport.IMPL``'s removal by dropping
    its two ``ImplDispatcher`` rows and nothing else: the four wire-transport sites
    above already carry the ``has_wire=False`` decision on their own merits, so the
    predicate never depended on there being a no-wire transport to point at.

    The seam spans TWO modules (``_SEAM_MODULES``). #1858 moved the
    transport-generic UNWRAP bodies — the four ``unwrap_rest_response`` branches,
    the shared ``_unwrap_tool_success``, and the three catch-all error unwraps —
    out of ``dispatchers.py`` into ``tests/harness/client.py`` (where the two
    unrecoverable->400 branches have since been DRY'd into ``_rest_transport_fault``,
    which is why that helper owns a row of its own). Neither file
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
        # TWO sites, because the A2A leg moved out of the dispatcher method and into the
        # module-level ``a2a_transport_result`` helper, which declares ``has_wire`` per
        # construction site rather than once for the function: the success leg has the
        # artifact DataPart in hand, the registration/refusal leg never reached the wire.
        # Ordinals follow the SOURCE order in the current dispatchers.py (success at :201,
        # registration at :210), not the order the pre-merge branch happened to write them.
        ("dispatchers.py", "a2a_transport_result", 0): (
            True,
            "success, downstream of the A2A artifact DataPart capture",
        ),
        ("dispatchers.py", "a2a_transport_result", 1): (
            False,
            "registration/refusal leg — nothing was delivered, so no wire to claim",
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
        ("client.py", "_rest_transport_fault", 0): (
            True,
            "a >= 400 HTTP response WAS received; its body simply carries no recoverable AdCP envelope. One "
            "helper for both such bodies (not JSON at all / JSON naming no code), so the two former sites "
            "collapsed into this one",
        ),
        ("client.py", "unwrap_rest_response", 0): (
            True,
            "structured >= 400 body — the real HTTP response was received",
        ),
        ("client.py", "unwrap_rest_response", 1): (
            True,
            "parse failure on an already-received 2xx response — the wire happened, the harness-side parse did not",
        ),
        ("client.py", "unwrap_rest_response", 2): (True, "2xx success — the real HTTP JSON body"),
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
        # ── tests/harness/raw_wire.py — a document sent as written (REST goes through client.py) ──
        ("raw_wire.py", "_protocol_refusal", 0): (
            False,
            "the transport's own refusal (JSON-RPC error, MCP ToolError with no envelope) — no AdCP body "
            "came back, and on the in-process legs the refusal fires before any bytes move",
        ),
        ("raw_wire.py", "_a2a_task_result", 0): (
            False,
            "failed A2A Task — the error convention shared with unwrap_a2a_error; the envelope it recovered "
            "is still the REAL artifact",
        ),
        ("raw_wire.py", "_a2a_task_result", 1): (
            True,
            "completed A2A Task — downstream of the artifact DataPart capture, as _unwrap_tool_success",
        ),
        ("raw_wire.py", "_mcp", 0): (
            False,
            "MCP ToolError carrying an envelope — the error convention shared with unwrap_mcp_error",
        ),
        ("raw_wire.py", "_mcp", 1): (
            True,
            "MCP success — downstream of the structured_content capture, as McpDispatcher.dispatch",
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
            "this module fabricates results to grade wire_field/wire_dict against a known declaration. "
            "It does NOT use tests/harness/wire_fixtures.py and must not: its sites are SUCCESS-path "
            "(payload + wire_response), not error envelopes; one is TransportResult(payload=None) inside "
            "pytest.raises(TypeError), a site that exists TO NOT go through a helper; and this is the guard "
            "module for the contract, so constructing through the helper it guards would be grading itself"
        ),
        "tests/harness/wire_fixtures.py": (
            "THE fabricator. Six modules used to hand-roll the same construction and each held a row here; "
            "they now call wire_error_result() and this is the single site. has_wire is derived from whether "
            "an envelope was captured, because across all seventeen former call sites the declaration was "
            "never independent of it — see that module's docstring, including the escape for a future site "
            "that genuinely needs an inconsistent pair"
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
    kwarg would yield ``has_wire=False`` — the value that says "these bytes never
    crossed a wire", which no site may claim by forgetfulness. It used to be worse
    still: ``False`` routed the readers to the production serializer, so a
    wire-shape assertion passed vacuously against a ``model_dump``. That fallback
    is gone (see ``test_readers_never_fall_back_to_the_production_serializer``),
    but a forgetful new site must still fail at construction, not go green.
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


@pytest.mark.requires_db
class TestWireReadersBranchOnTheDeclaration:
    """wire_field/wire_dict decide from the result's own predicate, not the transport enum.

    The ctx dicts below carry NO ``transport`` key on purpose: that is the state of
    every scenario tagged @rest/@mcp/@a2a (those are not parametrized —
    tests/bdd/conftest.py ``ctx``), where the old predicate silently disabled
    itself and let a stash-miss serialize the typed payload instead.

    They carry no ``response`` key either, for a different reason: it is retired.
    ``_dispatch._populate_ctx_from_result`` is the single writer of the post-dispatch
    ctx contract and no longer publishes it, so a fixture that set one would model a
    state the harness cannot produce.
    """

    @pytest.fixture()
    def payload(self, integration_db):
        """A real typed response, obtained by calling ``_impl`` directly.

        ``env.call_impl`` is the direct in-process call — it returns the DTO, not a
        ``TransportResult``, precisely because a direct call is not a transport. The
        fabricated ``TransportResult``s below then pair that real payload with a
        chosen ``has_wire`` declaration, which is the state under test.
        """
        with CreativeFormatsEnv() as env:
            response = env.call_impl()
        assert response is not None
        return response

    def test_readers_raise_when_a_declared_wire_was_not_stashed(self, payload):
        """has_wire with no wire_response is a harness bug — raise, never serialize."""
        result = TransportResult(payload=payload, wire_response=None, has_wire=True)
        ctx = {"result": result, "wire_response": None}
        for reader, args in ((wire_field, (ctx, "formats")), (wire_dict, (ctx,))):
            with pytest.raises(AssertionError, match="bypassed the real pipeline"):
                reader(*args)

    def test_readers_return_the_stashed_wire_not_a_reserialization(self, payload):
        """A declared wire is read from the wire, even where it differs from model_dump."""
        stashed = {"formats": [{"format_id": {"agent_url": "https://x.test", "id": "sentinel"}}]}
        assert stashed != payload.model_dump(mode="json")
        result = TransportResult(payload=payload, wire_response=stashed, has_wire=True)
        ctx = {"result": result, "wire_response": stashed}
        assert wire_dict(ctx) == stashed
        assert wire_field(ctx, "formats") == stashed["formats"]

    def test_readers_never_fall_back_to_the_production_serializer(self, payload):
        """NO declaration routes a success-path read onto ``model_dump`` — not even has_wire=False.

        The stricter successor to "has_wire=False is the ONLY path onto the
        serializer" (#1802's original rule). ``98f925f35`` deleted that last
        fallback outright: serializing the typed payload where no wire was captured
        turns a wire assertion into a serializer round-trip — the vacuous pass this
        whole module exists to prevent, and the reason ``has_wire`` was introduced
        in the first place (GH #1744 was the narrower fix for the same hazard).

        The state fabricated here — ``has_wire=False``, a real typed payload, no
        wire — is the one shape that used to reach the serializer, and it must now
        raise. Nothing produces it any more (the in-process pseudo-transport that
        did is deleted, and a direct ``env.call_impl`` call returns a bare DTO), so
        it is fabricated here to keep the reader's refusal graded.
        """
        result = TransportResult(payload=payload, wire_response=None, has_wire=False)
        ctx = {"result": result, "wire_response": None}
        for reader, args in ((wire_field, (ctx, "formats")), (wire_dict, (ctx,))):
            with pytest.raises(AssertionError, match="bypassed the real pipeline"):
                reader(*args)

    def test_readers_raise_when_no_transport_result_was_stashed(self):
        """No TransportResult in ctx means the When step bypassed both dispatch seams.

        The diagnostic must surface the recorded ``ctx['error']``: a dispatch that
        THREW lands here with the exception recorded and no ``ctx['result']``, and
        must not be misdiagnosed as "never dispatched".
        """
        ctx = {"error": RuntimeError("boom-from-dispatch")}
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


@pytest.mark.requires_db
class TestBothDispatchSeamsStashTheTransportResult:
    """Both seams stash ctx["result"] — the readers' precondition.

    ``dispatch_request`` already does. ``when_request._call_via`` (the seam UC-005
    dispatches through, including its literal @a2a/@mcp/@rest When steps) reaches it
    through the shared ``_populate_ctx_from_result``; without that the readers would
    have no TransportResult to branch on exactly where the transport key is also
    absent, so this pins the delegation rather than trusting it.
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
