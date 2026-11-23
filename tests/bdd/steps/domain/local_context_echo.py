"""Steps for local-context-echo-every-outcome.feature.

The scenarios grade ONE obligation across five outcomes: whatever leaves the
seller carries the buyer's `context` object unchanged. The pinned authority and
the upstream gap are stated in the feature file's header; this module is only
the wiring.

THE DOCUMENT IS A FACTORY BASELINE, PERTURBED ONE FIELD AT A TIME. Each tool's
request factory (``tests/factories/request.py``) builds the conformant baseline,
and ``payload(**overrides)`` applies the scenario's perturbations AFTER the model
dump, so a wrong-typed field or an omitted one reaches the wire as written -- a
model could not be constructed from the bytes these scenarios send. The Givens
accumulate overrides; the When builds the document from the factory the routing
tag's tool names and hands it to ``dispatch_request(ctx, **payload)``
(``tests/bdd/steps/generic/_dispatch.py``), the one keyword-bag seam every
scenario dispatches through.

``tests/unit/test_architecture_harness_single_dispatch.py`` is not touched by
any of this: what it bans is a per-env ``call_mcp``/``call_a2a`` override, a
``deliver_*`` override outside its shrink-only allowlist, and the deleted
``_last_wire_response`` stash. A raw keyword bag is none of those — it is the
existing seam's existing parameter.
"""

from __future__ import annotations

from uuid import uuid4

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict, wire_error_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.mint import mint
from tests.factories.request import (
    OMIT,
    CreateMediaBuyRequestFactory,
    GetMediaBuysRequestFactory,
    GetProductsRequestFactory,
)

#: The ctx slot the Givens accumulate their perturbations into, applied over the
#: factory baseline at the When. Named rather than reusing a generic key so
#: nothing else in the tree can write it: the whole point of these scenarios is
#: that the bytes the buyer sent are the bytes the seller saw.
_OVERRIDES = "ctxecho_overrides"

#: The factory for each tool a scenario can name; the routing tag decides the tool.
_FACTORY_BY_TOOL = {
    "get_products": GetProductsRequestFactory,
    "get_media_buys": GetMediaBuysRequestFactory,
    "create_media_buy": CreateMediaBuyRequestFactory,
}

#: The ctx slot holding the context object as SENT. The Then compares the wire
#: against this, so a seller that returns a context of its own invention fails
#: rather than satisfying a presence check.
_SENT = "ctxecho_sent_context"

#: The per-package context objects as SENT, in request order. Separate from
#: ``_SENT`` because the two are different obligations on different objects: the
#: envelope's is written by one seam for every outcome, an element's is carried
#: by whoever builds the element. A scenario that conflated them could pass on
#: the envelope echo while every package came back bare.
_SENT_PACKAGES = "ctxecho_sent_package_contexts"


# ── Given ────────────────────────────────────────────────────────────


@given("the buyer's request carries an opaque context object")
def given_request_carries_context(ctx: dict) -> None:
    """Put a free-form context bag on the outbound request, and remember it.

    Three kinds of member on purpose, because ``core/context.json`` is
    ``additionalProperties: true`` with zero declared properties and the pin
    says the object is preserved "byte-for-byte without parsing":

    * ``correlation_id`` — the member the storyboard's own
      ``field_value path:"context.correlation_id"`` checks read, minted unique
      per scenario so no two scenarios can satisfy each other's assertion;
    * ``buyer_ref`` — an UNDECLARED scalar, which a seller that re-validated
      the object against a model would drop;
    * ``trace`` — an UNDECLARED nested object, which a seller that flattened or
      normalized the bag would reshape.

    A scenario that sent only ``correlation_id`` could not tell "echoed
    unchanged" from "reconstructed with the one field the seller knows about".
    """
    sent = {
        "correlation_id": mint(f"ctxecho-{uuid4().hex}"),
        "buyer_ref": "context-echo-lane",
        "trace": {"span": "outer", "depth": 1},
    }
    ctx[_SENT] = sent
    ctx.setdefault(_OVERRIDES, {})["context"] = sent


@given("the request names a brief the seller can answer")
def given_answerable_brief(ctx: dict) -> None:
    """The one search criterion ``get_products`` requires of a caller: the baseline's own brief."""
    ctx.setdefault(_OVERRIDES, {})["brief"] = GetProductsRequestFactory.payload()["brief"]


@given("the request names no search criterion at all")
def given_no_search_criterion(ctx: dict) -> None:
    """Send neither brief, brand nor filters — a well-formed document the SELLER refuses.

    Asserts the absence on the BUILT document rather than trusting the overrides: a
    sibling Given that added a brief would silently turn this scenario into a success
    and the error assertions would then grade nothing.
    """
    overrides = ctx.setdefault(_OVERRIDES, {})
    overrides.update(brief=OMIT, brand=OMIT, filters=OMIT)
    payload = GetProductsRequestFactory.payload(**overrides)
    assert not any(k in payload for k in ("brief", "brand", "filters")), (
        f"the scenario claims no search criterion, but the request still carries one: {payload!r}"
    )


@given("the request carries a context that is not an object")
def given_context_not_an_object(ctx: dict) -> None:
    """A string where ``core/context.json`` declares an object.

    Deliberately does NOT record it under ``_SENT``: there is no object to echo, and
    the scenario grades that the rejection goes out WITHOUT the key rather than with
    something the seller invented.
    """
    ctx.setdefault(_OVERRIDES, {})["context"] = "not-an-object"


@given("each package in the request carries its own opaque context object")
def given_packages_carry_context(ctx: dict) -> None:
    """Put a DISTINCT random bag on every package, and remember them positionally.

    Random, and different per package, for the reason the envelope Given is
    three-membered: a seller that echoed a constant, echoed package 0's object
    onto every package, or rebuilt the bag from the members it recognises would
    satisfy any weaker check. Nothing here is a value production can derive --
    ``buyer_ref`` is not a declared field anywhere in AdCP, it is an extra inside
    an ``additionalProperties: true`` object with zero declared properties -- so
    the only way the assertion below passes is that the bytes were carried.

    The nested object and the list matter too: a seller that flattened, sorted or
    JSON-normalized the bag reshapes them while leaving the scalars intact.
    """
    baseline = _FACTORY_BY_TOOL["create_media_buy"].payload()
    packages = [dict(p) for p in baseline["packages"]]
    assert packages, "the create_media_buy baseline carries no packages — nothing to grade the element echo on"

    # Name the rows the route's seed actually created. The factory baseline carries its
    # own product_id/pricing_option_id literals, which no seed creates, so the create is
    # refused PRODUCT_NOT_FOUND before a package exists and the echo grades nothing.
    product = ctx.get("default_product")
    pricing_option = ctx.get("default_pricing_option")
    assert product is not None and pricing_option is not None, (
        "the route's seed did not stash default_product / default_pricing_option — "
        "this scenario needs the full create chain"
    )

    sent: list[dict] = []
    for index, package in enumerate(packages):
        package["product_id"] = product.product_id
        package["pricing_option_id"] = pricing_option.pricing_option_id
        bag = {
            "correlation_id": mint(f"ctxecho-pkg-{index}-{uuid4().hex}"),
            "buyer_ref": f"line-{uuid4().hex[:8]}",
            "trace": {"span": uuid4().hex[:6], "depth": index},
            "tags": [uuid4().hex[:4], uuid4().hex[:4]],
        }
        package["context"] = bag
        sent.append(bag)

    ctx[_SENT_PACKAGES] = sent

    # Written into ``request_kwargs``, NOT this module's ``_OVERRIDES``. The
    # create_media_buy When belongs to ``uc002_create_media_buy`` -- one sentence,
    # one definition -- and its default path dispatches ``ctx["request_kwargs"]``
    # flat through the parametrized transport. Feeding that seam is how this
    # scenario reaches the wire without a second copy of a step that exists.
    payload = dict(baseline)
    payload["packages"] = packages
    if _SENT in ctx:
        payload["context"] = ctx[_SENT]

    # The account the `the request targets a production account` Given seeded. The
    # baseline factory names one of its own, which no seed creates, so the create is
    # refused ACCOUNT_NOT_FOUND before a package is ever built and the echo grades
    # nothing. Same handoff the uc002 full-create branch performs.
    account_ref = ctx.get("account_ref")
    if account_ref is not None:
        payload["account"] = account_ref.model_dump(mode="json", exclude_none=True)

    ctx["request_kwargs"] = payload


@given("the request carries a brief of the wrong JSON type")
def given_brief_wrong_type(ctx: dict) -> None:
    """An integer where ``get-products-request.json`` declares a string.

    Refused on the bytes' own merits, so the seller owes INVALID_REQUEST. The
    value reaches the wire unrepaired — that is the scenario's whole subject.
    """
    ctx.setdefault(_OVERRIDES, {})["brief"] = 12345


@given("the request carries media_buy_ids of the wrong JSON type")
def given_media_buy_ids_wrong_type(ctx: dict) -> None:
    """A bare string where ``get-media-buys-request.json`` declares an array."""
    ctx.setdefault(_OVERRIDES, {})["media_buy_ids"] = "mb_not_an_array"


@given(parsers.parse('the request pins AdCP version "{version}"'))
def given_pins_adcp_version(ctx: dict, version: str) -> None:
    """Pin a release on the request envelope.

    Well-formed against the request model's pattern
    (``^\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$``) on purpose: the document must be
    ACCEPTED so that the refusal comes from version negotiation and not from
    schema validation, or the scenario would grade the schema-rejection path
    twice under two names.
    """
    ctx.setdefault(_OVERRIDES, {})["adcp_version"] = version


# ── When ─────────────────────────────────────────────────────────────


def _send(ctx: dict, *, tool: str) -> None:
    """Dispatch the accumulated document through ``ctx['transport']``.

    One body for both When steps: which tool runs is the ENV's declaration
    (``MCP_TOOL`` / ``A2A_SKILL`` / ``REST_ENDPOINT``), selected by the
    scenario's routing tag in ``tests/bdd/conftest.py``, so the two sentences
    differ only in what they name and not in what they do. A second copy of
    this body is the substituted-variable shape the DRY invariant forbids.

    *tool* is what the sentence CLAIMS is being sent, and it is checked against
    the env the routing tag actually produced. Without the check the sentence
    would be decorative: a scenario tagged ``@ctxecho-media-buys`` while saying
    "sends the get_products request" would dispatch get_media_buys and grade
    something other than what it reads as.

    A Given's ``ctx["credential"]`` is forwarded by ``dispatch_request`` itself —
    ``given_buyer_no_auth`` writes a token-less one for the token-less scenarios, and an
    empty dict is a MEANINGFUL value there (dispatch with no headers), which is why the
    presence of the key decides rather than its truthiness.
    """
    env = ctx["env"]
    assert env.MCP_TOOL == tool, (
        f"the scenario says it sends {tool!r}, but its routing tag selected "
        f"{type(env).__name__}, which dispatches {env.MCP_TOOL!r}"
    )
    assert _OVERRIDES in ctx, (
        "no perturbation was recorded — a Given must run before the When, or the scenario "
        "would dispatch the bare baseline and grade the seller's answer to nothing it claims to send"
    )
    payload = _FACTORY_BY_TOOL[tool].payload(**ctx[_OVERRIDES])
    dispatch_request(ctx, **payload)


@when("the Buyer Agent sends the get_products request")
def when_send_get_products(ctx: dict) -> None:
    """Dispatch the document as a get_products call."""
    _send(ctx, tool="get_products")


@when("the Buyer Agent sends the get_media_buys request")
def when_send_get_media_buys(ctx: dict) -> None:
    """Dispatch the document as a get_media_buys call."""
    _send(ctx, tool="get_media_buys")


# NO `create_media_buy` When here. ``uc002_create_media_buy.when_send_create_media_buy``
# already owns that sentence, and a second definition of it is exactly the duplication
# ``test_architecture_bdd_no_duplicate_steps`` exists to stop. The package-context
# scenario feeds that step's own seam instead: it writes ``ctx["request_kwargs"]``, which
# ``_dispatch_full_create`` dispatches flat through the parametrized transport.


# ── Then ─────────────────────────────────────────────────────────────


def _assert_echo(ctx: dict, envelope: dict, *, outcome: str) -> None:
    """Assert *envelope* carries the context object the buyer SENT, unchanged.

    *envelope* always arrives from a guarded accessor — :func:`wire_dict` or
    :func:`wire_error_dict` — so this function never decides WHERE the wire
    comes from, and there is no reconstruction it could fall back to. The one
    read it does perform is of ``context``, a TOP-LEVEL key that
    ``core/protocol-envelope.json`` declares at the envelope root and that the
    harness has no locator for (unlike the error code, whose location
    ``assert_wire_error`` owns and which this module never re-derives — the
    ``the error recovery should be "..."`` line in every error scenario routes
    the SHAPE check through that one authority).

    Exact equality, not a subset and not a field probe. The pin's obligation is
    "preserve byte-for-byte without parsing", so a seller that added a member,
    dropped the undeclared ones, or reshaped the nested object has not met it —
    and each of those passes a presence check.
    """
    sent = ctx.get(_SENT)
    assert sent is not None, "no context object was stashed — the Given must run before the Then"
    echoed = envelope.get("context")
    assert echoed == sent, (
        f"the {outcome} response did not echo the buyer's context object unchanged.\n"
        f"  sent:    {sent!r}\n"
        f"  echoed:  {echoed!r}\n"
        f"  envelope keys: {sorted(envelope)}"
    )


@then("the successful response echoes the buyer's context object unchanged")
def then_success_echoes_context(ctx: dict) -> None:
    """The success wire carries the sent context object verbatim.

    ``wire_dict(ctx)`` raises when no SUCCESS wire was captured, so this step
    also pins that the dispatch succeeded — an error outcome cannot reach the
    comparison and quietly satisfy it off the error envelope.
    """
    _assert_echo(ctx, wire_dict(ctx), outcome="successful")


@then("the error response echoes the buyer's context object unchanged")
def then_error_echoes_context(ctx: dict) -> None:
    """The two-layer error envelope carries the sent context object verbatim.

    ``wire_error_dict(ctx)`` raises when no ERROR wire was captured, so this
    step pins the outcome the same way its success twin does.
    """
    _assert_echo(ctx, wire_error_dict(ctx), outcome="error")


@then("every created package echoes its own context object unchanged")
def then_packages_echo_context(ctx: dict) -> None:
    """Each returned package carries back the bag ITS request package sent.

    Positional, and one assertion per package rather than a set comparison: the
    obligation is that package *i* gets package *i*'s object, and a seller that
    returned the right objects against the wrong packages has not met it while a
    set comparison would say it had.

    The count is asserted first. A seller that returned FEWER packages than were
    asked for would otherwise pass this vacuously on the ones it did return --
    the same vacuity the envelope Then avoids by comparing the whole object
    rather than probing one member.
    """
    sent = ctx.get(_SENT_PACKAGES)
    assert sent is not None, "no package contexts were stashed — the Given must run before the Then"

    envelope = wire_dict(ctx)
    returned = envelope.get("packages")
    assert isinstance(returned, list), (
        f"the success response carries no packages array to read an element echo off: keys={sorted(envelope)}"
    )
    assert len(returned) == len(sent), (
        f"sent {len(sent)} package(s) and got {len(returned)} back, so the positional echo "
        f"cannot be graded: {returned!r}"
    )

    for index, (package, expected) in enumerate(zip(returned, sent, strict=True)):
        echoed = package.get("context")
        assert echoed == expected, (
            f"packages[{index}] did not echo its own context object unchanged.\n"
            f"  sent:   {expected!r}\n"
            f"  echoed: {echoed!r}\n"
            f"  package keys: {sorted(package)}"
        )


@then("the response carries no packages array")
def then_response_carries_no_packages(ctx: dict) -> None:
    """The Submitted branch answers with no element to echo into.

    ``create-media-buy-response.json``'s Submitted variant declares task_id and a
    message; ``media_buy_id`` and ``packages`` land on the task's completion artifact.
    Asserting the ABSENCE is what stops the element-echo scenario's twin from being
    read as "this branch echoes too" -- there is nothing here to echo, and a seller that
    started returning packages on this branch would owe the element echo and get graded
    for it by the other scenario, not silently by this one.
    """
    envelope = wire_dict(ctx)
    assert "packages" not in envelope, (
        f"the submitted-for-approval response carried a packages array: {envelope['packages']!r}"
    )


@then("the error response carries no context object")
def then_error_carries_no_context(ctx: dict) -> None:
    """The rejection went out WITHOUT the key: nothing echoed, nothing invented.

    A context the seller could not model is dropped, not serialized as ``null`` and
    not replaced by an object of the seller's own; the buyer's real fault is what the
    body reports.
    """
    envelope = wire_error_dict(ctx)
    assert "context" not in envelope, f"a context the seller could not model reached the wire: {envelope['context']!r}"
