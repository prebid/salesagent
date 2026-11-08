"""The ``CreativeAsset`` <-> ``creatives`` row mapper, graded by round trip.

Every one of the twelve pinned ``CreativeAsset`` fields that the table gives no column
(salesagent-dufcl) is carried here, written through :meth:`CreativeMapper.to_row` and read
back through :meth:`CreativeMapper.to_domain`, and the value that comes back is compared to
the value that went in. Eleven travel in the ``data`` blob; the twelfth, ``format_id``, is
the retained assertion the identity holds in two columns, and has its own case.

TEN OF THE ELEVEN SHARE ONE FIXTURE AND ``component_assets`` CANNOT JOIN THEM. The pinned
schema states an ``allOf`` over it: ``component_assets`` is REQUIRED when ``format_kind`` is
``coordinated_placements`` and FORBIDDEN otherwise. So it gets its own case, under that kind,
rather than a fixture bent to make one parametrization cover everything.

THE BLOB IS CROSSED AS JSON, not as a Python dict. ``Creative.data`` is a bare ``JSONType``,
so ``process_bind_param`` hands the dict to the engine's ``_pydantic_json_serializer``
(``pydantic_core.to_json``) and psycopg hands back the parsed document on the way out. A test
that compared Python objects on both sides of an in-memory row would never serialize
anything, and the fields whose values are nested pinned MODELS -- which is most of them --
would round-trip trivially. :func:`_crossing_the_column` is that serialization step, applied
between the write and the read.

WHAT THIS DOES NOT GRADE: the real Postgres leg. The column is JSONB and the crossing above
is the engine's own serializer with psycopg's parse stood in for by ``json.loads``, which is
faithful to the types but is not the database. The mapper reaches a repository in step 3, and
that is where an integration test against real Postgres belongs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId

from src.core.database.database_session import _pydantic_json_serializer
from src.core.database.mapping.creative import CREATIVE_MAPPER
from src.core.database.models import Creative as CreativeRow
from src.core.schemas._base import CreativeFormatIdentity
from src.core.schemas.creative import CreativeAssetRequest

_ASSET: dict[str, Any] = {"asset_type": "image", "url": "https://example.test/a.png", "width": 300, "height": 250}

#: One constructible value per blob-held pinned field, each carrying every member the pin
#: makes REQUIRED on its nested type -- an under-populated nested model would be refused on
#: the way in and the field would be measured as "cannot round-trip" for the wrong reason.
_BLOB_VALUES: dict[str, Any] = {
    "assets": {"main": _ASSET},
    "industry_identifiers": [{"type": "ad_id", "value": "ABCD1234567H"}],
    "inputs": [{"name": "brief", "context_description": "a summer sale banner"}],
    "placement_ids": ["plc_1", "plc_2"],
    "placement_refs": [{"placement_id": "plc_1", "publisher_domain": "example.test"}],
    "provenance": {"digital_source_type": "trained_algorithmic_media"},
    "representation_selection": {
        "creative_id": "cr_round_trip",
        "revision_id": "rev_1",
        "revision_content_digest": "sha256:" + "a" * 64,
        "selected_representation_id": "rep_1",
        "selected_output_digest": "sha256:" + "b" * 64,
        "strategy": "representation_order",
        "resolved_by": "seller",
    },
    "rights": [
        {"rights_id": "r_1", "rights_agent": {"url": "https://rights.test", "id": "ra_1"}, "uses": ["editorial"]}
    ],
    "tags": ["q1", "summer"],
    "weight": 0.5,
}

#: The eleventh, which the pin admits only under one kind. See the module docstring.
_COMPONENT_ASSETS: dict[str, Any] = {"logo": {"main": _ASSET}}

#: What the seller's own half of the blob looks like: keys that describe work this agent did
#: and that no pinned field declares. The write path composes them today
#: (``_build_creative_data``) and the listing reads them back.
_SELLER_DOCUMENT: dict[str, Any] = {
    "url": "https://cdn.example.test/a.png",
    "click_url": "https://example.test/click",
    "width": 300,
    "height": 250,
    "concept_id": "concept_7",
    "variables": [],
}

_OWNED: dict[str, Any] = {
    "tenant_id": "tenant_1",
    "principal_id": "principal_1",
    "status": "approved",
    "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    "updated_at": datetime(2026, 1, 3, 3, 4, 5, tzinfo=UTC),
    "group_id": "grp_1",
    "strategy_id": "strat_1",
    "approved_at": datetime(2026, 1, 4, 3, 4, 5, tzinfo=UTC),
    "approved_by": "reviewer@example.test",
}


def _asset(**overrides: Any) -> CreativeAssetRequest:
    """A creative asset carrying every blob-held pinned field the kind ``image`` admits."""
    payload: dict[str, Any] = {
        "creative_id": "cr_round_trip",
        "name": "Summer banner",
        "format_kind": "image",
        **_BLOB_VALUES,
    }
    # A ``format_id`` override REPLACES the canonical arm rather than sitting beside it: the
    # item's oneOf counts a present key, null included, so ``format_kind: None`` is not absent.
    if "format_id" in overrides:
        del payload["format_kind"]
    payload.update(overrides)
    return CreativeAssetRequest.model_validate(payload)


def _crossing_the_column(row: CreativeRow) -> CreativeRow:
    """The row as a read would see it: its ``data`` document after the JSON column.

    ``to_row`` leaves live pinned MODELS in the blob, which is what the column accepts and
    what the existing write path already stores. A read never sees those objects, so the
    mapper's read side has to be graded against the document, not against what it was handed.
    """
    row.data = json.loads(_pydantic_json_serializer(row.data))
    return row


def _written(
    obj: CreativeAssetRequest,
    identity: CreativeFormatIdentity | None = None,
    seller_document: dict[str, Any] | None = None,
) -> CreativeRow:
    return _crossing_the_column(
        CREATIVE_MAPPER.to_row(
            obj,
            identity=identity if identity is not None else CreativeFormatIdentity(kind="image"),
            seller_document=_SELLER_DOCUMENT if seller_document is None else seller_document,
            **_OWNED,
        )
    )


# ---------------------------------------------------------------------------
# The twelve
# ---------------------------------------------------------------------------


def test_the_blob_fields_are_exactly_the_pinned_fields_with_no_column() -> None:
    """The set the mapper carries is derived; this is what says the derivation found them.

    Without it, every per-field assertion below could be passing over an empty set, and the
    eleven could be any eleven names.
    """
    assert CREATIVE_MAPPER.blob_fields == frozenset(_BLOB_VALUES) | {"component_assets"}


@pytest.mark.parametrize("field", sorted(_BLOB_VALUES))
def test_a_blob_held_pinned_field_round_trips(field: str) -> None:
    """Written into ``data``, crossed as JSON, and read back equal."""
    obj = _asset()
    row = _written(obj)
    assert field in row.data, f"{field} never reached the row"
    assert getattr(CREATIVE_MAPPER.to_domain(row), field) == getattr(obj, field)


def test_component_assets_round_trips_under_the_one_kind_the_pin_admits_it_on() -> None:
    """The eleventh blob field, and the only one with a kind-conditional ``allOf``."""
    obj = _asset(format_kind="coordinated_placements", component_assets=_COMPONENT_ASSETS)
    row = _written(obj, CreativeFormatIdentity(kind="coordinated_placements"))
    assert "component_assets" in row.data
    assert CREATIVE_MAPPER.to_domain(row).component_assets == obj.component_assets


def test_the_retained_format_id_assertion_round_trips_through_its_two_columns() -> None:
    """The twelfth field. ``format_id`` has no column either: the identity holds it.

    Verbatim in ``legacy_format_id`` because the echo owes the buyer their own reference back,
    and canonically in ``legacy_format_key`` because comparing two of them canonicalizes
    ``agent_url`` first.
    """
    assertion = LibraryFormatId.model_validate({"agent_url": "https://agent.test", "id": "display_300x250"})
    obj = _asset(format_id=assertion)
    row = _written(obj, CreativeFormatIdentity.of_legacy(assertion, kind="image"))

    assert row.legacy_format_id == assertion.model_dump(exclude_none=True)
    assert row.legacy_format_key is not None
    assert row.format_kind == "image"

    back = CREATIVE_MAPPER.to_domain(row)
    # Compared as the DOCUMENT, which is what "verbatim" means and is the obligation the two
    # columns exist for. Not as an object: ``from_row`` validates the stored document into the
    # local ``FormatId`` subclass, and pydantic equality is class-sensitive, so an object
    # comparison would grade which class the identity builder happens to use.
    assert back.format_id is not None
    assert back.format_id.model_dump(exclude_none=True) == assertion.model_dump(exclude_none=True)
    # The pin makes the two arms exclusive, so the kind the assertion PROJECTS to stays off
    # the domain object even though the column holds it.
    assert back.format_kind is None


# ---------------------------------------------------------------------------
# What the row holds that the asset does not, and the other way round
# ---------------------------------------------------------------------------


def test_every_column_of_the_row_is_assigned() -> None:
    """A column left unassigned is the defect the coverage check exists to prevent.

    The check grades the DECLARATION; this grades the implementation that declaration
    licenses, which is a different claim -- a mapper can account for a column in ``overrides``
    and then not write it.

    ASSIGNED, not non-None, and the distinction is the whole point. Three columns here are
    legitimately NULL (a canonically-synced creative retains no legacy assertion and names no
    option), so a non-None assertion would grade the fixture rather than the mapper. The ORM
    records what was passed to the constructor in the instance ``__dict__``, so a column the
    mapper never mentioned is absent from it -- which is exactly "written as NULL with nothing
    reporting it".

    ``to_row`` directly, WITHOUT the JSON crossing: the crossing assigns ``data`` itself, so
    going through it would make this test green on a mapper that never wrote the blob.
    """
    row = CREATIVE_MAPPER.to_row(
        _asset(),
        identity=CreativeFormatIdentity(kind="image"),
        seller_document=_SELLER_DOCUMENT,
        **_OWNED,
    )
    assigned = set(row.__dict__) - {"_sa_instance_state"}
    unassigned = sorted({column.name for column in CreativeRow.__table__.columns} - assigned)
    assert unassigned == []


def test_the_sellers_own_blob_keys_pass_through_untouched() -> None:
    """They describe work this agent did and no pinned field declares them."""
    row = _written(_asset())
    for key, value in _SELLER_DOCUMENT.items():
        assert row.data[key] == value


def test_a_pinned_field_does_not_lose_to_a_stale_seller_key_of_the_same_name() -> None:
    """The domain object is the value, so it is written last and wins."""
    row = _written(_asset(), seller_document={"tags": ["stale"]})
    assert row.data["tags"] == ["q1", "summer"]


def test_an_omitted_pinned_field_leaves_no_key_in_the_blob() -> None:
    """The absent key and an explicit null read back identically, and only one is honest."""
    row = _written(_asset(tags=None), seller_document={})
    assert "tags" not in row.data
    assert CREATIVE_MAPPER.to_domain(row).tags is None


def test_status_comes_off_the_column_and_not_off_the_asset() -> None:
    """The one name that is both a pinned field and seller state (hence an override).

    The asset asserts ``pending_review`` and the row says ``approved``; the row is the
    authority, because the stored value is the seller's decision and not the buyer's claim.
    """
    row = _written(_asset(status="pending_review"))
    assert row.status == "approved"
    assert CREATIVE_MAPPER.to_domain(row).status == "approved"


def test_the_repository_owned_columns_are_not_fields_of_the_domain_model() -> None:
    """The point of the layer: the asset declares no field that exists only to persist."""
    assert CREATIVE_MAPPER.repository_owned.isdisjoint(CreativeAssetRequest.model_fields)


# ---------------------------------------------------------------------------
# Both directions composed
# ---------------------------------------------------------------------------


def test_a_row_read_and_written_back_reproduces_every_column() -> None:
    """to_row(to_domain(row)) == row, column by column.

    The composition is the claim a repository will rely on in step 3: a read followed by a
    write must not move a column. The seller's own keys are handed back in, because the
    domain object does not carry them -- that is the division of labour, not a gap.
    """
    original = _written(_asset())
    seller_half = {key: value for key, value in original.data.items() if key not in CREATIVE_MAPPER.blob_fields}
    rewritten = _written(CREATIVE_MAPPER.to_domain(original), seller_document=seller_half)
    for column in CreativeRow.__table__.columns:
        assert getattr(rewritten, column.name) == getattr(original, column.name), column.name


def test_a_row_whose_blob_has_no_assets_is_refused_at_the_read() -> None:
    """``assets`` is pin-REQUIRED, so such a row cannot produce a valid domain model.

    Refusing at the read is the designed behaviour -- a malformed row fails where it is read
    rather than three layers up -- and it is a real difference from the current listing, which
    substitutes ``{}``. Rows in this state exist: ``get_by_principal`` carries a
    ``data['assets'] IS NOT NULL`` predicate precisely to hide them.
    """
    row = _written(_asset())
    row.data = {key: value for key, value in row.data.items() if key != "assets"}
    with pytest.raises(ValueError, match="assets"):
        CREATIVE_MAPPER.to_domain(row)
