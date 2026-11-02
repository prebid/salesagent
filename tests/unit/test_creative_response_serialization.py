"""Test that all Creative-related response models properly exclude internal fields.

adcp 3.6.0: Many Creative fields moved to internal (exclude=True):
- name, assets, tags, status, created_date, updated_date are now INTERNAL
- model_dump() only returns: creative_id, format_id, variants

This test suite covers:
- CreateCreativeResponse
- GetCreativesResponse
- SyncCreativeResult
- SyncCreativesResponse
- ListCreativeFormatsResponse
- ListCreativesResponse
"""

import pytest
from adcp.types import CreativeAction

from src.core.schemas import (
    CreateCreativeResponse,
    CreativeApprovalStatus,
    GetCreativesResponse,
    SyncCreativeResult,
    SyncCreativesResponse,
)
from tests.factories.creative_asset import build_assets, image_spec
from tests.harness.assertions import assert_omits_paths
from tests.helpers.creative_test_helpers import (
    assert_listing_creative_fields,
    make_test_creative,
    make_test_creative_list,
)


def test_create_creative_response_excludes_internal_fields():
    """Test that CreateCreativeResponse excludes Creative internal fields."""
    creative = make_test_creative()

    response = CreateCreativeResponse(
        creative=creative,
        status=CreativeApprovalStatus(creative_id="test_123", status="pending_review", detail="Under review"),
        suggested_adaptations=[],
    )

    result = response.model_dump()
    creative_data = result["creative"]
    assert_listing_creative_fields(creative_data, "test_123")

    # Delivery-only fields should NOT be present
    assert "variants" not in creative_data, "Delivery field 'variants' should not be in listing response"


def test_get_creatives_response_excludes_internal_fields():
    """Test that GetCreativesResponse excludes Creative internal fields from all creatives."""
    creatives = make_test_creative_list(3)

    response = GetCreativesResponse(creatives=creatives, assignments=None)
    result = response.model_dump()

    for i, creative_data in enumerate(result["creatives"]):
        assert_listing_creative_fields(creative_data, f"creative_{i}", prefix=f"Creative {i}")


def test_creative_optional_fields_still_included():
    """A public optional field is on the wire; an internal field is on the model only."""
    creative = make_test_creative(
        creative_id="test_with_optional",
        name="Test Creative",
        principal_id="principal_123",
        tags=["sports", "premium"],
    )

    response = GetCreativesResponse(creatives=[creative])
    result = response.model_dump()
    creative_data = result["creatives"][0]

    # Listing Creative: tags is a public optional field; present when set
    assert "tags" in creative_data, "Listing Creative: tags is a public field"

    # Internal fields still excluded
    assert "principal_id" not in creative_data, "Internal field principal_id should be excluded"

    # A Field(exclude=True) field EXISTS on the model — the attribute is what existing
    # means, and there is no second dump shape to read it out of (CLAUDE.md pattern 4).
    assert creative.principal_id == "principal_123"


@pytest.mark.parametrize("null_field", ["alt_text", "provenance"])
def test_creative_model_dump_omits_null_fields_inside_assets(null_field):
    """An unset optional field on a stored asset is OMITTED, never dumped as null.

    AdCP 3.1 types these asset fields without accepting ``null``, so a literal wire
    ``null`` fails schema validation. What prevents it is that ``Creative.assets`` is
    INHERITED as the library's typed asset map — it used to be redeclared
    ``dict[str, Any]``, which Pydantic's ``exclude_none=True`` default cannot see inside,
    and a ``strip_none_deep`` pass over the dict patched the output back. The redeclaration
    and the strip are both gone (``src/core/schemas/creative.py``): the row-to-model read
    validates the stored document into the typed map, so ``exclude_none`` reaches the
    asset's own fields.

    Mutation check: redeclare ``assets: dict[str, Any]`` on ``Creative`` -> this goes red
    with the key present and valued ``None``. The wire-level oracle is
    ``tests/integration/test_list_creatives_a2a_wire_shape.py::test_a2a_wire_omits_null_asset_fields``.
    """
    creative = make_test_creative(
        creative_id="c_null_asset_field",
        assets=build_assets(image_spec("banner").with_fields(**{null_field: None})),
    )

    # ``mode="json"`` because the claim is about the SERIALIZED shape: the typed asset
    # holds ``url`` as a pydantic ``AnyUrl``, and only the JSON dump is the wire form the
    # pinned asset schema grades.
    banner = creative.model_dump(mode="json")["assets"]["banner"]

    assert_omits_paths(
        banner,
        [null_field],
        context=f"model_dump() assets.banner (a null here is invalid against the pinned AdCP asset schema; {banner!r})",
    )
    # Negative control: the omission must not eat the asset's real fields.
    assert banner["asset_type"] == "image"
    assert banner["url"] == "https://example.com/banner.png"


# ── SyncCreativeResult serialization ─────────────────────────────────────


def test_sync_creative_result_excludes_internal_fields():
    """model_dump() excludes internal_status and review_feedback; the spec status is derived."""
    result = SyncCreativeResult(
        creative_id="c_1",
        action=CreativeAction.created,
        internal_status="pending_review",
        review_feedback="Looks good",
    )
    dumped = result.model_dump(mode="json")
    assert dumped["creative_id"] == "c_1"
    # The pinned per-creative ``status`` is the row's review state (creative-status enum).
    assert dumped["status"] == "pending_review"
    assert "internal_status" not in dumped
    assert "review_feedback" not in dumped
    # The other half of the split: both fields EXIST on the model. The attribute is what
    # existing means — there is no second dump shape (CLAUDE.md pattern 4).
    assert result.internal_status == "pending_review"
    assert result.review_feedback == "Looks good"


def test_sync_creative_result_omits_unset_optional_arrays():
    """model_dump() omits changes, errors and warnings when the tool populated none of them.

    Pinned ``creative/sync-creatives-response.json``, the per-creative item: ``required``
    is ``["creative_id", "action"]``, so all three arrays are OPTIONAL and their absence is
    spec-valid. The item's one conditional obligation is on a different field — ``status``
    "MUST be omitted when action is failed or deleted" — and the schema carries that as an
    ``if/then``; ``changes``/``errors``/``warnings`` carry no such clause. This seller
    spells the absence as ``None`` (the parent's default, see ``SyncCreativeResult``), and
    ``exclude_none`` omits it on model_dump, model_dump_json and structured_content alike.

    This case used to pass ``changes=[]``/``errors=[]``/``warnings=[]`` and demand the
    empty lists be stripped. Nothing in the pin asks for that, and stripping a field by its
    VALUE would need the per-class "last word" wire hook CLAUDE.md pattern #4 deleted — a
    field that must not reach the wire is ``Field(exclude=True)`` at its declaration, which
    is unconditional.
    """
    result = SyncCreativeResult(creative_id="c_2", action=CreativeAction.updated)
    dumped = result.model_dump()
    assert dumped["creative_id"] == "c_2"
    assert "changes" not in dumped
    assert "errors" not in dumped
    assert "warnings" not in dumped


def test_sync_creative_result_keeps_populated_lists():
    """model_dump() keeps changes/errors/warnings when non-empty."""
    result = SyncCreativeResult(
        creative_id="c_3",
        action=CreativeAction.updated,
        changes=["name"],
        warnings=["provenance missing"],
    )
    dumped = result.model_dump()
    assert dumped["changes"] == ["name"]
    assert dumped["warnings"] == ["provenance missing"]
    assert "errors" not in dumped  # unset → None → omitted by exclude_none


# REMOVED: test_sync_creative_result_model_dump_internal. Its whole subject was the
# ``model_dump_internal`` seat, which is gone (CLAUDE.md pattern 4 — a per-class second dump
# path is a shape that exists on one path and not the others). Everything it asserted is
# graded above: creative_id and changes on the wire by
# test_sync_creative_result_excludes_internal_fields and
# test_sync_creative_result_keeps_populated_lists, and the internal half of the split by the
# two attribute assertions added to the former.


# ── SyncCreativesResponse __str__ ────────────────────────────────────────


def test_sync_creatives_response_properties_success():
    """Success variant: creatives, dry_run accessible; errors is None."""
    response = SyncCreativesResponse(  # type: ignore[call-arg]
        creatives=[
            SyncCreativeResult(creative_id="c_1", action=CreativeAction.created),
        ],
        dry_run=False,
    )
    assert len(response.creatives) == 1
    assert response.dry_run is False
    # adcp 3.9: SyncCreativesResponse subclasses success variant only;
    # error variant is a separate type, no .errors attr on success
    assert not hasattr(response, "errors")


# ── ListCreativeFormatsResponse __str__ ──────────────────────────────────


# ── ListCreativesResponse __str__ ────────────────────────────────────────


# ── CreateCreativeResponse __str__ and nested serialization ──────────────
