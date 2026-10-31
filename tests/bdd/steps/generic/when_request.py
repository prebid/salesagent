"""When steps — dispatch requests through the CreativeFormatsEnv harness.

Every step calls production code directly. No stub mode.

Steps store results in ctx:
    ctx["result"] — the TransportResult; read its payload via require_payload
    ctx["error"] — Exception on failure
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, when

from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.harness.transport import Transport

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


# ── Helpers ──────────────────────────────────────────────────────────


def _call(ctx: dict, req: ListCreativeFormatsRequest | None = None, **extra: Any) -> None:
    """Dispatch through ctx['transport'] (a wire transport: a2a/mcp/rest).

    IMPL was dropped from the BDD default parametrization (#1417), so a
    missing transport is a wiring bug — fail loudly rather than bypassing the wire.
    """
    transport = ctx.get("transport")
    if transport is None:
        raise RuntimeError(
            "when_request._call: ctx['transport'] is unset. BDD scenarios must dispatch "
            "through a wire transport (a2a/mcp/rest); the IMPL call_impl fallback was removed."
        )
    _call_via(ctx, transport, req=req, **extra)


def _call_via(
    ctx: dict, transport: str | Transport, req: ListCreativeFormatsRequest | None = None, **extra: Any
) -> None:
    """Dispatch through *transport* — an ADAPTER over :func:`dispatch_request`.

    The body that used to live here (its own transport map, its own per-transport
    ``req`` shaping, its own call to ``env.call_via``) is gone. It was the second of
    three step-level dispatch entries, and being a second entry is what let the
    malformation gate cover one path of three (salesagent-99w2t) and what would have
    let a payload capture measure a subset of the traffic. What remains is the
    signature its six call sites use.

    Three differences went with the fold; each was measured before it was made:

    TRANSPORT. ``dispatch_request`` reads ``ctx['transport']`` through the single
    normalizer ``_as_transport``, which casefolds and accepts every ``Transport``
    member; this function took the transport as an ARGUMENT and mapped it with a
    private, case-sensitive ``{"a2a","mcp","rest"}`` dict. Writing the argument into
    ``ctx`` is not a translation, it is the truth: of 335 recorded dispatches through
    this seam, the argument equalled ``ctx['transport']`` in 335, and the seven step
    functions that passed a LITERAL transport were deleted as unbound in b59d4cbbc.
    The one caller with no ``ctx['transport']`` at all
    (``tests/integration/test_harness_wire_response.py``, which builds ``{"env": env}``)
    means to dispatch through the transport it names, and now says so in the key the
    normalizer reads.

    ``req`` SHAPING. The MCP branch used to flatten ``req.model_dump(exclude_none=True)``
    into the kwargs; ``dispatch_request`` forwards ``req=`` unchanged. That is
    equivalent for both remaining ``req``-passing callers, because both dispatch on
    ``CreativeFormatsEnv``, whose ``deliver_mcp`` override routes to ``_run_mcp_client``
    — which pops ``req`` and performs the IDENTICAL ``model_dump(exclude_none=True)``
    with the same "explicit kwargs win" precedence (tests/harness/_base.py). Deleting
    the shaping is also what makes the capture transport-INDEPENDENT: with it, the same
    scenario recorded ``format_ids[].agent_url`` on MCP and ``req.format_ids[].agent_url``
    on a2a/rest — a different key path and a different value type for 335 events — and
    ``compare_payloads.py``'s transport-twin tolerance would have been comparing unlike
    with unlike. The equivalence is PINNED in both halves, because it does NOT hold for
    an env on the base client-core path (which would send ``{"req": <model>}`` as the
    MCP arguments): ``tests/unit/test_bdd_dispatch_seam.py`` pins that ``req`` travels
    whole out of here, and ``tests/integration/test_bdd_dispatch_seam.py`` sends a real
    ``format_ids`` filter through all three transports and requires the seller to honour
    it — which it cannot if ``req`` stopped reaching the tool as arguments.

    THE GATE AND THE CAPTURE now run here, which is the point. Predicted cost: zero new
    failures — all 144 dispatches carrying a top-level ``creatives`` list already arrive
    through ``dispatch_request`` (salesagent-ryzil.2).
    """
    ctx["transport"] = transport
    if req is not None:
        extra["req"] = req
    dispatch_request(ctx, **extra)


def _call_raw(ctx: dict, **payload: Any) -> None:
    """Dispatch the LITERAL payload — no ``ListCreativeFormatsRequest`` in the way.

    THE NEGATIVE-PATH DISPATCH. Use this whenever the scenario expects the
    request to be REJECTED, so the SELLER performs the validation and the buyer
    receives a real wire envelope.

    Why this exists next to :func:`_call`, which is still correct for the
    positive path — the two are not redundant:

    ``_call`` builds the typed request in the TEST PROCESS. For a payload the
    model accepts that is harmless. For a payload the model REJECTS it is fatal
    to the test's meaning: pydantic raises here, the old ``except Exception:
    ctx["error"] = exc`` stashed a client-side exception, and production was
    never executed. The scenario then proved something about the MODEL and
    nothing about the SERVER — so transport framing, boundary translation and
    *which code each transport actually emits* were all ungraded, which is the
    class of defect that produced #1858's four accidental finds
    (salesagent-prkv.9/.35/.37/.49). prkv.33 measured the blast radius: all 86
    UC-005 instances recorded ``dispatched=False``.

    WHY ``dispatch_request`` AND NOT ``dispatch_via_client``. Both are raw-payload
    seams, but the client one would MASK the outcome this migration exists to
    expose. ``AdCPTestClient``'s UNWRAP parses a success wire into
    ``spec_response_model("list_creative_formats")`` — the PINNED response — and
    this env's real format-registry wire does not satisfy it (measured: 2520
    errors, e.g. ``formats.N.assets.M.max_count`` required by the pinned Assets
    variants but absent from ours). ``CreativeFormatsEnv`` carries a documented
    JUSTIFIED OVERRIDE of ``deliver_mcp``/``deliver_a2a`` for exactly that reason
    and parses with the LOCAL subclass instead.

    That gap is a schema-conformance issue graded elsewhere, not a dispatch
    defect — but it decides the seam here: if a payload the scenario expects to be
    REJECTED is in fact ACCEPTED (a graduation, which prkv.33 predicts for roughly
    two thirds of these rows), the client seam would fail to parse the success and
    report a confusing envelope error, hiding the graduation behind a fake
    failure. ``env.call_via`` keeps the env's parser, so an unexpected success
    reads as a plain success and the graduation is legible.

    The positive branches stay on ``_call``/``_call_via`` for the ordinary reason
    that they already work; nothing about them needed changing.
    """
    dispatch_request(ctx, **payload)


# ── Transport-NAMING steps: deleted, and not to be re-added ──────────
#
# Seven ``@when`` steps lived here — four calling ``_call_via(ctx, "a2a"/"mcp")``
# with a LITERAL transport and three delegating to those — under phrasings like
# "the Buyer Agent sends a list_creative_formats task via A2A" and "... calls
# list_creative_formats MCP tool". All seven bound ZERO scenarios: measured by
# rendering every Scenario Outline Examples row of all 46 feature files through
# pytest-bdd's own ``ScenarioTemplate.render`` and matching each of the 49531
# resulting step lines with pytest-bdd's own ``StepParser.is_matching`` — 0 hits
# for each of the seven, against two positive controls that both read BOUND
# (one of them a sentence that exists only AFTER Examples substitution, so a
# literal grep of the feature sources finds it 0 times).
#
# They were also the only callers that passed a transport differing in TYPE from
# ``ctx["transport"]``, which is why deleting them matters beyond dead weight:
# every surviving ``_call_via`` caller now passes what ``ctx["transport"]``
# already holds. And per rule 1 of tests/CLAUDE.md's BDD authoring discipline, a
# ``When`` that names a transport is a defect unless it grades a spec-cited
# transport-specific behavior — these graded none; the harness parametrizes the
# transport-neutral phrasings below over a2a/mcp/rest already.


# ── Generic format request (transport-agnostic) ──────────────────────


@given("the Buyer Agent calls list_creative_formats without filters")
@when(
    parsers.re(
        r"the Buyer Agent (?:requests the format catalog"
        r"|requests all formats with no filters"
        r"|sends a list_creative_formats request)"
    )
)
def when_request_unfiltered(ctx: dict) -> None:
    """Unfiltered list_creative_formats dispatch.

    Serves both the generic 'requests/sends a list_creative_formats request'
    When phrasings and the UC-005 baseline Given 'the Buyer Agent calls
    list_creative_formats without filters' (single canonical dispatch step).

    Names the account an earlier Given seeded (``ctx["account_ref"]``, written by "the
    request targets a sandbox/production account"), because that is what makes the
    sentence true: sandbox.mdx makes sandbox account-level -- "Once a request references
    a sandbox account, the entire request is treated as sandbox" -- so a request that
    names no account is a production request whatever rows exist.
    """
    _call(ctx, **_named_account(ctx))


def _named_account(ctx: dict) -> dict[str, Any]:
    """``{"account": <ref>}`` for the account an earlier Given seeded, else nothing."""
    account = ctx.get("account_ref")
    return {"account": account} if account is not None else {}


@when("the Buyer Agent sends a list_creative_formats request with invalid dimension filters")
def when_send_request_invalid_dimensions(ctx: dict) -> None:
    """A dimension filter of the wrong JSON type, which the SELLER must refuse.

    The pinned request declares ``min_width`` as ``{"type": "integer"}`` with no
    ``minimum``, so a negative width is a valid request and cannot stand for an invalid
    one; a non-integer is the schema violation the sentence names. The request names the
    account the scenario seeded, so a sandbox scenario grades a sandbox request.
    """
    _call_raw(ctx, min_width="wide", **_named_account(ctx))


@when(parsers.parse("the Buyer Agent sends a list_creative_formats request with asset_types {asset_types}"))
def when_send_request_asset_types(ctx: dict, asset_types: str) -> None:
    _call_raw(ctx, asset_types=json.loads(asset_types))


# There is no "type + asset_types" step. It parsed a `type` the request does not
# declare, dropped it, and sent asset_types alone -- so BR-RULE-031's AND semantics
# were stated over one filter. The two scenarios now use the asset_types +
# name_search step below, which sends two filters production actually applies.


# ── Filter: asset_types + name_search combined ──────────────────────


@when(parsers.parse('the Buyer Agent requests formats with asset_types {asset_types} and name_search "{name_search}"'))
def when_request_asset_types_and_name_search(ctx: dict, asset_types: str, name_search: str) -> None:
    parsed_assets = json.loads(asset_types)
    _call_raw(ctx, asset_types=parsed_assets, name_search=name_search)


# There is no "type only" step. It accepted a type value and then dispatched an
# UNFILTERED request, so every sentence naming a type filter was a no-op that the
# matching Then then certified. `type` is not a parameter of the media-buy
# list-creative-formats request.


# ── Filter: format_ids ───────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with format_ids filter {filter_value}"))
def when_request_format_ids(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    # Plain dicts, not FormatId: a bad id must be rejected by the seller, and
    # constructing FormatId here would reject it in the test process instead.
    _call_raw(ctx, format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": fid} for fid in parsed])


@when(
    parsers.re(
        r'the Buyer Agent requests formats filtered by format_id "(?P<fmt_id>[^"]+)" '
        r'declared by agent "(?P<agent_url>[^"]+)"'
    )
)
def when_request_format_id_at_agent(ctx: dict, fmt_id: str, agent_url: str) -> None:
    """One format_ids reference naming *agent_url* exactly as the scenario spells it.

    Worded apart from ``requests formats with format_ids filter``, whose ``{filter_value}``
    would otherwise swallow this sentence too. A plain dict, not ``FormatId``, so the SELLER
    parses and canonicalizes the spelling rather than the test process.
    """
    _call_raw(ctx, format_ids=[{"agent_url": agent_url, "id": fmt_id}])


# ── Filter: asset_types ─────────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with asset_types filter {filter_value}"))
def when_request_asset_types(ctx: dict, filter_value: str) -> None:
    parsed = json.loads(filter_value)
    _call_raw(ctx, asset_types=parsed)


# ── Filter: min_width / max_width ────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d}"))
def when_request_min_width(ctx: dict, min_w: int) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(min_width=min_w))


@when(parsers.parse("the Buyer Agent requests formats with max_width {max_w:d}"))
def when_request_max_width(ctx: dict, max_w: int) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(max_width=max_w))


@when(parsers.parse("the Buyer Agent requests formats with min_width {min_w:d} and max_width {max_w:d}"))
def when_request_min_max_width(ctx: dict, min_w: int, max_w: int) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(min_width=min_w, max_width=max_w))


# ── Filter: is_responsive ───────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with is_responsive {value}"))
def when_request_responsive(ctx: dict, value: str) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(is_responsive=value.lower() == "true"))


# ── Filter: name_search ─────────────────────────────────────────────


@when(parsers.parse('the Buyer Agent requests formats with name_search "{search}"'))
def when_request_name_search(ctx: dict, search: str) -> None:
    _call(ctx, req=ListCreativeFormatsRequest(name_search=search))


# ── Filter: disclosure_positions / disclosure_persistence ────────────


@when(
    parsers.re(
        r"the Buyer Agent requests formats with "
        r"(?P<field>disclosure_positions|disclosure_persistence) filter (?P<filter_value>.+)"
    )
)
def when_request_disclosure_filter(ctx: dict, field: str, filter_value: str) -> None:
    """Send one of the two disclosure filters verbatim, so the SELLER grades the members."""
    _call_raw(ctx, **{field: json.loads(filter_value)})


# ── Filter: output_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with output_format_ids filter {filter_value}"))
def when_request_output_format_ids(ctx: dict, filter_value: str) -> None:
    # Forwarded verbatim — a row whose entries lack agent_url/id is exactly the
    # malformed payload the seller is supposed to reject.
    _call_raw(ctx, output_format_ids=json.loads(filter_value) or [])


# ── Filter: input_format_ids ────────────────────────────────────────


@when(parsers.parse("the Buyer Agent requests formats with input_format_ids filter {filter_value}"))
def when_request_input_format_ids(ctx: dict, filter_value: str) -> None:
    _call_raw(ctx, input_format_ids=json.loads(filter_value) or [])


# ── Partition dispatch steps ──────────────────────────────────────────
# Each partition When step maps the semantic label to an actual filter
# and calls production code through the harness.


def _partition_format_ids(ctx: dict, partition: str) -> None:
    """Map format_ids partition label to filter and call harness."""
    known_ids = ctx.get("known_format_ids", [])
    if partition == "omitted":
        _call(ctx)
    elif partition == "all_ids_match":
        req = ListCreativeFormatsRequest(format_ids=known_ids)
        _call(ctx, req=req)
    elif partition == "partial_match":
        req = ListCreativeFormatsRequest(format_ids=known_ids[:1])
        _call(ctx, req=req)
    elif partition == "no_match":
        no_match = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
        req = ListCreativeFormatsRequest(format_ids=no_match)
        _call(ctx, req=req)
    else:
        _call_raw(ctx, format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": partition}])


def _partition_asset_types(ctx: dict, partition: str) -> None:
    """Map asset_types partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "single_type_match":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["image"]))
    elif partition == "multiple_types_or":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["image", "video"]))
    elif partition == "no_matching_formats":
        _call(ctx, req=ListCreativeFormatsRequest(asset_types=["webhook"]))
    else:
        _call_raw(ctx, asset_types=[partition])


def _partition_dimension(ctx: dict, partition: str) -> None:
    """Map dimension partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "width_only":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=300))
    elif partition == "height_only":
        _call(ctx, req=ListCreativeFormatsRequest(min_height=50))
    elif partition == "width_and_height":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=300, min_height=50))
    elif partition == "no_render_match":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=9999))
    elif partition == "no_dimension_info":
        _call(ctx, req=ListCreativeFormatsRequest(min_width=1))
    else:
        # int(partition) may itself raise for a non-numeric label; forward the raw
        # string in that case so the seller grades the type, not the test process.
        try:
            min_width: object = int(partition)
        except ValueError:
            min_width = partition
        _call_raw(ctx, min_width=min_width)


def _partition_responsive(ctx: dict, partition: str) -> None:
    """Map responsive partition label to filter and call harness."""
    if partition == "omitted":
        _call(ctx)
    elif partition == "responsive_true":
        _call(ctx, req=ListCreativeFormatsRequest(is_responsive=True))
    elif partition == "responsive_false":
        _call(ctx, req=ListCreativeFormatsRequest(is_responsive=False))
    else:
        # Forward the label VERBATIM. `partition.lower() == "true"` coerced every
        # unrecognized label to a valid `False`, so an invalid row dispatched a
        # perfectly good request and could never be rejected.
        _call_raw(ctx, is_responsive=partition)


def _partition_name_search(ctx: dict, partition: str) -> None:
    """Map name_search partition label to filter and call harness."""
    names = ctx.get("named_formats", ["Standard Banner", "Video Interstitial", "Native Card"])
    if partition == "omitted":
        _call(ctx)
    elif partition == "exact_name":
        _call(ctx, req=ListCreativeFormatsRequest(name_search=names[0]))
    elif partition == "partial_match":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="Banner"))
    elif partition == "case_insensitive":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="standard banner"))
    elif partition == "no_match":
        _call(ctx, req=ListCreativeFormatsRequest(name_search="ZZZZZ_NO_MATCH"))
    else:
        _call(ctx, req=ListCreativeFormatsRequest(name_search=partition))


def _partition_wcag(ctx: dict, partition: str) -> None:
    """Map wcag_level partition label to filter and call harness."""
    from adcp.types import WcagLevel

    wcag_map = {"level_a": WcagLevel.A, "level_aa": WcagLevel.AA, "level_aaa": WcagLevel.AAA}
    if partition == "not_provided":
        _call(ctx)
    elif partition in wcag_map:
        _call(ctx, req=ListCreativeFormatsRequest(wcag_level=wcag_map[partition]))
    else:
        _call_raw(ctx, wcag_level=partition)


def _partition_enum_list(ctx: dict, field: str, partition: str, valid: dict[str, list[str]]) -> None:
    """Shared body for the two disclosure filters, both arrays of one enum.

    ``disclosure_positions`` and ``disclosure_persistence`` partition the same way: a label
    in *valid* names the members a typed request carries; ``omitted`` sends no filter;
    ``empty_array`` and ``duplicate_*`` break the request schema's ``minItems: 1`` and
    ``uniqueItems: true`` (duplicating the first valid row's first member); and any other
    label goes out raw as the one member, so the SELLER refuses an unknown value.

    The requested members are recorded under ``requested_<field>`` (``None`` when the
    filter is absent or invalid), so a Then can compute what the filter must keep.
    """
    ctx[f"requested_{field}"] = valid.get(partition)
    if partition == "omitted":
        _call(ctx)
    elif partition in valid:
        _call(ctx, req=ListCreativeFormatsRequest(**{field: valid[partition]}))
    elif partition == "empty_array":
        _call_raw(ctx, **{field: []})
    elif partition.startswith("duplicate_"):
        first = next(iter(valid.values()))[0]
        _call_raw(ctx, **{field: [first, first]})
    else:
        _call_raw(ctx, **{field: [partition]})


#: ``disclosure_positions`` partitions over the "various disclosure positions" seller
#: (prominent-ad -> ["prominent"], footer-ad -> ["footer"]).
_DISCLOSURE_POSITION_PARTITIONS: dict[str, list[str]] = {
    "single_position": ["prominent"],
    "multiple_positions_all_match": ["prominent", "footer"],
    # All 8 values of the DisclosurePosition enum. Earlier wiring used stale literals
    # (corner/inline/before/after) that no longer exist in the enum, so the request never
    # built and the scenario passed vacuously under a blanket strict=False marker.
    "all_positions": ["prominent", "footer", "audio", "subtitle", "overlay", "end_card", "pre_roll", "companion"],
    # A valid position neither seeded format supports, so a working filter yields nothing.
    "no_matching_formats": ["subtitle"],
}

#: ``disclosure_persistence`` partitions over the "various disclosure persistence
#: capabilities" seller (given_entities.given_seller_various_persistence).
_DISCLOSURE_PERSISTENCE_PARTITIONS: dict[str, list[str]] = {
    "single_mode": ["continuous"],
    # Satisfied only by split-ad, whose two positions declare one mode each.
    "multiple_modes_satisfied_across_positions": ["continuous", "initial"],
    "all_modes": ["continuous", "initial", "flexible"],
    # Each mode is declared somewhere, but no one format declares both.
    "no_matching_formats": ["initial", "flexible"],
    # positions-only declares a position and no disclosure_capabilities, so it drops out.
    "format_without_disclosure_capabilities": ["continuous"],
}


def _partition_disclosure(ctx: dict, partition: str) -> None:
    """Map disclosure_positions partition label to filter and call harness."""
    _partition_enum_list(ctx, "disclosure_positions", partition, _DISCLOSURE_POSITION_PARTITIONS)


def _partition_persistence(ctx: dict, partition: str) -> None:
    """Map disclosure_persistence partition label to filter and call harness."""
    _partition_enum_list(ctx, "disclosure_persistence", partition, _DISCLOSURE_PERSISTENCE_PARTITIONS)


def _partition_format_id_list(ctx: dict, partition: str, direction: str) -> None:
    """Shared body for the output_format_ids and input_format_ids partitions.

    The two handlers were identical apart from the field name and the
    ``format_without_<direction>_ids`` label, so they are ONE function with a
    parameter rather than two copies (CLAUDE.md DRY invariant): a fix applied to
    one copy would otherwise have to be remembered in the other, and the four
    negative branches below are exactly where that memory would fail.

    *direction* is ``"output"`` or ``"input"``; the AdCP field, the seeded-ids
    ctx key and the "no ids declared" partition label are all derived from it.
    """
    field = f"{direction}_format_ids"
    known = ctx.get(f"known_{field}", [])

    # ── Positive branches: typed request via _call (see _call_raw's docstring
    # for why this module deliberately uses both dispatch paths).
    if partition == "omitted":
        _call(ctx)
    elif partition in ("single_format_id", f"format_without_{direction}_ids"):
        _call(ctx, req=ListCreativeFormatsRequest(**{field: known[:1]}))
    elif partition == "multiple_ids_any_match":
        extra = FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")
        _call(ctx, req=ListCreativeFormatsRequest(**{field: known[:1] + [extra]}))
    elif partition == "no_matching_formats":
        no_match = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
        _call(ctx, req=ListCreativeFormatsRequest(**{field: no_match}))

    # ── Negative branches: the payload goes out RAW so the seller rejects it and
    # the buyer receives a real envelope. Each dict below is deliberately NOT a
    # FormatId — constructing one would raise here and production would never run.
    elif partition == "empty_array":
        _call_raw(ctx, **{field: []})
    elif partition == "invalid_format_id_missing_agent_url":
        _call_raw(ctx, **{field: [{"id": "some-id"}]})
    elif partition == "invalid_format_id_missing_id":
        _call_raw(ctx, **{field: [{"agent_url": DEFAULT_AGENT_URL}]})
    elif partition == "invalid_format_id_width_only":
        # core/format-id.json: "dependencies": {"width": ["height"], ...}
        _call_raw(ctx, **{field: [{"agent_url": DEFAULT_AGENT_URL, "id": "display_static", "width": 300}]})
    else:
        _call_raw(ctx, **{field: [{"agent_url": DEFAULT_AGENT_URL, "id": partition}]})


def _partition_output_format_ids(ctx: dict, partition: str) -> None:
    """Map output_format_ids partition label to filter and call harness."""
    _partition_format_id_list(ctx, partition, "output")


def _partition_input_format_ids(ctx: dict, partition: str) -> None:
    """Map input_format_ids partition label to filter and call harness."""
    _partition_format_id_list(ctx, partition, "input")


@when(parsers.parse('the Buyer Agent requests creative formats with format_ids "{partition}"'))
def when_partition_format_ids(ctx: dict, partition: str) -> None:
    _partition_format_ids(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with asset_types "{partition}"'))
def when_partition_asset_types(ctx: dict, partition: str) -> None:
    _partition_asset_types(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with dimension filter "{partition}"'))
def when_partition_dimension(ctx: dict, partition: str) -> None:
    _partition_dimension(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with is_responsive "{partition}"'))
def when_partition_responsive(ctx: dict, partition: str) -> None:
    _partition_responsive(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with name_search "{partition}"'))
def when_partition_name_search(ctx: dict, partition: str) -> None:
    _partition_name_search(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with wcag_level "{partition}"'))
def when_partition_wcag(ctx: dict, partition: str) -> None:
    _partition_wcag(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with disclosure_positions "{partition}"'))
def when_partition_disclosure(ctx: dict, partition: str) -> None:
    _partition_disclosure(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with disclosure_persistence "{partition}"'))
def when_partition_persistence(ctx: dict, partition: str) -> None:
    _partition_persistence(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with output_format_ids "{partition}"'))
def when_partition_output_ids(ctx: dict, partition: str) -> None:
    _partition_output_format_ids(ctx, partition)


@when(parsers.parse('the Buyer Agent requests creative formats with input_format_ids "{partition}"'))
def when_partition_input_ids(ctx: dict, partition: str) -> None:
    _partition_input_format_ids(ctx, partition)


# ── Boundary dispatch steps ──────────────────────────────────────────
# Boundary steps reuse the same partition mapping — the boundary_point
# label is just a more descriptive partition label.


@when(parsers.parse('the Buyer Agent requests creative formats at format_ids boundary "{boundary_point}"'))
def when_boundary_format_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "all IDs match": "all_ids_match",
        "partial match (some excluded)": "partial_match",
        "no IDs match (empty result)": "no_match",
        "omitted (no filter)": "omitted",
    }
    _partition_format_ids(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at asset_types boundary "{boundary_point}"'))
def when_boundary_asset_types(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single asset type match": "single_type_match",
        "multiple types OR semantics": "multiple_types_or",
        "omitted (no filter)": "omitted",
        "brief (new asset type for generative formats)": "brief",
        "catalog (new asset type for catalog-based formats)": "catalog",
        "no formats match (empty result)": "no_matching_formats",
        "Unknown string not in enum": "unknown_asset_type",
        "promoted_offerings (removed from enum)": "removed_promoted_offerings",
    }
    _partition_asset_types(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at dimension boundary "{boundary_point}"'))
def when_boundary_dimension(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "width filter only": "width_only",
        "height filter only": "height_only",
        "width and height combined": "width_and_height",
        "omitted (no dimension filter)": "omitted",
        "no render matches constraints": "no_render_match",
    }
    _partition_dimension(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at responsive boundary "{boundary_point}"'))
def when_boundary_responsive(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "is_responsive = true": "responsive_true",
        "is_responsive = false": "responsive_false",
        "is_responsive omitted": "omitted",
    }
    _partition_responsive(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at name_search boundary "{boundary_point}"'))
def when_boundary_name_search(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "exact name match": "exact_name",
        "partial substring match": "partial_match",
        "case-insensitive match": "case_insensitive",
        "omitted (no filter)": "omitted",
        "no match (empty result)": "no_match",
        # Sent verbatim: an unrecognized partition is the name_search value itself.
        "empty string (filters nothing)": "",
    }
    _partition_name_search(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at wcag_level boundary "{boundary_point}"'))
def when_boundary_wcag(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "A (first enum value — minimum conformance)": "level_a",
        "AAA (last enum value — highest conformance)": "level_aaa",
        "Not provided (no filter)": "not_provided",
        "Unknown string not in enum": "unknown_value",
    }
    _partition_wcag(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at disclosure boundary "{boundary_point}"'))
def when_boundary_disclosure(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single position ['prominent'] (min array size)": "single_position",
        "all 8 positions (max meaningful array)": "all_positions",
        "omitted (no filter)": "omitted",
        "format has no supported_disclosure_positions (excluded)": "no_matching_formats",
        "empty array []": "empty_array",
        "unknown position string 'sidebar'": "sidebar",
        "duplicate positions ['prominent','prominent']": "duplicate_positions",
    }
    _partition_disclosure(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at persistence boundary "{boundary_point}"'))
def when_boundary_persistence(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single mode ['continuous'] (min array size)": "single_mode",
        "all 3 modes (max array size)": "all_modes",
        "two modes satisfied by different positions": "multiple_modes_satisfied_across_positions",
        "omitted (no filter)": "omitted",
        "format has no disclosure_capabilities (excluded)": "format_without_disclosure_capabilities",
        "no format covers all requested modes": "no_matching_formats",
        "empty array []": "empty_array",
        "unknown mode string 'permanent'": "permanent",
        "duplicate modes ['continuous','continuous']": "duplicate_modes",
    }
    _partition_persistence(ctx, mapping.get(boundary_point, boundary_point))


@when("the Buyer Agent requests creative formats for that format")
def when_request_that_format(ctx: dict) -> None:
    """Request the format an earlier Given named (``ctx["target_format"]``) by its format_id."""
    _call(ctx, req=ListCreativeFormatsRequest(format_ids=[ctx["target_format"].format_id]))


@when(parsers.parse('the Buyer Agent requests creative formats at output_format_ids boundary "{boundary_point}"'))
def when_boundary_output_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single FormatId (min array size)": "single_format_id",
        "multiple FormatIds, one matches (ANY semantics)": "multiple_ids_any_match",
        "omitted (no filter)": "omitted",
        "format has no output_format_ids (excluded)": "format_without_output_ids",
        "no formats match requested output IDs": "no_matching_formats",
        "empty array []": "empty_array",
        "FormatId missing agent_url": "invalid_format_id_missing_agent_url",
        "FormatId missing id": "invalid_format_id_missing_id",
    }
    _partition_output_format_ids(ctx, mapping.get(boundary_point, boundary_point))


@when(parsers.parse('the Buyer Agent requests creative formats at input_format_ids boundary "{boundary_point}"'))
def when_boundary_input_ids(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "single FormatId (min array size)": "single_format_id",
        "multiple FormatIds, one matches (ANY semantics)": "multiple_ids_any_match",
        "omitted (no filter)": "omitted",
        "format has no input_format_ids (excluded)": "format_without_input_ids",
        "no formats match requested input IDs": "no_matching_formats",
        "empty array []": "empty_array",
        "FormatId missing agent_url": "invalid_format_id_missing_agent_url",
        "FormatId missing id": "invalid_format_id_missing_id",
    }
    _partition_input_format_ids(ctx, mapping.get(boundary_point, boundary_point))


# ── Creative agent format queries (partition / boundary) ─────────────
# Asset type partitions map to the ``asset_types`` filter on
# ``ListCreativeFormatsRequest``, which the media-buy variant this seller binds
# does declare. The TYPE partitions are gone with their outlines — see
# tests/bdd/conftest.py's UC-005 record.


def _partition_agent_asset_types(ctx: dict, partition: str) -> None:
    """Creative agent asset type filter — maps to asset_types on ListCreativeFormatsRequest."""
    if partition in ("not_provided", "omitted"):
        _call(ctx)
    elif partition == "unknown_value":
        # Rejected by the SELLER's validation, not by the model in this process.
        _call_raw(ctx, asset_types=[partition])
    elif partition == "empty_array":
        _call_raw(ctx, asset_types=[])
    else:
        # Valid asset types: image, video, audio, text, html, javascript, url.
        # Still dispatched raw: this branch also receives the rows the Examples table
        # marks invalid (e.g. "vast", valid in the media-buy variant but not for a
        # creative agent), so the model must not get to pre-judge them.
        _call_raw(ctx, asset_types=[partition])


@when(parsers.parse('the Buyer Agent queries creative agent formats with asset_types "{partition}"'))
def when_query_agent_asset_types(ctx: dict, partition: str) -> None:
    _partition_agent_asset_types(ctx, partition)


@when(parsers.parse('the Buyer Agent queries creative agent formats at asset_types boundary "{boundary_point}"'))
def when_boundary_agent_asset_types(ctx: dict, boundary_point: str) -> None:
    mapping = {
        "image (first enum value)": "image",
        "url (last enum value)": "url",
        "Not provided (no filter)": "not_provided",
        "vast (valid in media-buy variant but not in creative agent)": "unknown_value",
        "Empty array": "empty_array",
    }
    _partition_agent_asset_types(ctx, mapping.get(boundary_point, boundary_point))
