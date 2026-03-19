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

from pytest_bdd import given, parsers

from tests.bdd.steps.generic._registry import sync_registry as _sync_registry
from tests.factories.format import (
    CATEGORY_MAP,
    FormatFactory,
    FormatIdFactory,
    make_asset,
    make_fixed_renders,
    make_renders,
    make_responsive_renders,
)


def _datatable_to_dicts(datatable: Sequence[Sequence[object]]) -> list[dict[str, str]]:
    """Convert pytest-bdd raw datatable (list of lists) to list of dicts.

    The first row is treated as column headers. Remaining rows become dicts
    keyed by those headers.
    """
    headers = [str(cell) for cell in datatable[0]]
    return [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]


# ── Format by type + asset type ──────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" of type "{fmt_type}" with asset type "{asset_type}"'))
def given_registry_format_typed(ctx: dict, name: str, fmt_type: str, asset_type: str) -> None:
    """Register a single format with explicit type and asset type."""
    fmt = FormatFactory.build(name=name, type=CATEGORY_MAP[fmt_type], assets=[make_asset(asset_type)])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with format_id ───────────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with format_id id "{fmt_id}"'))
def given_registry_format_with_id(ctx: dict, name: str, fmt_id: str) -> None:
    """Register a format with a known format_id."""
    fid = FormatIdFactory.build(agent_url="https://creatives.adcontextprotocol.org", id=fmt_id)
    fmt = FormatFactory.build(name=name, format_id=fid)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with asset type(s) ───────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with assets of type "{asset_type}"'))
def given_registry_format_with_asset(ctx: dict, name: str, asset_type: str) -> None:
    """Register a format with a single asset type."""
    fmt = FormatFactory.build(name=name, assets=[make_asset(asset_type)])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with assets of types "{type_a}" and "{type_b}"'))
def given_registry_format_with_two_assets(ctx: dict, name: str, type_a: str, type_b: str) -> None:
    """Register a format with two asset types."""
    fmt = FormatFactory.build(name=name, assets=[make_asset(type_a), make_asset(type_b)])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(
    parsers.parse('the registry has format "{name}" with a repeatable asset group containing "{type_a}" and "{type_b}"')
)
def given_registry_format_with_asset_group(ctx: dict, name: str, type_a: str, type_b: str) -> None:
    """Register a format with a repeatable asset group.

    Asset groups are flattened to individual assets for filtering purposes.
    """
    fmt = FormatFactory.build(name=name, assets=[make_asset(type_a), make_asset(type_b)])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with render dimensions ────────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with renders:'))
def given_registry_format_with_renders(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with render dimensions from a data table."""
    rows = _datatable_to_dicts(datatable)
    renders = [make_renders(width=int(row["width"]), height=int(row["height"])) for row in rows]
    fmt = FormatFactory.build(name=name, renders=renders)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with render width {width:d} and height {height:d}'))
def given_registry_format_exact_dimensions(ctx: dict, name: str, width: int, height: int) -> None:
    """Register a format with exact render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_renders(width=width, height=height)])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no render dimensions'))
def given_registry_format_no_dimensions(ctx: dict, name: str) -> None:
    """Register a format with no render dimension information."""
    fmt = FormatFactory.build(name=name)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with responsive render dimensions'))
def given_registry_format_responsive(ctx: dict, name: str) -> None:
    """Register a format with responsive render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_responsive_renders()])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with non-responsive render dimensions'))
def given_registry_format_non_responsive(ctx: dict, name: str) -> None:
    """Register a format with non-responsive (fixed) render dimensions."""
    fmt = FormatFactory.build(name=name, renders=[make_fixed_renders()])
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with name ────────────────────────────────────────────────


@given(parsers.parse('the registry has format named "{name}"'))
def given_registry_format_named(ctx: dict, name: str) -> None:
    """Register a format with just a name."""
    fmt = FormatFactory.build(name=name)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with disclosure positions ─────────────────────────────────


@given(parsers.parse('the registry has format "{name}" with supported_disclosure_positions {positions}'))
def given_registry_format_disclosure(ctx: dict, name: str, positions: str) -> None:
    """Register a format with supported disclosure positions.

    Positions are parsed from JSON array notation, e.g. ``["prominent", "footer"]``.
    """
    parsed = json.loads(positions)
    fmt = FormatFactory.build(name=name, supported_disclosure_positions=parsed)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no supported_disclosure_positions field'))
def given_registry_format_no_disclosure(ctx: dict, name: str) -> None:
    """Register a format without a supported_disclosure_positions field."""
    fmt = FormatFactory.build(name=name)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Format with output_format_ids / input_format_ids (data table) ────


@given(parsers.parse('the registry has format "{name}" with output_format_ids:'))
def given_registry_format_output_ids(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with output_format_ids from a data table."""
    rows = _datatable_to_dicts(datatable)
    ids = [FormatIdFactory.build(agent_url=row["agent_url"], id=row["id"]) for row in rows]
    fmt = FormatFactory.build(name=name, output_format_ids=ids)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no output_format_ids field'))
def given_registry_format_no_output_ids(ctx: dict, name: str) -> None:
    """Register a format without output_format_ids."""
    fmt = FormatFactory.build(name=name)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with input_format_ids:'))
def given_registry_format_input_ids(ctx: dict, name: str, datatable: Sequence[Sequence[object]]) -> None:
    """Register a format with input_format_ids from a data table."""
    rows = _datatable_to_dicts(datatable)
    ids = [FormatIdFactory.build(agent_url=row["agent_url"], id=row["id"]) for row in rows]
    fmt = FormatFactory.build(name=name, input_format_ids=ids)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


@given(parsers.parse('the registry has format "{name}" with no input_format_ids field'))
def given_registry_format_no_input_ids(ctx: dict, name: str) -> None:
    """Register a format without input_format_ids."""
    fmt = FormatFactory.build(name=name)
    ctx.setdefault("registry_formats", []).append(fmt)
    _sync_registry(ctx)


# ── Formats from data table ─────────────────────────────────────────


@given("the registry has formats:")
def given_registry_formats_table(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Register multiple formats from a data table with name and type columns."""
    rows = _datatable_to_dicts(datatable)
    formats = [FormatFactory.build(name=row["name"], type=CATEGORY_MAP[row["type"]]) for row in rows]
    ctx.setdefault("registry_formats", []).extend(formats)
    _sync_registry(ctx)


# ── Formats from inline list ────────────────────────────────────────


@given(parsers.parse('the registry has formats: "{name_a}" ({type_a}), "{name_b}" ({type_b}), "{name_c}" ({type_c})'))
def given_registry_three_formats_inline(
    ctx: dict, name_a: str, type_a: str, name_b: str, type_b: str, name_c: str, type_c: str
) -> None:
    """Register three formats from inline notation."""
    ctx.setdefault("registry_formats", []).extend(
        [
            FormatFactory.build(name=name_a, type=CATEGORY_MAP[type_a]),
            FormatFactory.build(name=name_b, type=CATEGORY_MAP[type_b]),
            FormatFactory.build(name=name_c, type=CATEGORY_MAP[type_c]),
        ]
    )
    _sync_registry(ctx)


@given(parsers.parse('the registry has formats: "{name_a}" ({type_a}), "{name_b}" ({type_b})'))
def given_registry_two_formats_inline(ctx: dict, name_a: str, type_a: str, name_b: str, type_b: str) -> None:
    """Register two formats from inline notation."""
    ctx.setdefault("registry_formats", []).extend(
        [
            FormatFactory.build(name=name_a, type=CATEGORY_MAP[type_a]),
            FormatFactory.build(name=name_b, type=CATEGORY_MAP[type_b]),
        ]
    )
    _sync_registry(ctx)
