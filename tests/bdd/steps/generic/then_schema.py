"""Generic Then steps that grade a response against the pinned AdCP schema.

``the response should be schema-valid against <file>`` lived at module scope in
tests/bdd/test_uc018_list_creatives.py, so only UC-018 could use it. Moving it here
is correct on its own merits — it overrides no generic text, so unlike the eight
deliberately module-scoped UC-019 steps there is nothing to keep it local.

It was NOT, however, why the UC-019 scenario was dormant, and this docstring used
to say it was. Measured: ``-k freshly`` reported ``2 xfailed`` with
``Step definition not found: Given "the buyer captured a media_buy_id from a
successful create_media_buy response"``. The blocker was the missing ``Given`` —
moving this ``Then`` would not have woken the scenario. That ``Given`` now exists
(``steps/domain/uc019_query_media_buys.py``), which is what actually woke it.

Grades the REAL WIRE. When a dispatcher stashed ``ctx["wire_response"]`` (REST's
HTTP body, MCP's structured_content, A2A's artifact DataPart) that is the document
a buyer actually receives, and validating it catches transport-framing regressions
that a re-serialized typed payload cannot.

Both steps read through ``wire_dict``, which RAISES when a real-wire transport
stashed nothing, rather than quietly re-serializing the typed payload. The
difference is the whole point: ``status`` is a model field with a default, so a
re-serialized payload carries it whether or not the envelope ever reached the
wire — an instrument that reports success precisely where it could not observe
what it was asked to grade. This module is registered globally, and that fallback
fired on ``then_envelope_status``, the step grading the obligation GH #1900 owns.

No ``exclude_none``: stripping literal nulls would mask exactly the regression
class a wire reader exists to catch, and ``confirmed_at`` reaches the wire as an
explicit null under the required-nullable contract.
"""

from __future__ import annotations

from pytest_bdd import parsers, then

from tests.bdd.steps._outcome_helpers import wire_dict, wire_error_envelope_or_none
from tests.helpers.pinned_schema import validator_for
from tests.helpers.response_schemas import branch_names, response_schema_ref, response_validator


def _dispatch_errored(ctx: dict) -> bool:
    """Whether the call under test was REFUSED rather than answered.

    ``TransportResult`` is the object that holds the outcome, so ask it. Falling
    back to ``ctx["error"]`` covers the steps that stash an exception directly.
    """
    result = ctx.get("result")
    if result is not None:
        return not result.is_success
    return ctx.get("error") is not None or ctx.get("wire_error_envelope") is not None


def _assert_compliant(ctx: dict, tool: str, branch: str | None) -> None:
    """Grade whatever the call actually produced, against the contract for THAT outcome.

    Outcome-aware on purpose, and this is the correction of a real mistake. The
    first version graded the response schema unconditionally, so it called
    ``wire_dict``, which asserts ``is_success``. Every scenario whose call was
    refused then failed with "expected a success wire body, got error ..." — a
    message about the harness, blaming a step that was working, for a request
    that was SUPPOSED to be refused. 345 scenarios failed that way in run
    1c81890872ef4fd9be44f6265ada9a44.

    The SCENARIO OUTLINE makes the unconditional version not merely inconvenient
    but impossible: one outline carries both ``Examples: Valid partitions`` and
    ``Examples: Invalid partitions``, so its single Then line is executed once
    per row against BOTH outcomes. No fixed choice of contract is right for every
    row. The contract has to follow the outcome, which is what this does.

    It does NOT weaken the grading, because it does not decide whether the
    outcome was correct — it only decides which contract applies to the outcome
    that happened. Whether the call SHOULD have succeeded is pinned by the
    scenario's own assertions sitting beneath this line, which is the whole point
    of a general check plus a specific one.
    """
    if _dispatch_errored(ctx):
        then_error_compliant(ctx)
        # Then grade the refusal against the TOOL's own error branch. Without this, the
        # delegation above discards both ``tool`` and ``branch``, so ``<tool> error spec``
        # and ``<tool> success spec`` were the SAME check on a refusal — asserting the
        # SUCCESS branch of an error response passed. The branch form's own docstring
        # calls itself "strictly stronger"; that held only for success outcomes until
        # this line, and the ``error`` branch form was used ZERO times out of 251.
        #
        # Conditional because not every tool branches: a single-shape response has no
        # error branch to narrow to, and ``response_validator`` raises rather than guess.
        # Measured across the whole BDD suite when added: 128 failing before, 128 after.
        # Nothing was wrong — nothing was checking.
        # Through the ONE guarded accessor, never getattr on the result: a second reader
        # in a step module is free to drift from the one the harness owns, which is what
        # test_no_hand_rolled_wire_envelope_access exists to stop. It caught this line.
        envelope = wire_error_envelope_or_none(ctx)
        if envelope is not None and "error" in branch_names(tool):
            branch_errors = sorted(
                response_validator(tool, "error").iter_errors(envelope),
                key=lambda e: list(e.absolute_path),
            )
            if branch_errors:
                detail = "\n".join(
                    f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in branch_errors
                )
                raise AssertionError(f"the refusal does not comply with the {tool} error branch:\n{detail}")
        return
    wire = wire_dict(ctx)
    errors = sorted(response_validator(tool, branch).iter_errors(wire), key=lambda e: list(e.absolute_path))
    if errors:
        where = f"{tool} {branch}" if branch else tool
        detail = "\n".join(f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors)
        raise AssertionError(
            f"the response does not comply with the {where} spec ({response_schema_ref(tool)}):\n{detail}"
        )


@then("the error is compliant with the AdCP error spec")
def then_error_compliant(ctx: dict) -> None:
    """Grade a REFUSAL against ``core/error.json``, so an error path is not exempt.

    THE RULE IS THAT EVERY SCENARIO CARRIES A COMPLIANCE CHECK. A scenario whose
    dispatch is refused has no response document to grade, and leaving it with
    none is how "the suite is schema-clean" comes to mean "the happy paths are".
    A refusal is still a wire contract: the boundary's ``AdcpErrorResponse`` serializes to
    ``{adcp_error, errors[], context}`` and every entry in ``errors[]`` is a
    ``core/error.json`` object, required ``code`` and ``message``.

    THIS IS THE LINE FOR A TOOL WITHOUT AN ERROR BRANCH. Where the pinned response
    schema branches (``create_media_buy``, ``update_media_buy``, ``sync_creatives``,
    ``sync_accounts`` carry an ``error`` title in their ``oneOf``), the canonical
    sentence is ``the response is compliant with the <tool> error spec`` -- it runs
    this same check and THEN grades the refusal against the tool's own error branch,
    so it is strictly stronger and the generic line under-claims. Every refusal of
    a branching tool was swept to it; what remains on this line is the read tools,
    whose response schema has one shape and no branch to narrow to.

    Tool-independent on purpose. The AdCP error vocabulary is OPEN — ``code`` is
    a wire-typed string, published codes are documentary, and a receiver decodes
    an unknown one by reading ``recovery`` — so there is nothing per-tool to
    resolve. WHICH code was emitted is a different obligation, graded by
    ``assert_wire_error``; this grades that the refusal is well-formed.

    DELEGATES the envelope parsing to ``TransportResult``, which owns the
    normalized envelope. This step originally reached for
    ``ctx["wire_error_envelope"]`` and reimplemented the entry extraction —
    ``test_no_hand_rolled_envelope_parsing`` caught it, correctly: a second
    parser in a step definition is free to drift from the one on the result
    object, and the two disagreeing about what an envelope contains is precisely
    how an error assertion goes quietly vacuous.
    """
    result = ctx.get("result")
    assert result is not None, (
        "no TransportResult on the context, so there is nothing to grade. This step "
        "belongs after a When that dispatched through a transport."
    )
    result.assert_wire_error_is_schema_conformant()


@then("the webhook payload is compliant with the AdCP delivery webhook spec")
def then_webhook_payload_compliant(ctx: dict) -> None:
    """Grade an OUTBOUND delivery webhook, the one wire a buyer sees that no tool returns.

    A webhook is the seller SENDING, so there is no response document and none of
    the response steps apply. That is exactly why it went ungraded, and why it is
    worth grading: ``next_expected_at`` is a field where a hand-written obligation
    once demanded an explicit ``null``, the pin says "Omitted on final
    notifications", and production shipped a schema-invalid null to buyers. A
    check here catches that class.

    The pinned contract is TWO LAYERS, and the chain is explicit in the pin rather
    than inferred: ``core/mcp-webhook-payload.json`` is the POST body; its
    ``result`` is ``$ref: async-response-data.json``, which resolves per
    ``task_type`` (``enums/task-type.json``) to
    ``media-buy/media-buy-delivery-webhook-result.json`` for
    ``media_buy_delivery``. That inner schema says so in its own description:
    "Payload-only delivery report result carried under
    core/mcp-webhook-payload.json result ... This is not a top-level webhook POST
    body and does not include protocol envelope".

    THE PROSE IS EXPLICIT, and this step was once weakened to drop the envelope
    check on the grounds that GH #2058 "had not decided". That reasoning was
    wrong twice: an internal issue is not the authority, and the spec had already
    ruled. ``webhooks.mdx:217`` — "Delivery-report content lives under ``result``;
    it is not valid as the top-level POST body by itself" — and ``:254`` prints
    the bare report as a LABELLED COUNTER-EXAMPLE: "This inner result object is
    valid delivery-report content, but it is not valid as the top-level webhook
    POST body".

    That counter-example WAS byte-for-byte what one of this seller's two senders
    posted, which is why fourteen scenarios carrying this line were ledgered.
    Both senders now build the body through ``build_webhook_envelope``
    (``src/core/webhooks/delivery.py``), so there is one shape, it is the
    envelope, and these scenarios grade it live.

    Both layers are graded, and the failure names WHICH layer broke — an envelope
    that never arrived is a different defect from a malformed report inside a
    correct envelope, and one message that cannot tell them apart sends the reader
    to the wrong file.

    Reads the body off the socket (``env.delivered_requests``), not a dict the
    sender kept. Grading what the sender believes it sent cannot catch a
    serialization that changes it.

    WHAT THIS CANNOT CATCH, measured rather than assumed: the inner schema sets
    ``additionalProperties: true``, so it ACCEPTS a result carrying
    ``aggregated_totals`` — which webhooks.mdx:253 forbids ("API-only for
    get_media_buy_delivery responses and must not be emitted in reporting
    webhook result payloads"). A prose MUST NOT that the schema does not encode
    is invisible to any schema check.

    That gap is upstream, not ours, and is now asked about there:
    adcontextprotocol/adcp#7329. The schema does not encode it at 3.1, at 3.1.20
    (``latest_stable``) or at 3.2.0-rc.1, and the generated SDK model is
    ``extra="allow"`` — it accepts ``aggregated_totals`` and round-trips it into
    its own output. Until that is resolved, do NOT tighten this step by
    hand-coding the prohibition here: a local rule that the pin does not carry
    is how a suite starts grading one seller's reading of the spec instead of
    the spec.

    That is the argument for the general check and a specific one sitting side
    by side rather than the general one replacing anything. The scenario that
    catches it is ``@T-UC-004-webhook-no-aggregated``, asserting the field's
    absence by name. Never delete a specific assertion on the grounds that a
    compliance line now covers the response — for prose obligations it does not.
    """
    deliveries = ctx["env"].delivered_requests
    assert deliveries, "no webhook POST was made, so there is no payload to grade"
    body = deliveries[-1].json()
    assert body, f"the webhook POST carried no JSON body: {deliveries[-1].body!r}"

    envelope_failures = sorted(
        validator_for("core/mcp-webhook-payload.json").iter_errors(body),
        key=lambda e: list(e.absolute_path),
    )
    if envelope_failures:
        detail = "\n".join(
            f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in envelope_failures
        )
        raise AssertionError(
            f"the webhook POST body is not a core/mcp-webhook-payload.json envelope "
            f"(it carries {sorted(body)}):\n{detail}"
        )

    result_failures = sorted(
        validator_for("media-buy/media-buy-delivery-webhook-result.json").iter_errors(body.get("result")),
        key=lambda e: list(e.absolute_path),
    )
    if result_failures:
        detail = "\n".join(
            f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in result_failures
        )
        raise AssertionError(
            f"the webhook envelope is well-formed but its result does not comply with "
            f"media-buy/media-buy-delivery-webhook-result.json:\n{detail}"
        )


@then(parsers.parse("the response is compliant with the {tool} spec"))
def then_response_compliant(ctx: dict, tool: str) -> None:
    """Grade the response the buyer received against the tool's pinned schema.

    The scenario names the TOOL, never a schema file. A filename in a feature
    file is a second spelling of "which tool is this scenario exercising", and
    the two drift silently — a scenario whose When changed keeps asserting the
    old contract and still passes.

    For a tool whose response branches, this grades the whole ``oneOf`` — "one of
    the legal shapes". That is weaker than naming a branch, and deliberately
    allowed only because the SCENARIO OUTLINE cannot name one: 104 scenarios end
    in ``the result should be <outcome>``, and a single outline carries both
    ``Examples: Valid partitions`` and ``Examples: Invalid partitions``, so no
    one line is right for every row. Refusing would leave those scenarios with
    no compliance check at all.

    Weaker is not vacuous, which was measured rather than assumed: against
    ``create-media-buy-response.json`` it rejects an empty object, an object of
    junk keys, a success document missing ``confirmed_at`` and ``revision``, a
    submitted document missing ``task_id``, and a success document whose
    ``status`` is misspelled.

    Where a scenario pins ONE outcome, use the branch form — it is strictly
    stronger, and this one would accept the branch the scenario says did NOT
    happen.
    """
    _assert_compliant(ctx, tool, None)


@then(parsers.parse("the response is compliant with the {tool} {branch} spec"))
def then_response_compliant_branch(ctx: dict, tool: str, branch: str) -> None:
    """Grade the response against ONE branch of a branching response.

    ``success``, ``error`` and ``submitted`` are the spec's own words, read from
    the schema's ``oneOf`` titles (``CreateMediaBuySuccess`` -> ``success``), so
    the vocabulary a scenario may use is the vocabulary the pin defines.
    """
    _assert_compliant(ctx, tool, branch)


#: ``the response should be schema-valid against <file>`` is DELETED. It named a
#: schema file, which is a second spelling of "which tool is this scenario
#: exercising" — and the two drift, silently, because a scenario whose When
#: changes keeps asserting the old contract and still passes. All 20 uses across
#: nine feature files now name the tool instead.


@then(parsers.parse("the response envelope carries status {expected_status}"))
def then_envelope_status(ctx: dict, expected_status: str) -> None:
    """Assert the protocol envelope's spec-required ``status`` is on the response.

    Scoped to the envelope rather than full-document validity on purpose: this is
    the obligation GH #1900 owns, and it is gradeable on any response whose schema
    composes core/protocol-envelope.json, independently of whether that response's
    domain body is complete.

    Parameterized on the status rather than hard-coding ``completed``: an exact-text
    step means the next scenario that needs a different terminal status has to invent
    a second sentence for the same obligation, which is how one obligation ends up
    with several phrasings and only one of them graded.
    """
    document = wire_dict(ctx)
    assert "status" in document, (
        f"AdCP 3.1.1 core/protocol-envelope.json marks 'status' REQUIRED on every task "
        f"response envelope, but the response carries only {sorted(document)}"
    )
    assert document["status"] == expected_status, (
        f"expected the envelope to report status {expected_status!r}, got {document['status']!r}"
    )
