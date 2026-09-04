"""Guard: every property of the two TMP wire schemas is produced or reasoned about.

Schema validation of an emitted body is one-directional. It grades the keys that
ARE present and is silent about keys the write path can never produce — so a
member the spec defines and this codebase simply never fills looks identical to a
member that does not apply. `tmpx_macros` was invisible that way for the whole
feature: no column, no field, no form input, no mapping, while the schema says a
provider emitting TMPX "MUST also register this list" (#1197 review).

This reads the pinned property set and requires each property to be either
produced by the write path or listed here with a reason. Adding a property
upstream fails this test until someone decides which it is — the same "read the
authority" move `TestValueConstraintsComeFromTheSchema` makes for the numeric
bounds.
"""

from __future__ import annotations

from adcp.types import AvailablePackage

from src.routes.tmp_providers import PROVIDER_REGISTRATION_SCHEMA
from src.services.tmp_provider_sync import AVAILABLE_PACKAGE_SCHEMA
from tests.helpers.pinned_schema import load as load_pinned_schema

#: Registration properties this codebase deliberately does not carry, with why.
#: An entry here is a decision on the record; a property in neither this map nor
#: the emitted set fails the test.
_REGISTRATION_OMISSIONS: dict[str, str] = {
    "tmpx_macros": (
        "Registering TMPX macro names is its own feature: it is meaningful only for a "
        "provider that emits TMPX on its identity-match response, which this agent "
        "neither produces nor consumes, and the schema cannot express that predicate "
        "(\"the schema cannot enforce this because 'emits TMPX' is not a "
        'schema-visible predicate"). Carrying it would mean an admin field an '
        "operator has no way to fill correctly. Revisit with TMPX support."
    ),
}

#: Sync-body properties deliberately not carried, with why.
_PACKAGE_OMISSIONS: dict[str, str] = {
    "catalogs": (
        "Product catalogs are not modelled on the media-buy package: there is no "
        "catalog concept on MediaPackage or in the create request, so there is no row "
        "value to send. Populating it would require the catalog feature (#1205's "
        "get_products TMP capability), not a mapping change."
    ),
}


def _emitted_registration_properties() -> set[str]:
    """The keys the discovery entry can put on the wire."""
    # The union's branch models carry the field set; either branch is the whole set
    # apart from the match-mode discriminator, so take both.
    from src.core.schemas.tmp_provider import _ContextMatchEntry, _IdentityMatchEntry

    return set(_ContextMatchEntry.model_fields) | set(_IdentityMatchEntry.model_fields)


def test_every_registration_property_is_produced_or_reasoned_about():
    schema_properties = set(load_pinned_schema(PROVIDER_REGISTRATION_SCHEMA)["properties"])
    emitted = _emitted_registration_properties()

    unaccounted = schema_properties - emitted - set(_REGISTRATION_OMISSIONS)
    assert not unaccounted, (
        f"{PROVIDER_REGISTRATION_SCHEMA} properties neither emitted nor listed as a reasoned "
        f"omission: {sorted(unaccounted)}"
    )


def test_every_package_property_is_produced_or_reasoned_about():
    schema_properties = set(load_pinned_schema(AVAILABLE_PACKAGE_SCHEMA)["properties"])
    emitted = set(AvailablePackage.model_fields)

    unaccounted = schema_properties - emitted - set(_PACKAGE_OMISSIONS)
    assert not unaccounted, (
        f"{AVAILABLE_PACKAGE_SCHEMA} properties neither emitted nor listed as a reasoned "
        f"omission: {sorted(unaccounted)}"
    )


def test_the_omission_maps_name_only_real_properties():
    """A reason for a property that no longer exists is stale — delete it.

    Keeps the maps from accumulating entries for members a spec bump removed, which
    would let a genuinely-unaccounted property hide behind a stale key.
    """
    registration = set(load_pinned_schema(PROVIDER_REGISTRATION_SCHEMA)["properties"])
    package = set(load_pinned_schema(AVAILABLE_PACKAGE_SCHEMA)["properties"])

    assert set(_REGISTRATION_OMISSIONS) <= registration, sorted(set(_REGISTRATION_OMISSIONS) - registration)
    assert set(_PACKAGE_OMISSIONS) <= package, sorted(set(_PACKAGE_OMISSIONS) - package)


def test_the_write_path_actually_fills_format_ids():
    """``format_ids`` is claimed as emitted, so the write path must really fill it.

    Without this, moving ``format_ids`` into the omission map would be an easier
    way to make the test above pass than populating it.
    """
    from unittest.mock import MagicMock

    from src.services.tmp_provider_sync import _build_package_payload

    pkg = MagicMock()
    pkg.package_id = "pkg-fmt"
    pkg.package_config = {
        "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
    }

    built = _build_package_payload("mb-1", pkg, "https://agent.example.com/mcp")
    wire = built.model_dump(mode="json", exclude_none=True)

    assert wire["format_ids"] == [{"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"}], (
        wire["format_ids"]
    )


def test_a_package_without_formats_omits_the_member():
    """The member is omitted, never sent empty — the schema types it as an array."""
    from unittest.mock import MagicMock

    from src.services.tmp_provider_sync import _build_package_payload

    pkg = MagicMock()
    pkg.package_id = "pkg-nofmt"
    pkg.package_config = {}

    wire = _build_package_payload("mb-1", pkg, "https://agent.example.com/mcp").model_dump(
        mode="json", exclude_none=True
    )

    assert "format_ids" not in wire
