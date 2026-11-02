"""``MediaBuyListEnv`` must actually capture the MCP wire it declares.

This env was the last one in ``tests/harness/`` still dispatching through
``_run_mcp_wrapper``, which calls the module function directly. A failure becomes an
``AdCPToolError`` only in ``RegistryTool.run`` (``src/core/main.py``), so the wrapper
path never raises one, never stashes an envelope, and the dispatcher captures ``None``
— while the env goes on declaring ``has_wire=True``.

That combination is one the harness itself calls a bug and raises on loudly
(``tests/harness/transport.py``), so it cannot be left to a BDD scenario to
notice: the two live UC-019 ``[mcp]`` scenarios pass at HEAD through the
documented typed-exception fallback, which is precisely the weak grading this
pins. The obligation is a harness-contract one, so it is graded here, through
the public ``call_via`` surface — the same way
``test_request_validation_suggestion_parity.py`` pins its own.
"""

import pytest

from tests.harness.media_buy_list import MediaBuyListEnv
from tests.harness.transport import Transport


@pytest.mark.requires_db
class TestMediaBuyListMcpWireCapture:
    def test_an_mcp_rejection_exposes_the_two_layer_wire_envelope(self, integration_db):
        """The rejection a real MCP buyer receives, not one rebuilt from the exception.

        Asserted on the envelope's SHAPE. ``is not None`` would pass on any dict,
        including one a builder regenerated — the substitution this whole lane
        exists to make impossible.

        The rejection is raised OUTSIDE ``_impl``, and that is the point: FastMCP's
        ``TypeAdapter`` rejects the enum at the schema boundary before ``_impl``
        runs, and ``RequestCompatMiddleware`` translates that into the two-layer
        error. A buyer calling this tool over MCP gets exactly this. Grading
        ``_impl``'s wording here would grade a rejection no MCP client ever sees.

        THE CODE. A pydantic ``ValidationError`` is by construction a SCHEMA
        constraint violation, and AdCP 3.1.1 ``enums/error-code.json`` splits on
        exactly that: ``INVALID_REQUEST`` is "malformed, missing required fields,
        or violates schema constraints"; ``VALIDATION_ERROR`` is "invalid field
        values or violates business rules BEYOND schema validation". A value
        outside a declared enum is the former. ``adcp_error_for()``
        (``src/core/exceptions.py``) implements the split, testing
        ``ValidationError`` before ``ValueError`` precisely so a schema-boundary
        rejection does not fall through to the business-rule code.

        THE CHANNELS. Under ADR-010 the buyer-facing ``message`` is a read-only
        ``CODE_TABLE`` property, so the accepted-value list no longer travels in
        prose — it travels structurally on ``issues[]``, the channel the pin
        defines for field-level rejection ("``field`` (singular) cannot carry the
        full pointer map", v3.1.1 ``core/error.json``). The two facts this test has
        always cared about are therefore asserted where they now live: WHICH field
        on ``field``, and WHICH values are accepted on ``issues[].keyword_value``.
        """
        from src.core.errors.codes import CODE_TABLE
        from tests.factories import PrincipalFactory, TenantFactory

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            result = env.call_via(Transport.MCP, status_filter="not_a_status")

        assert result.is_error, f"expected a rejection, got payload: {result.payload!r}"
        result.assert_wire_error(
            "INVALID_REQUEST",
            recovery="correctable",
            require_suggestion=True,
            # The rejected field failed the `enum` keyword specifically, not merely
            # "something was wrong" — the keyword IS the machine-readable reason.
            issues=[{"keyword": "enum"}],
        )

        adcp_error = result.wire_error_envelope["adcp_error"]
        assert adcp_error["field"].startswith("status_filter"), (
            f"the rejection must name the field the buyer got wrong, got {adcp_error['field']!r}"
        )

        # ADR-010: `message` is CODE_TABLE's, so it is asserted by identity with the
        # table rather than against a literal. Kept non-vacuous by pinning that the
        # OTHER side of the schema/business-rule split says something different — an
        # equality-only check would pass for whichever code production emitted.
        assert adcp_error["message"] == CODE_TABLE["INVALID_REQUEST"].message, (
            f"adcp_error.message={adcp_error['message']!r}, expected the first-party sentence "
            f"{CODE_TABLE['INVALID_REQUEST'].message!r}"
        )
        assert CODE_TABLE["INVALID_REQUEST"].message != CODE_TABLE["VALIDATION_ERROR"].message, (
            "the two halves of the schema/business-rule split must not advise the buyer identically"
        )

        # The obligation that used to read `"pending_creatives" in message`, on the
        # channel that carries it now. `keyword_value` is the keyword's constraint —
        # for `enum`, the accepted set — so the buyer still learns which values ARE
        # valid, structurally instead of by parsing English.
        issues = result.wire_error_issues("INVALID_REQUEST")
        enum_issues = [issue for issue in issues if issue.get("keyword") == "enum"]
        assert enum_issues, f"the enum rejection must reach the buyer on issues[], got {issues!r}"
        assert "pending_creatives" in (enum_issues[0].get("keyword_value") or ""), (
            f"the rejection must tell the buyer which values ARE valid, got "
            f"keyword_value={enum_issues[0].get('keyword_value')!r}"
        )

    def test_a_successful_mcp_call_exposes_the_wire_response_it_declares(self, integration_db):
        """``has_wire=True`` is a promise about the success path too.

        The env declared it and delivered ``None``. Grading only the error path
        would leave the declared-but-uncaptured success wire with no grader at
        all, which is the same defect wearing the other face.
        """
        from tests.factories import PrincipalFactory, TenantFactory

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            result = env.call_via(Transport.MCP)

        assert not result.is_error, f"expected success, got error: {result.error!r}"
        assert result.wire_response is not None, (
            "MediaBuyListEnv declares has_wire=True, so a successful MCP dispatch must "
            "carry the wire response it promised, not None"
        )
        assert "media_buys" in result.wire_response
