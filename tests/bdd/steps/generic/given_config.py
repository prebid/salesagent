"""Given steps for registry/configuration setup with specific format definitions.

These steps populate ``ctx["registry_formats"]`` with real Format objects
for invariant and edge-case scenarios. They use pytest-bdd data tables where
the feature file includes ``| col | col |`` rows.

Each step pushes the updated registry_formats to the CreativeFormatsEnv
harness via ``_sync_registry(ctx)``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pytest_bdd import given, parsers, when

from tests.bdd.steps.generic._registry import sync_registry as _sync_registry
from tests.bdd.steps.generic._table import rows as table_rows
from tests.factories.format import (
    CATEGORY_MAP,
    FormatFactory,
    FormatIdFactory,
    make_asset,
    make_asset_group,
    make_fixed_renders,
    make_renders,
    make_responsive_renders,
)
from tests.factories.webhook import PushNotificationConfigRequestFactory


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


@given(parsers.parse('the registry has format "{name}" with format_id id "{fmt_id}"'))
def given_registry_format_with_id(ctx: dict, name: str, fmt_id: str) -> None:
    """Register a format with a known format_id.

    Uses the FormatIdFactory default agent_url (the seller's own creative agent,
    matching the format_ids request step). v3.1 matches format_ids on the
    (agent_url, id) pair, so the seeded format and the requested reference MUST
    agree on agent_url — a prior "creatives" (plural) typo here diverged from the
    "creative" the request uses and was masked only by the old id-only filter.
    """
    fid = FormatIdFactory.build(id=fmt_id)
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
