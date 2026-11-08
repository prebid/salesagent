"""Given steps for seller CONFIGURATION: which ad server the tenant runs, and the
format registry it serves.

Most of this module is the format half. The tenant's ad server lives here too
because it is the same kind of fact — something configured about the seller, asked
by more than one domain — and a Given that names it belongs where every feature can
reach it rather than inside one domain's step module.

The format steps populate ``ctx["registry_formats"]`` with real Format objects
for invariant and edge-case scenarios. They use pytest-bdd data tables where
the feature file includes ``| col | col |`` rows.

Each step pushes the updated registry_formats to the CreativeFormatsEnv
harness via ``_sync_registry(ctx)``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from adcp.types.domains.core.requirements.image_asset_requirements import (  # no stable alias in adcp.types
    ImageAssetRequirements,
)
from pytest_bdd import given, parsers, when

from tests.bdd.steps.generic._registry import sync_registry as _sync_registry
from tests.bdd.steps.generic._table import rows as table_rows
from tests.factories.format import (
    CATEGORY_MAP,
    FormatFactory,
    FormatIdFactory,
    make_asset,
    make_asset_group,
    make_disclosure_capabilities,
    make_fixed_renders,
    make_renders,
    make_responsive_renders,
)
from tests.factories.webhook import PushNotificationConfigRequestFactory


@given(parsers.parse('the tenant runs the "{adapter_type}" ad server'))
def given_ad_server(ctx: dict, adapter_type: str) -> None:
    """The tenant genuinely runs *adapter_type* as its ad server.

    Generic because it says something about the TENANT and two domains now ask it:
    publisher authorization (which reads the adapter the tenant declares) and
    BR-UC-002's sandbox-isolation pair (which needs a LIVE ad server configured so
    that reaching it is observable). Each env realizes the sentence its own way —
    ``PublisherAuthorizationEnv.run_ad_server`` writes the row,
    ``MediaBuyCreateEnv.run_ad_server`` writes the row and stops faking the adapter
    decision, because an env that keeps faking it is not running this ad server.

    One sentence, one binding, the env realizes it: the step says WHAT is true and
    never branches on which env is answering.
    """
    ctx["env"].run_ad_server(adapter_type)


def _add_format(ctx: dict, fmt: object) -> None:
    """Add a format to the registry.

    On the first call within a scenario, clears the Background-seeded
    real catalog so that invariant tests get only the formats they
    explicitly define. Subsequent calls within the same scenario append.
    """
    if not ctx.get("_config_replaced"):
        ctx["registry_formats"] = []
        ctx["_config_replaced"] = True
    ctx["registry_formats"].append(fmt)


def _datatable_to_dicts(datatable: Sequence[Sequence[object]]) -> list[dict[str, str]]:
    """Convert pytest-bdd raw datatable (list of lists) to list of dicts.

    The first row is treated as column headers. Remaining rows become dicts
    keyed by those headers.
    """
    return table_rows(datatable)


# ── Format by type + asset type ──────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" of type "{fmt_type}" with asset type "{asset_type}"'))
def given_registry_format_typed(ctx: dict, name: str, fmt_type: str, asset_type: str) -> None:
    """Register a single format with explicit type and asset type."""
    fmt = FormatFactory.build(name=name, type=CATEGORY_MAP.get(fmt_type), assets=[make_asset(asset_type)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Format with format_id ───────────────────────────────────────────


@given(
    parsers.re(
        r'the registry has format "(?P<name>[^"]+)" with format_id id "(?P<fmt_id>[^"]+)"'
        r'(?: declared by agent "(?P<agent_url>[^"]+)")?'
    )
)
def given_registry_format_with_id(ctx: dict, name: str, fmt_id: str, agent_url: str | None) -> None:
    """Register a format with a known format_id, declared by *agent_url* when the sentence names one.

    Without the clause the format_id carries the FormatIdFactory default agent_url, which
    is the agent the format_ids request step names. core/format-id.json identifies a
    format "by its declaring agent and local slug", so a format declared by another agent
    under the same id is a different format: the clause is how a scenario puts one in the
    catalog. Both are served verbatim by the tenant's peer agent, so the listing carries
    each identity as declared. A regex rather than ``parsers.parse``: parse's ``{fmt_id}``
    would also swallow the clause, binding the longer sentence to the shorter form.
    """
    fid = FormatIdFactory.build(id=fmt_id, **({"agent_url": agent_url} if agent_url else {}))
    fmt = FormatFactory.build(name=name, format_id=fid)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Format with asset type(s) ───────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with assets of type "{asset_type}"'))
def given_registry_format_with_asset(ctx: dict, name: str, asset_type: str) -> None:
    """Register a format with a single asset type."""
    fmt = FormatFactory.build(name=name, assets=[make_asset(asset_type)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with assets of types "{type_a}" and "{type_b}"'))
def given_registry_format_with_two_assets(ctx: dict, name: str, type_a: str, type_b: str) -> None:
    """Register a format with two asset types."""
    fmt = FormatFactory.build(name=name, assets=[make_asset(type_a), make_asset(type_b)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(
    parsers.parse('the registry has format "{name}" with a repeatable asset group containing "{type_a}" and "{type_b}"')
)
def given_registry_format_with_asset_group(ctx: dict, name: str, type_a: str, type_b: str) -> None:
    """Register a format with a repeatable asset group.

    Uses the AdCP Assets18 repeatable_group structure which groups assets
    that repeat together as a unit, distinct from individual assets.
    """
    fmt = FormatFactory.build(name=name, assets=[make_asset_group(type_a, type_b)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(
    parsers.parse(
        'the registry has format "{name}" with an individual "{asset_type}" asset'
        ' and a repeatable asset group containing "{type_a}" and "{type_b}"'
    )
)
def given_registry_format_with_asset_and_group(ctx: dict, name: str, asset_type: str, type_a: str, type_b: str) -> None:
    """Register a format holding one individual asset beside a repeatable asset group."""
    fmt = FormatFactory.build(name=name, assets=[make_asset(asset_type), make_asset_group(type_a, type_b)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Format with render dimensions ────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with renders:'))
def given_registry_format_with_renders(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with render dimensions from a data table."""
    rows = _datatable_to_dicts(datatable)
    renders = [make_renders(width=int(row["width"]), height=int(row["height"])) for row in rows]
    fmt = FormatFactory.build(name=name, renders=renders)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with render width {width:d} and height {height:d}'))
def given_registry_format_exact_dimensions(ctx: dict, name: str, width: int, height: int) -> None:
    """Register a format with exact render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_renders(width=width, height=height)])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no render dimensions'))
def given_registry_format_no_dimensions(ctx: dict, name: str) -> None:
    """Register a format with no render dimension information."""
    fmt = FormatFactory.build(name=name)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with responsive render dimensions'))
def given_registry_format_responsive(ctx: dict, name: str) -> None:
    """Register a format with responsive render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_responsive_renders()])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with non-responsive render dimensions'))
def given_registry_format_non_responsive(ctx: dict, name: str) -> None:
    """Register a format with non-responsive (fixed) render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_fixed_renders()])
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Format with name ────────────────────────────────────────────────


@given(parsers.parse('the registry has format named "{name}"'))
def given_registry_format_named(ctx: dict, name: str) -> None:
    """Register a format with just a name."""
    fmt = FormatFactory.build(name=name)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Format with disclosure positions ─────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with supported_disclosure_positions {positions}'))
def given_registry_format_disclosure(ctx: dict, name: str, positions: str) -> None:
    """Register a format with supported disclosure positions.

    Positions are parsed from JSON array notation, e.g. ``["prominent", "footer"]``.
    """
    parsed = json.loads(positions)
    fmt = FormatFactory.build(name=name, supported_disclosure_positions=parsed)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no supported_disclosure_positions field'))
def given_registry_format_no_disclosure(ctx: dict, name: str) -> None:
    """Register a format without a supported_disclosure_positions field."""
    fmt = FormatFactory.build(name=name, supported_disclosure_positions=None)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('"{name}" has no supported_disclosure_positions field'))
def given_registered_format_drops_flat_disclosure(ctx: dict, name: str) -> None:
    """The already-registered format *name* carries no flat ``supported_disclosure_positions``.

    Re-seeds the catalog with that one field cleared, so a scenario pairing it with
    ``disclosure_capabilities`` grades the request schema's two-source rule on
    ``disclosure_positions`` -- "When a format has disclosure_capabilities, match against
    those positions. Otherwise fall back to supported_disclosure_positions" -- with no flat
    list for a filter to fall back to.
    """
    formats = ctx.get("registry_formats", [])
    matches = [i for i, fmt in enumerate(formats) if fmt.name == name]
    assert len(matches) == 1, f"expected one registered format named {name!r}, found {len(matches)}"
    formats[matches[0]] = formats[matches[0]].model_copy(update={"supported_disclosure_positions": None})
    _sync_registry(ctx)


# ── Format with disclosure_capabilities (positions × persistence) ────


@given(parsers.parse('the registry has format "{name}" with disclosure_capabilities positions {positions}'))
def given_registry_format_capability_positions(ctx: dict, name: str, positions: str) -> None:
    """Register a format declaring *positions* through ``disclosure_capabilities``.

    ``core/format.json`` requires ``persistence`` on every entry (``minItems: 1``) and the
    sentence names positions only, so each position declares ``flexible`` -- the mode
    ``enums/disclosure-persistence.json`` defines as "presence is sufficient", the one that
    claims nothing beyond the position itself.
    """
    capabilities = make_disclosure_capabilities(*((position, ["flexible"]) for position in json.loads(positions)))
    _add_format(ctx, FormatFactory.build(name=name, disclosure_capabilities=capabilities))
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with disclosure_capabilities:'))
def given_registry_format_capabilities_table(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format whose ``disclosure_capabilities`` are the table's rows.

    One row per position (the pin: "Each position MUST appear at most once"); the
    ``persistence`` cell names that position's modes, comma-separated.
    """
    capabilities = make_disclosure_capabilities(
        *(
            (row["position"], [m.strip() for m in row["persistence"].split(",")])
            for row in _datatable_to_dicts(datatable)
        )
    )
    _add_format(ctx, FormatFactory.build(name=name, disclosure_capabilities=capabilities))
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no disclosure_capabilities field'))
def given_registry_format_no_capabilities(ctx: dict, name: str) -> None:
    """Register a format without ``disclosure_capabilities``, so it declares no persistence mode."""
    _add_format(ctx, FormatFactory.build(name=name, disclosure_capabilities=None))
    _sync_registry(ctx)


# ── Seller catalog: a format by what it carries ──────────────────────


@given(parsers.parse('the seller catalog has at least one format containing an "{asset_type}" asset'))
def given_catalog_format_with_asset_type(ctx: dict, asset_type: str) -> None:
    """The seller serves one format whose single asset slot is of *asset_type*.

    ``make_asset`` builds every member of ``enums/asset-content-type.json`` and refuses a
    type the pinned assets union does not model, so a row naming a non-member fails here
    rather than seeding a different asset.
    """
    _add_format(ctx, FormatFactory.build(name=f"{asset_type}-format", assets=[make_asset(asset_type)]))
    _sync_registry(ctx)


@given(parsers.parse("the seller catalog has a format with a required image asset capped at {megabytes:d} MB"))
def given_catalog_format_with_capped_image(ctx: dict, megabytes: int) -> None:
    """The seller serves one format with a required image asset capped at *megabytes* MB.

    The cap is ``max_file_size_kb`` under the slot's ``requirements``:
    ``core/requirements/image-asset-requirements.json`` declares the member in kilobytes,
    so *megabytes* MB is ``megabytes * 1024`` KB. The format is remembered as the one a
    following "for that format" request names.
    """
    image = make_asset("image", requirements=ImageAssetRequirements(max_file_size_kb=megabytes * 1024))
    fmt = FormatFactory.build(name="capped-image-format", assets=[image])
    _add_format(ctx, fmt)
    ctx["target_format"] = fmt
    _sync_registry(ctx)


# ── Format with output_format_ids / input_format_ids (data table) ────


@given(parsers.parse('the registry has format "{name}" with output_format_ids:'))
def given_registry_format_output_ids(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with output_format_ids from a data table."""
    rows = _datatable_to_dicts(datatable)
    ids = [FormatIdFactory.build(agent_url=row["agent_url"], id=row["id"]) for row in rows]
    fmt = FormatFactory.build(name=name, output_format_ids=ids)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no output_format_ids field'))
def given_registry_format_no_output_ids(ctx: dict, name: str) -> None:
    """Register a format without output_format_ids."""
    fmt = FormatFactory.build(name=name, output_format_ids=None)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with input_format_ids:'))
def given_registry_format_input_ids(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with input_format_ids from a data table."""
    rows = _datatable_to_dicts(datatable)
    ids = [FormatIdFactory.build(agent_url=row["agent_url"], id=row["id"]) for row in rows]
    fmt = FormatFactory.build(name=name, input_format_ids=ids)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no input_format_ids field'))
def given_registry_format_no_input_ids(ctx: dict, name: str) -> None:
    """Register a format without input_format_ids."""
    fmt = FormatFactory.build(name=name, input_format_ids=None)
    _add_format(ctx, fmt)
    _sync_registry(ctx)


# ── Formats from data table ─────────────────────────────────────────


@given("the registry has formats:")
def given_registry_formats_table(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Register formats from a data table whose only required column is ``name``.

    ``type`` is optional because adcp 3.12 removed it from ``Format``. A table that still
    declares the column keeps working; one that does not no longer dies on ``KeyError:
    'type'`` before the scenario runs.
    """
    rows = _datatable_to_dicts(datatable)
    formats = [
        FormatFactory.build(name=row["name"], **({"type": CATEGORY_MAP.get(row["type"])} if "type" in row else {}))
        for row in rows
    ]
    ctx["registry_formats"] = formats
    _sync_registry(ctx)


# ── Formats from inline list ────────────────────────────────────────


# Two INLINE `the registry has formats: "<name>" (<type>), ...` Givens bound here, neither
# reachable: the only registry-formats sentence in any feature is the DATATABLE form at
# BR-UC-005-discover-creative-formats.feature:106, which a different step serves. Both also
# took a `(<type>)` column, and adcp 3.12 removed `type` from Format.


@given(parsers.parse('the request includes a push_notification_config with url "{url}"'))
@when(parsers.parse('the request includes a push_notification_config with url "{url}"'))
def push_notification_config_with_url(ctx: dict, url: str) -> None:
    """Attach a push_notification_config to the upcoming dispatch.

    REGISTERED UNDER BOTH KEYWORDS, on purpose. Both feature lines that use this
    sentence write it as ``And``, which inherits whichever keyword came before --
    and that is how it ended up defined as ``@given`` in
    ``steps/domain/uc006_sync_creatives.py`` and ``@when`` in
    ``steps/domain/uc011_accounts.py``. A sentence whose keyword depends on its
    neighbour cannot be owned by one keyword.

    The two copies were not equivalent, and the difference was a live defect: the
    @given one set ``push_notification_config`` (which the dispatch reads) AND a
    ``push_notification_url`` mirror; the @when one set only the mirror. Scenarios
    routed to the @when copy therefore dispatched with NO webhook config, while the
    Then step that checks "the system registered the webhook" fell back to the url
    key and passed anyway. Both the fallback and the mirror are gone: the config is
    the one key, so a scenario that fails to attach one now fails.
    """
    attach_push_notification_config(ctx, url)


def attach_push_notification_config(ctx: dict, url: str) -> dict:
    """Put a push_notification_config on the context, ONE way, and return it.

    THE OWNER OF THE CTX PROTOCOL, not just of the payload shape. The factory
    already deduplicated what a config LOOKS like; this deduplicates what
    attaching one MEANS, which is where the copies actually diverged:

      * this module set ``push_notification_config`` and a ``push_notification_url``
        mirror but never ``request_kwargs``;
      * ``given_media_buy.given_media_buy_with_push_config`` set the config and
        ``request_kwargs["push_notification_config"]`` but never the url.

    So which keys a scenario ended up with depended on which sentence it happened
    to use, and a Then step reading a key the other sentence never wrote passed on
    a fallback rather than on the thing it names. That is the same defect the
    docstring above describes between the @given and @when copies, one level up:
    the sentences were unified and the STATE THEY LEAVE was not.

    Writes the config, and ``request_kwargs`` only when the scenario has one,
    because creating it here would hand a create-shaped bag to a scenario that
    dispatches something else. The url mirror is NOT written: once the Then step
    stopped falling back to it, nothing read it, and a second spelling of a fact
    is how the two sentences disagreed in the first place.
    """
    config = PushNotificationConfigRequestFactory.payload(url=url)
    ctx["push_notification_config"] = config
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["push_notification_config"] = config
    return config
