"""Then steps for payload and field assertions.

Every assertion operates on real production response objects:
    the dispatch payload (require_payload) -- ListCreativeFormatsResponse on success
    ctx["error"] -- Exception on failure

No stub mode. No dict intermediaries -- assertions access Format attributes directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from enum import Enum
from typing import Any

from pytest_bdd import parsers, then

from tests.bdd.steps._outcome_helpers import payload_or_none, require_payload, wire_field
from tests.bdd.steps.generic._table import rows as table_rows
from tests.factories.format import asset_types_of

# -- Helpers -------------------------------------------------------------------


def _get_formats(ctx: dict) -> list[Any]:
    """Extract formats list from response as real Format objects."""
    resp = payload_or_none(ctx)
    if resp is None:
        return []
    if hasattr(resp, "formats"):
        return list(resp.formats or [])
    return []


def _fmt_type_str(f: Any) -> str | None:
    """Get format type as a string value (enum .value or str)."""
    if f.type is None:
        return None
    return f.type.value if hasattr(f.type, "value") else str(f.type)


def _fmt_name(f: Any) -> str | None:
    """Get format name."""
    return f.name if hasattr(f, "name") else None


# -- Format catalog assertions ------------------------------------------------


@then("the response should include all registered formats")
def then_all_formats(ctx: dict) -> None:
    """Assert the response covers EVERY format the registry serves -- identity, not count.

    "Registered" is what the seller lists on this transport
    (``CreativeFormatsEnv.served_registry_formats``): the Given's formats on every
    transport, served by the seller's own peer creative agent with the standard agent not
    federated and declared by the tenant's product, or nothing when no Given named any --
    the list projects the seller's products. ``pagination.total_count``
    must equal that catalog's size, every returned format must be one of it, and a
    response with no further page must hold all of it.

    The e2e branch this replaces asserted ``display``/``video``/``audio`` categories read
    off ``type``, which ``core/format.json`` has not declared since the 3.0 major, so it
    raised AttributeError on every format instead of grading anything.
    """
    resp = require_payload(ctx)
    registered_names = sorted(n for r in ctx["env"].served_registry_formats() if (n := _fmt_name(r)))
    returned_names = sorted(n for f in resp.formats if (n := _fmt_name(f)))
    assert resp.pagination.total_count == len(registered_names), (
        f"total_count {resp.pagination.total_count} != {len(registered_names)} registered formats"
    )
    unexpected = set(returned_names) - set(registered_names)
    assert not unexpected, f"returned formats the registry does not serve: {sorted(unexpected)}"
    if not resp.pagination.has_more:
        assert returned_names == registered_names, (
            f"Format identity mismatch: returned={returned_names}, registered={registered_names}"
        )


# Deleted: "the response should include only display formats" and "no video formats
# should be present in the results". Both graded a format's `type`, and `type` is not a
# field of the pinned core/format.json -- Format.model_fields has none, so a Format
# carries one only as pydantic model_extra that FormatFactory injected in the test
# process. Measured: FormatFactory.build(type="display") lands it in model_extra. So
# both steps asserted a value no buyer receives, on an object the test itself had
# decorated. @T-UC-005-main-filtered was their only caller and now asserts on `name`,
# which the pinned Format REQUIRES, through `only "..." should be returned` -- which
# also pins the count at 1, so an empty result cannot satisfy it.


def _referral_url(ref: Any) -> str | None:
    """Extract agent_url from a wire referral dict (model_dump shape)."""
    return ref.get("agent_url") if isinstance(ref, dict) else getattr(ref, "agent_url", None)


def _referral_capabilities(ref: Any) -> Any:
    """Extract capabilities from a wire referral dict (model_dump shape)."""
    return ref.get("capabilities") if isinstance(ref, dict) else getattr(ref, "capabilities", None)


@then("the response should include creative_agents referrals")
def then_has_referrals(ctx: dict) -> None:
    """Assert the wire response contains creative_agents matching the Given agents.

    Reads ``creative_agents`` from the serialized wire (REST/A2A/MCP/e2e_rest) via
    the shared :func:`wire_field` reader, which raises on a missing wire rather than
    serializing a typed payload in its place. The previous body read the lossy
    reconstructed ``ctx['response'].creative_agents`` payload — this asserts the
    buyer-facing wire.
    """
    referrals = wire_field(ctx, "creative_agents")
    assert referrals is not None, "Expected creative_agents field on the wire, got None"
    assert referrals, "Expected creative agent referrals on the wire but got empty list"

    actual_urls = set()
    for ref in referrals:
        url_value = _referral_url(ref)
        assert url_value, f"Referral missing agent_url: {ref}"
        url_str = str(url_value)
        assert url_str.startswith("http"), f"agent_url should be http/https URL, got: {url_str!r}"
        actual_urls.add(url_str)
        assert _referral_capabilities(ref) is not None, f"Referral missing capabilities: {ref}"

    # ONE comparison, every transport. The e2e branch this replaces re-checked that each
    # returned URL starts with "http" — which the loop above already asserts — and skipped
    # the only assertion that can fail: that the agent the Given configured is ACTUALLY
    # referred. It was written when the Given wrote nothing anywhere, so over e2e there was
    # no expectation to compare against. The Given now seeds the ``creative_agents`` row
    # production reads, in the per-test database in process and in the live server's own
    # over e2e_rest, so the same comparison holds on both.
    given_agents = ctx.get("creative_agent_referrals", [])
    if given_agents:
        expected_urls = {str(a.agent_url) for a in given_agents}
        missing = expected_urls - actual_urls
        assert not missing, f"Given agent URLs not found in response: {missing}. Response contains: {actual_urls}"


@then("each referral should include the agent URL and supported capabilities")
def then_referral_fields(ctx: dict) -> None:
    """Assert each wire referral has a well-formed agent_url AND non-empty capabilities.

    Reads referrals from the serialized wire via the shared :func:`wire_field`
    reader instead of the lossy reconstructed ``ctx['response']`` payload.
    """
    referrals = wire_field(ctx, "creative_agents")
    assert referrals, "No referrals to verify -- expected at least one creative agent"
    # Capabilities are commitments (AdCP design principle): referrals must
    # advertise exactly the backed set — extras are unbacked commitments,
    # omissions are silent capability loss. Derived from the production
    # constant; the unit-level policy anchor
    # (test_creative_formats_behavioral.py) stays hardcoded on purpose.
    from src.core.tools.creative_formats import ADVERTISED_CREATIVE_AGENT_CAPABILITIES

    known_capabilities = {c.value for c in ADVERTISED_CREATIVE_AGENT_CAPABILITIES}
    for ref in referrals:
        url_value = _referral_url(ref)
        assert url_value, f"Missing agent_url in referral: {ref}"
        url_str = str(url_value)
        assert url_str.startswith("http"), f"agent_url should be a URL (http/https), got: {url_str!r}"
        caps = _referral_capabilities(ref)
        assert caps is not None, f"Missing capabilities in referral: {ref}"
        assert isinstance(caps, (list, tuple)), f"capabilities should be a list, got: {type(caps).__name__}"
        assert caps, "capabilities should be non-empty, got empty list"
        cap_strs = {str(c.value) if isinstance(c, Enum) else str(c) for c in caps}
        assert cap_strs == known_capabilities, (
            f"Advertised capabilities {cap_strs} != backed set {known_capabilities} "
            "(extras are unbacked commitments; omissions are silent capability loss)"
        )


# -- Format field presence -----------------------------------------------------


@then("each format should include a format_id with agent_url and id")
def then_format_id_fields(ctx: dict) -> None:
    for f in _get_formats(ctx):
        fid = f.format_id if hasattr(f, "format_id") else None
        assert fid is not None, f"Format '{_fmt_name(f)}' missing format_id"
        assert getattr(fid, "agent_url", None), f"Format '{_fmt_name(f)}' format_id missing agent_url"
        assert getattr(fid, "id", None), f"Format '{_fmt_name(f)}' format_id missing id"


@then("each format should include a name")
def then_format_name(ctx: dict) -> None:
    """Every returned format carries a non-empty string name.

    The `type category` half of this step's predecessor is gone with the field: adcp 3.12
    removed `type` from Format, so the vocabulary that half graded is one the pin no longer
    has. See the note below for the step that carried it.
    """
    formats = _get_formats(ctx)
    assert formats, "No formats in response -- cannot verify names"
    for f in formats:
        name = _fmt_name(f)
        assert name, f"Format missing name: {f}"
        assert isinstance(name, str), f"Format name is not a string: {type(name)}"


# ``each format should include a name and type category`` used to bind here — the second
# of the two Thens @T-UC-005-main dropped when adcp 3.12 removed ``type`` from ``Format``
# (BR-UC-005-discover-creative-formats.feature:36-37 names both). The only occurrence of
# that sentence left in the feature sources is inside that comment, so nothing binds it.
# Its body could not have graded the pin either way: the ``valid_types`` set is a
# vocabulary 3.12 no longer defines, ``_fmt_type_str`` raises AttributeError on every row
# at the pin, and with no formats in the response the loop asserted nothing at all.
# ``then_format_name`` above keeps the half that survives the removal.


@then("each format should include asset requirements with type and dimensions")
def then_format_assets(ctx: dict) -> None:
    """Assert EVERY format has asset requirements with the exact required keys."""
    required_asset_keys = {"asset_type", "asset_id"}
    formats = _get_formats(ctx)
    assert formats, "No formats in response -- cannot verify asset requirements"

    for f in formats:
        assets = f.assets
        renders = f.renders
        assert assets or renders, (
            f"Format '{_fmt_name(f)}' has neither assets nor renders -- "
            f"step requires 'each format' to include asset requirements"
        )

        if assets:
            for a in assets:
                asset_keys = set(a.model_dump(exclude_none=True).keys())
                missing = required_asset_keys - asset_keys
                assert not missing, (
                    f"Asset in format '{_fmt_name(f)}' missing required keys {missing} "
                    f"(has: {asset_keys & required_asset_keys or 'none of the required keys'}). "
                    f"Spec requires each asset requirement to carry asset_type + asset_id."
                )
                asset_type = a.asset_type
                asset_type_str = asset_type.value if isinstance(asset_type, Enum) else asset_type
                assert asset_type_str, (
                    f"Asset in format '{_fmt_name(f)}' has empty asset_type -- buyers cannot know what media to upload"
                )

        if renders:
            for r in renders:
                dims = r.dimensions
                assert dims is not None, f"Render in format '{_fmt_name(f)}' missing dimensions"
                dim_keys = set(dims.model_dump(exclude_none=True).keys())
                width_keys = {"width", "min_width", "responsive"}
                assert dim_keys & width_keys, (
                    f"Render dimensions in format '{_fmt_name(f)}' missing width "
                    f"specification. Expected one of {width_keys}, got keys: {dim_keys}"
                )


# -- Sorting assertions --------------------------------------------------------


# ``the results should be sorted by format type then name`` used to bind here. The
# sentence was removed from @T-UC-005-main deliberately — adcp 3.12 dropped ``type``
# from ``Format``, and the feature says so at BR-UC-005-discover-creative-formats.feature:36
# — leaving a step no rendered sentence binds. It also could not have graded the
# obligation it named: it returned a pass for a catalog of 0 or 1 formats, and on a
# longer one ``_fmt_type_str`` raises AttributeError at the pin rather than failing an
# assertion (see ``then_results_ordered`` below, which reads whatever columns the table
# declares for exactly that reason).


@then("the results should be ordered:")
def then_results_ordered(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Assert the response's format order matches the table, column by column.

    Reads whatever columns the table declares instead of a fixed {name, type} pair. The
    fixed pair could not work at the pin: adcp 3.12 removed ``type`` from ``Format``, so
    ``_fmt_type_str`` raises ``AttributeError`` on every row rather than failing an
    assertion.
    """
    expected = table_rows(datatable)
    columns = list(expected[0]) if expected else ["name"]
    readers = {"name": _fmt_name, "type": _fmt_type_str}
    actual = [{c: readers.get(c, _fmt_name)(f) for c in columns} for f in _get_formats(ctx)]
    assert actual == expected, f"Expected order {expected}, got {actual}"


# -- Specific format inclusion/exclusion ---------------------------------------


def _listed_formats(ctx: dict) -> list[Any]:
    """The formats of a SUCCESSFUL listing; a refusal fails here instead of listing nothing.

    The exclusion Thens below need this and the inclusion ones do not. ``_get_formats``
    answers ``[]`` for a refused request, and ``the response is compliant with the
    list_creative_formats spec`` grades a refusal against the error contract, so a
    scenario ending in "no formats" or "X should not be returned" held on a seller that
    refused the filter outright. Every such scenario sends a request the pin admits and
    expects the format filtered out of a successful response.
    """
    resp = require_payload(ctx)
    return list(resp.formats or [])


@then("no formats should be returned")
def then_no_formats(ctx: dict) -> None:
    formats = _listed_formats(ctx)
    assert len(formats) == 0, f"Expected 0 formats, got {len(formats)}"


@then(parsers.parse('only "{name}" should be returned'))
def then_only_named(ctx: dict, name: str) -> None:
    formats = _get_formats(ctx)
    names = [_fmt_name(f) for f in formats]
    assert len(formats) == 1, f"Expected exactly 1 format, got {len(formats)}: {names}"
    assert _fmt_name(formats[0]) == name, f"Expected format '{name}', got '{_fmt_name(formats[0])}'"


@then(parsers.parse('"{name}" should be returned'))
def then_named_returned(ctx: dict, name: str) -> None:
    names = [_fmt_name(f) for f in _get_formats(ctx)]
    assert name in names, f"Expected '{name}' in results, got {names}"


@then(parsers.parse('"{name}" should not be returned'))
def then_named_not_returned(ctx: dict, name: str) -> None:
    names = [_fmt_name(f) for f in _listed_formats(ctx)]
    assert name not in names, f"Did not expect '{name}' in results, got {names}"


@then("no format should include pricing_options")
def then_no_format_pricing(ctx: dict) -> None:
    """No format on the wire carries a ``pricing_options`` key.

    Read as key absence on the serialized body: a ``null`` would be a field the seller
    emitted, and ``core/format.json`` says of ``pricing_options`` "New code SHOULD NOT
    emit this field".
    """
    formats = wire_field(ctx, "formats")
    assert formats, "no formats on the wire -- the absence of pricing_options would hold vacuously"
    priced = [f.get("name") for f in formats if "pricing_options" in f]
    assert not priced, f"formats carrying pricing_options on the wire: {priced}"


@then(parsers.parse('each returned format should contain at least one asset of type "{asset_type}"'))
def then_each_format_has_asset_type(ctx: dict, asset_type: str) -> None:
    """Every format on the wire declares an *asset_type* asset, in a slot or a repeatable group.

    The asset_types filter keeps "formats that include these asset types"
    (list-creative-formats-request.json), so with one requested type every survivor carries
    it. An empty answer fails: the Given seeded a format that does, so nothing returned
    means the seller refused the value or dropped the format.
    """
    formats = wire_field(ctx, "formats")
    assert formats, f"no formats on the wire for asset_types [{asset_type!r}] -- the seeded format was not returned"
    lacking = [f.get("name") for f in formats if asset_type not in asset_types_of(f)]
    assert not lacking, f"formats returned for asset_types [{asset_type!r}] without such an asset: {lacking}"


def _requested_format_on_wire(ctx: dict) -> dict:
    """The one format on the wire whose format_id is the one the request named (``ctx["target_format"]``)."""
    target_id = ctx["target_format"].format_id.id
    matches = [f for f in wire_field(ctx, "formats") if (f.get("format_id") or {}).get("id") == target_id]
    assert len(matches) == 1, f"expected the requested format {target_id!r} once on the wire, found {len(matches)}"
    return matches[0]


@then(parsers.parse('the format\'s "{collection}" entry should expose "{key}" with value "{value}"'))
def then_format_entry_exposes(ctx: dict, collection: str, key: str, value: str) -> None:
    """The requested format's *collection* holds exactly one entry whose *key* is *value*; it becomes "the same entry"."""
    entries = [e for e in _requested_format_on_wire(ctx).get(collection) or [] if e.get(key) == value]
    assert len(entries) == 1, f"expected one {collection} entry with {key}={value!r} on the wire, found {len(entries)}"
    ctx["wire_entry"] = entries[0]


@then(parsers.parse('the same entry should expose "{key}" as {value}'))
def then_same_entry_exposes_as(ctx: dict, key: str, value: str) -> None:
    """The entry carries *key* at its own level with the JSON value *value* (type and value both)."""
    entry, expected = ctx["wire_entry"], json.loads(value)
    assert key in entry, f"{key!r} is absent from the entry on the wire: {entry}"
    assert type(entry[key]) is type(expected) and entry[key] == expected, (
        f"entry {key!r} is {entry[key]!r} on the wire, expected {expected!r}"
    )


@then(parsers.parse('the same entry should expose "{key}" with value {value:d} under "{parent}"'))
def then_same_entry_exposes_under(ctx: dict, key: str, value: int, parent: str) -> None:
    """The entry's *parent* object carries *key* = *value*."""
    nested = ctx["wire_entry"].get(parent)
    assert isinstance(nested, dict), f"entry has no {parent!r} object on the wire: {ctx['wire_entry']}"
    assert key in nested and nested[key] == value, (
        f"{parent}.{key} is {nested.get(key)!r} on the wire, expected {value}"
    )


@then(parsers.parse('the same entry should not expose "{key}" outside "{parent}"'))
def then_same_entry_confines_key(ctx: dict, key: str, parent: str) -> None:
    """*key* appears nowhere in the entry except inside its *parent* object, at any depth."""

    def paths(node: Any, path: str) -> list[str]:
        if isinstance(node, dict):
            here = [f"{path}.{key}"] if key in node else []
            return here + [p for k, v in node.items() for p in paths(v, f"{path}.{k}")]
        if isinstance(node, list):
            return [p for i, v in enumerate(node) for p in paths(v, f"{path}[{i}]")]
        return []

    outside = [p for k, v in ctx["wire_entry"].items() if k != parent for p in paths(v, k)]
    if key in ctx["wire_entry"]:
        outside.insert(0, key)
    assert not outside, f"{key!r} appears outside {parent!r} in the entry on the wire at: {outside}"


@then(parsers.parse('"{a}", "{b}", and "{c}" should all be returned'))
def then_three_returned(ctx: dict, a: str, b: str, c: str) -> None:
    names = [_fmt_name(f) for f in _get_formats(ctx)]
    for name in [a, b, c]:
        assert name in names, f"Expected '{name}' in results, got {names}"


# `the returned format type should be "{fmt_type}"` bound here — the third casualty of adcp
# 3.12 dropping `type` from Format, alongside the two noted above. No feature carries the
# sentence.


# -- Partition/boundary test outcomes ------------------------------------------
# These verify that production code filters and boundaries actually work,
# not merely that the response has a well-formed shape.
#
# Two regex steps cover all partition ("filtering should result in") and
# boundary ("handling should be") scenarios for UC-005 creative formats.
# UC-004 delivery boundary scenarios are handled by their own more-specific
# regex in uc004_delivery.py.


_KNOWN_FILTER_FIELDS = frozenset(
    {
        "type",
        "format_ids",
        "asset_types",
        "dimension",
        "responsive",
        "name search",
        "wcag",
        "disclosure_positions",
        "disclosure",
        "disclosure_persistence",
        "persistence",
        "output_format_ids",
        "input_format_ids",
        "creative agent type",
        "creative agent asset type",
    }
)

# Fields that use creative_agent_formats (dicts) instead of registry_formats
_CREATIVE_AGENT_FIELDS = frozenset({"creative agent type", "creative agent asset type"})


#: An outcome cell naming an AdCP error code, e.g. ``INVALID_REQUEST``.
_OUTCOME_CODE_RE = re.compile(r"^[A-Z][A-Z_0-9]+$")


def _assert_partition_outcome(ctx: dict, field: str, expected: str) -> None:
    """Assert a partition/boundary outcome: ``valid``, or a NAMED wire error code.

    The rejection half used to accept ``expected == "invalid"`` and check only
    that ``"error"`` was a key in ``ctx`` and that no payload came back. Both
    halves of that were satisfiable WITHOUT the seller ever seeing the request:
    the When built the typed model in the test process, pydantic raised, the step
    stashed the exception, and this assertion passed. It could not distinguish a
    client-side rejection from a server one, and it never looked at a code — so
    ~36 UC-005 rows asserted only "something went wrong somewhere".

    Now the outcome cell NAMES the code (the Examples table carries the
    contract) and the assertion is made against the real two-layer envelope the
    buyer received, via the harness's own ``assert_wire_error`` — the single
    sanctioned surface, shared with ``then_error.py``'s wire-first steps rather
    than re-spelled here (CLAUDE.md DRY invariant).

    There is deliberately NO bare-``invalid`` branch left. Keeping one would let
    an unmigrated row keep grading nothing, and every UC-005 row now names a code.
    """
    assert field in _KNOWN_FILTER_FIELDS, (
        f"Unknown filter field '{field}' in partition/boundary test. Known fields: {sorted(_KNOWN_FILTER_FIELDS)}"
    )
    if expected == "valid":
        assert "error" not in ctx, f"Expected valid filter result for '{field}' but got error: {ctx.get('error')}"
        resp = require_payload(ctx)
        # Access .formats directly -- AttributeError means the response is not
        # a ListCreativeFormatsResponse (stronger than hasattr which is always
        # True on Pydantic models).
        formats_attr = resp.formats
        assert isinstance(formats_attr, list), (
            f"Expected 'formats' to be a list for '{field}' filter, got {type(formats_attr).__name__}"
        )
        return

    if expected == "invalid":
        raise AssertionError(
            f"Outcome cell for '{field}' says a bare 'invalid'. Name the AdCP error code the "
            "seller must return (e.g. INVALID_REQUEST) so the scenario grades WHICH rejection "
            "the buyer receives, not merely that something failed."
        )

    if not _OUTCOME_CODE_RE.match(expected):
        raise AssertionError(
            f"Unexpected outcome value '{expected}' for '{field}' -- expected 'valid' or an AdCP error code"
        )

    # The rejection must have crossed a transport. assert_wire_error hard-asserts a
    # real captured envelope before comparing, so this cannot pass on a
    # reconstructed exception or on a transport that swallowed the typed error.
    result = ctx.get("result")
    assert result is not None, (
        f"Expected '{field}' to be rejected with {expected} on the wire, but no transport result "
        f"was captured — the payload never reached the seller. ctx['error']={ctx.get('error')!r}"
    )
    try:
        result.assert_wire_error(expected)
    except AssertionError as exc:
        raise AssertionError(f"[{field}] {exc}") from None


def _assert_returned_formats_subset_of_registry(ctx: dict, field: str, label: str) -> None:
    """Assert returned formats are a proper subset of the seeded registry.

    Checks containment, cardinality, and uniqueness invariants.
    Skips for creative agent fields which use a different data path.
    """
    if field in _CREATIVE_AGENT_FIELDS:
        return

    formats = _get_formats(ctx)
    registry = ctx.get("registry_formats", [])

    assert registry, (
        f"{label} '{field}' has no seeded registry_formats in ctx -- "
        "the Given step must populate ctx['registry_formats'] so the Then step "
        "can verify returned formats against the catalog"
    )

    registry_names: set[str] = {n for r in registry if (n := _fmt_name(r)) is not None}

    # 1. Containment
    returned_names = [_fmt_name(f) for f in formats]
    returned_names_set: set[str] = {n for n in returned_names if n is not None}
    unexpected = returned_names_set - registry_names
    assert not unexpected, (
        f"{label} '{field}' returned formats not in the seeded registry: "
        f"{sorted(unexpected)}. Registry names: {sorted(registry_names)}"
    )

    # 2. Cardinality
    assert len(formats) <= len(registry), (
        f"{label} '{field}' returned {len(formats)} formats but registry has only "
        f"{len(registry)} -- cannot produce more formats than the catalog"
    )

    # 3. Uniqueness
    if returned_names:
        seen: set[str | None] = set()
        dupes = [n for n in returned_names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        assert not dupes, f"{label} '{field}' returned duplicate format names: {dupes}"


# -- Filter content helpers ----------------------------------------------------


def _get_format_id_str(f: Any) -> str | None:
    """Extract the format_id.id string from a Format object."""
    fid = getattr(f, "format_id", None)
    if fid is None:
        return None
    return getattr(fid, "id", None)


def _is_format_responsive(f: Any) -> bool:
    """Check if a format is responsive by examining renders.dimensions.responsive."""
    renders = getattr(f, "renders", None) or []
    for r in renders:
        dims = getattr(r, "dimensions", None)
        if dims:
            responsive = getattr(dims, "responsive", None)
            if responsive:
                if getattr(responsive, "width", False) or getattr(responsive, "height", False):
                    return True
    return False


def _has_render_dimensions(f: Any) -> bool:
    """Check if a format has at least one render with width or height."""
    renders = getattr(f, "renders", None) or []
    for r in renders:
        dims = getattr(r, "dimensions", None)
        if dims:
            w = getattr(dims, "width", None)
            h = getattr(dims, "height", None)
            if w is not None or h is not None:
                return True
    return False


def _get_disclosure_positions(f: Any) -> set[str]:
    """Extract supported disclosure position strings from a Format."""
    positions = getattr(f, "supported_disclosure_positions", None) or []
    return {p.value if isinstance(p, Enum) else str(p) for p in positions}


def _get_disclosure_persistence(f: Any) -> set[str]:
    """Every persistence mode any position in a Format's ``disclosure_capabilities`` declares."""
    return {
        m.value if isinstance(m, Enum) else str(m)
        for c in (getattr(f, "disclosure_capabilities", None) or [])
        for m in c.persistence
    }


def _get_linked_format_ids(f: Any, attr: str) -> set[str]:
    """Extract linked format ID strings from output_format_ids or input_format_ids."""
    linked = getattr(f, attr, None) or []
    return {fid.id for fid in linked if getattr(fid, "id", None)}


def _assert_filter_content(ctx: dict, field: str, label: str) -> None:
    """Assert that the returned formats satisfy the filter field's semantics.

    This is the discriminating assertion that goes beyond subset-of-registry.
    It compares the returned set against the registry to infer whether filtering
    narrowed the result, then verifies field-specific properties.
    """
    formats = _get_formats(ctx)
    registry = ctx.get("registry_formats", [])
    registry_names = {_fmt_name(r) for r in registry}
    returned_names = {_fmt_name(f) for f in formats}
    was_narrowed = bool(registry) and returned_names != registry_names

    # There is deliberately no ``field == "type"`` branch. It asserted
    # ``not was_narrowed`` -- that type filtering had NOT happened -- for the two
    # outlines whose When steps dispatched an unfiltered request, so ten rows read green
    # for asserting the absence of a behaviour this seller does not implement and is not
    # obliged to. ``type`` is not a parameter of the media-buy
    # list-creative-formats request (removed at the 3.0 major; 2.5 had it) and survives
    # only on the "(Creative Agent)" variant. Both outlines are deleted; a ``type``
    # sentence reaching here now falls through to the shared checks below, which is the
    # honest answer for a field this request does not carry.
    if field == "format_ids":
        known_ids = ctx.get("known_format_ids", [])
        if known_ids:
            known_id_set = {fid.id for fid in known_ids}
            for f in formats:
                fid_str = _get_format_id_str(f)
                assert fid_str is not None, f"{label} 'format_ids': format '{_fmt_name(f)}' has no format_id.id"
                if was_narrowed:
                    assert fid_str in known_id_set, (
                        f"{label} 'format_ids': returned format '{_fmt_name(f)}' "
                        f"has id '{fid_str}' which is not in known_format_ids "
                        f"{sorted(known_id_set)}"
                    )

    elif field == "asset_types":
        if was_narrowed:
            for f in formats:
                types = asset_types_of(f)
                assert types, (
                    f"{label} 'asset_types': format '{_fmt_name(f)}' survived "
                    f"asset_types filtering but has no asset types -- "
                    f"the filter should have excluded it"
                )

    elif field == "dimension":
        if was_narrowed:
            for f in formats:
                assert _has_render_dimensions(f), (
                    f"{label} 'dimension': format '{_fmt_name(f)}' survived "
                    f"dimension filtering but has no render dimensions -- "
                    f"the filter should have excluded it"
                )

    elif field == "responsive":
        if was_narrowed and formats:
            responsive_values = {_is_format_responsive(f) for f in formats}
            assert len(responsive_values) == 1, (
                f"{label} 'responsive': after filtering, returned formats "
                f"have mixed responsiveness values {responsive_values} -- "
                f"a responsiveness filter should yield a homogeneous set"
            )

    elif field == "name search":
        if was_narrowed and formats:
            for f in formats:
                name = _fmt_name(f)
                assert name is not None, (
                    f"{label} 'name search': format survived name_search filtering but has no name attribute"
                )

    elif field == "wcag":
        if was_narrowed:
            for f in formats:
                acc = getattr(f, "accessibility", None)
                assert acc is not None, (
                    f"{label} 'wcag': format '{_fmt_name(f)}' survived wcag "
                    f"filtering but has no accessibility info -- the filter "
                    f"should have excluded formats without WCAG levels"
                )
                wcag_val = getattr(acc, "wcag_level", None)
                assert wcag_val is not None, (
                    f"{label} 'wcag': format '{_fmt_name(f)}' has accessibility info but wcag_level is None"
                )

    elif field in ("disclosure_positions", "disclosure"):
        if was_narrowed and formats:
            for f in formats:
                positions = _get_disclosure_positions(f)
                assert positions, (
                    f"{label} '{field}': format '{_fmt_name(f)}' survived "
                    f"disclosure filtering but has no supported_disclosure_positions"
                )

    elif field in ("disclosure_persistence", "persistence"):
        # EXACT SET, not "every survivor declares some persistence": the request schema
        # keeps a format iff "each requested persistence mode is supported by at least one
        # position in disclosure_capabilities" (list-creative-formats-request.json,
        # /properties/disclosure_persistence), so the seeded catalog determines the answer.
        # The When records the members it sent; None is the omitted filter, which keeps all.
        requested = ctx["requested_disclosure_persistence"]
        expected_names = (
            registry_names
            if requested is None
            else {_fmt_name(r) for r in registry if set(requested) <= _get_disclosure_persistence(r)}
        )
        assert returned_names == expected_names, (
            f"{label} '{field}' {requested}: returned {sorted(n for n in returned_names if n)}, "
            f"the formats declaring every requested mode are {sorted(n for n in expected_names if n)}"
        )

    elif field == "output_format_ids":
        known_out = ctx.get("known_output_format_ids", [])
        if was_narrowed and known_out:
            known_out_set = {fid.id for fid in known_out}
            for f in formats:
                linked = _get_linked_format_ids(f, "output_format_ids")
                assert linked & known_out_set, (
                    f"{label} 'output_format_ids': format '{_fmt_name(f)}' "
                    f"survived filtering but its output_format_ids "
                    f"{sorted(linked)} do not overlap with the requested IDs "
                    f"{sorted(known_out_set)}"
                )

    elif field == "input_format_ids":
        known_in = ctx.get("known_input_format_ids", [])
        if was_narrowed and known_in:
            known_in_set = {fid.id for fid in known_in}
            for f in formats:
                linked = _get_linked_format_ids(f, "input_format_ids")
                assert linked & known_in_set, (
                    f"{label} 'input_format_ids': format '{_fmt_name(f)}' "
                    f"survived filtering but its input_format_ids "
                    f"{sorted(linked)} do not overlap with the requested IDs "
                    f"{sorted(known_in_set)}"
                )

    elif field in _CREATIVE_AGENT_FIELDS:
        for f in formats:
            assert _fmt_name(f) is not None, f"{label} '{field}': returned format is missing a name"
            assert _get_format_id_str(f) is not None, (
                f"{label} '{field}': returned format '{_fmt_name(f)}' is missing format_id.id"
            )


# -- Step definitions ----------------------------------------------------------


@then(parsers.re(r"the (?P<field>.+) filtering should result in (?P<expected>\w+)"))
def then_partition_filtering_result(ctx: dict, field: str, expected: str) -> None:
    """Generic partition test: field filtering should result in expected.

    For expected == "valid":
    1. Verifies the response is well-formed (ListCreativeFormatsResponse).
    2. Verifies returned formats are a subset of the seeded registry.
    3. Verifies field-specific filter semantics: that the returned formats
       actually satisfy the property constraint implied by the filter field.

    For expected == "invalid": verifies error presence and no response.
    """
    _assert_partition_outcome(ctx, field, expected)
    if expected == "valid":
        _assert_returned_formats_subset_of_registry(ctx, field, label="Filter")
        _assert_filter_content(ctx, field, label="Filter")


# Domain boundary handlers for then_boundary_handling_result (see ).
# Each handler takes (ctx, field, expected) and returns True if it handled the
# field, else False. This lets a domain step module (e.g. uc004_delivery) own its
# boundary-handling logic without the generic module importing domain code, while
# keeping a single global registration of the "X handling should be Y" step
# (pytest-bdd's step registry is global — a second registration would shadow this
# one for ALL features, including UC-005 creative formats).
_BOUNDARY_HANDLERS: list = []


def register_boundary_handler(handler):
    """Register a domain boundary handler for ``then_boundary_handling_result``."""
    _BOUNDARY_HANDLERS.append(handler)
    return handler


@then(parsers.re(r"the (?P<field>.+) handling should be (?P<expected>\w+)"))
def then_boundary_handling_result(ctx: dict, field: str, expected: str) -> None:
    """Generic boundary test: field handling should be expected.

    Dispatches to registered domain handlers first (e.g. the delivery handler in
    uc004_delivery). If no domain handler claims the field, falls back to the
    creative-format (UC-005) catalog checks owned by this module.
    """
    for handler in _BOUNDARY_HANDLERS:
        if handler(ctx, field, expected):
            return

    # Creative format domain (UC-005): catalog checks
    _assert_partition_outcome(ctx, field, expected)
    if expected == "valid":
        _assert_returned_formats_subset_of_registry(ctx, field, label="Boundary")
        _assert_filter_content(ctx, field, label="Boundary")
