"""Deprecation signals + provenance round-trip for the creative wire shapes.

Issue #289: emit ``DeprecationWarning`` for the two buyer-facing legacy shapes
on the sync/listing wire so callers see a migration signal in their own logs
and test runs, not just our server-side log stream.
Library-typed ``FormatReferenceStructuredObject`` conversion is intentionally
not warned — that path is internal plumbing, not buyer-facing.

Issue #290: lock down ``provenance`` round-trip from listing ``Creative`` →
sync ``CreativeAsset``. EU AI Act Article 50 (enforcement Aug 2026) means a
schema change that silently drops provenance between wire shapes would surface
as a buyer compliance audit failure, not a test failure — so we pin the
behavior explicitly.
"""

import warnings
from datetime import UTC, datetime

import pytest
from adcp.types import AiTool

from src.core._deprecations import LEGACY_FORMAT_ID_SUNSET
from src.core.schemas import Creative
from src.core.schemas.creative import CreativeAsset, DigitalSourceType, Provenance


def _base_creative_kwargs(format_value, *, key: str = "format_id") -> dict:
    return {
        "creative_id": "c1",
        "variants": [],
        "name": "Test Creative",
        key: format_value,
        "assets": {
            "banner_image": {
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        "principal_id": "p1",
        "created_date": datetime.now(),
        "updated_date": datetime.now(),
    }


class TestLegacyFormatKey:
    """The legacy ``format`` key (instead of ``format_id``) is silently renamed."""

    def test_creative_legacy_format_key_emits_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match=r"'format'.*deprecated"):
            Creative(**_base_creative_kwargs("display_300x250", key="format"))

    def test_creative_asset_legacy_format_key_emits_deprecation_warning(self):
        # CreativeAsset is the sync-wire shape — same upgrade helper, same warning
        with pytest.warns(DeprecationWarning, match=r"'format'.*deprecated"):
            CreativeAsset.model_validate(
                {
                    "creative_id": "c1",
                    "name": "Test",
                    "format": "display_300x250",  # legacy key
                    "assets": {
                        "banner_image": {
                            "asset_type": "image",
                            "url": "https://example.com/b.png",
                            "width": 300,
                            "height": 250,
                        }
                    },
                }
            )

    def test_format_id_key_with_structured_value_does_not_warn(self):
        """Spec-shaped key + structured value path is silent."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            Creative(
                **_base_creative_kwargs(
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}
                )
            )

    def test_warning_attributes_to_caller_not_to_validator_or_pydantic(self):
        """``DeprecationWarning`` must blame the buyer's call site, not our validator.

        The whole point of warning is to tell buyers *where in their code* to
        fix the legacy shape. Without ``skip_file_prefixes``, warnings emitted
        from inside a Pydantic ``model_validator`` blame the validator function
        (in our package) or Pydantic internals — neither of which a buyer can
        act on. Lock down the attribution so a future regression on the
        ``skip_file_prefixes`` plumbing fails this test.
        """
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", DeprecationWarning)
            Creative(**_base_creative_kwargs("display_300x250", key="format"))

        deprecation_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]
        assert deprecation_warnings, "expected a DeprecationWarning to be emitted"
        for w in deprecation_warnings:
            assert w.filename.endswith(__file__.split("/")[-1]), (
                f"warning attributed to {w.filename}:{w.lineno} (should be this test file). "
                f"skip_file_prefixes plumbing in src/core/_deprecations.py is not skipping "
                f"the validator/pydantic frames."
            )

    def test_sunset_version_appears_in_warning_message(self):
        """The warning message names the sunset version so buyers know the deadline."""
        with pytest.warns(DeprecationWarning) as captured:
            Creative(**_base_creative_kwargs("display_300x250", key="format"))

        messages = [str(w.message) for w in captured]
        assert any(LEGACY_FORMAT_ID_SUNSET in m for m in messages), (
            f"sunset version {LEGACY_FORMAT_ID_SUNSET} should appear in at least one warning. Got: {messages}"
        )


def _build_listing_creative(provenance: Provenance) -> Creative:
    return Creative(
        creative_id="c1",
        variants=[],
        name="Test",
        format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        assets={
            "banner_image": {
                "url": "https://example.com/x.png",
                "width": 300,
                "height": 250,
                "asset_type": "image",
            }
        },
        principal_id="p1",
        created_date=datetime.now(tz=UTC),
        updated_date=datetime.now(tz=UTC),
        provenance=provenance,
    )


def _convert_listing_to_sync(listing: Creative) -> CreativeAsset:
    """Wire-shape conversion: dump listing, reconstruct sync (matches HTTP flow)."""
    dumped = listing.model_dump()
    return CreativeAsset.model_validate(
        {
            "creative_id": dumped["creative_id"],
            "name": dumped["name"],
            "format_id": dumped["format_id"],
            "assets": dumped["assets"],
            "provenance": dumped["provenance"],
        }
    )


class TestProvenanceRoundTrip:
    """Provenance must survive listing Creative → sync CreativeAsset conversion.

    Buyer flow: read via ``list_creatives`` (listing ``Creative`` with the
    salesagent provenance extension), edit a field, post back via
    ``sync_creatives`` (library-native ``CreativeAsset.provenance``). If the
    conversion drops fields, EU AI Act Article 50 audits fail — so pin the
    behavior explicitly.

    Issue #290. Schema-drift between local ``Provenance`` and library
    ``Provenance`` (declared_by/c2pa/disclosure type mismatch, human_oversight
    bool-vs-enum, divergent ``DigitalSourceType`` vocab) is tracked in #291.
    """

    def test_minimal_provenance_round_trips(self):
        """Required-only provenance round-trips and surfaces on the sync wire."""
        listing = _build_listing_creative(Provenance(digital_source_type=DigitalSourceType.digital_capture))

        sync = _convert_listing_to_sync(listing)

        assert sync.provenance is not None, "provenance dropped silently in conversion"
        assert sync.provenance.digital_source_type.value == "digital_capture"

    def test_listing_dump_retains_every_provenance_sub_field(self):
        """Listing wire surfaces every provenance sub-field for seller review.

        Sellers read provenance via ``list_creatives`` for compliance review;
        a silent ``exclude`` on any sub-field would hide disclosure metadata.
        """
        prov = Provenance(
            digital_source_type=DigitalSourceType.composite_with_trained_model,
            ai_tool=AiTool(name="gpt-image-1", version="2026-04"),
            human_oversight=True,
            declared_by="agency-acme",
            created_time=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
            c2pa="https://c2pa.example.com/manifest/abc123",
            disclosure="Composite produced with a trained generative model.",
            verification={"signature_valid": True, "trust_chain": "c2pa-v1"},
        )
        listing = _build_listing_creative(prov)

        dumped = listing.model_dump()

        assert "provenance" in dumped
        out = dumped["provenance"]
        assert out["digital_source_type"] == "composite_with_trained_model"
        assert out["ai_tool"] == {"name": "gpt-image-1", "version": "2026-04"}
        assert out["human_oversight"] is True
        assert out["declared_by"] == "agency-acme"
        assert out["c2pa"] == "https://c2pa.example.com/manifest/abc123"
        assert "Composite produced" in out["disclosure"]
        assert out["verification"] == {"signature_valid": True, "trust_chain": "c2pa-v1"}

    def test_provenance_round_trips_via_from_attributes_path(self):
        """In-process conversion (``model_validate(creative, from_attributes=True)``) round-trips.

        This exercises the ``BaseModel`` early-return branch in
        ``_upgrade_format_id_in_values`` — distinct from the HTTP/dump path
        covered by the other round-trip tests. If a future Pydantic upgrade
        changes how ``model_validate`` dispatches ``BaseModel`` inputs through
        the ``mode='before'`` validator, the dump path stays green while this
        path silently drops provenance.
        """
        prov = Provenance(
            digital_source_type=DigitalSourceType.digital_capture,
            ai_tool=AiTool(name="gpt-image-1"),
            created_time=datetime(2026, 4, 1, tzinfo=UTC),
        )
        listing = _build_listing_creative(prov)

        sync = CreativeAsset.model_validate(listing, from_attributes=True)

        assert sync.provenance is not None, "provenance dropped on from_attributes path"
        assert sync.provenance.digital_source_type.value == "digital_capture"
        assert sync.provenance.ai_tool is not None
        assert sync.provenance.ai_tool.name == "gpt-image-1"
        assert sync.provenance.created_time == datetime(2026, 4, 1, tzinfo=UTC)

    def test_shared_provenance_subset_round_trips_to_sync_wire(self):
        """Subset of provenance fields with matching shapes round-trips end-to-end.

        Constrained to the shared surface that does round-trip today:
        - ``digital_source_type`` value present in both vocabularies
        - ``ai_tool`` (library-typed, identical on both sides)
        - ``created_time`` (datetime on both sides)

        The fields with diverged shapes (``human_oversight``, ``declared_by``,
        ``c2pa``, ``disclosure``, ``verification``, divergent enum values) are
        tracked in the schema-alignment follow-up. Locking down the working
        subset means a future schema regression on these fields fails this test
        instead of a buyer's compliance audit.
        """
        prov = Provenance(
            digital_source_type=DigitalSourceType.digital_capture,  # shared enum value
            ai_tool=AiTool(name="gpt-image-1", version="2026-04"),
            created_time=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        listing = _build_listing_creative(prov)

        sync = _convert_listing_to_sync(listing)

        assert sync.provenance is not None
        assert sync.provenance.digital_source_type.value == "digital_capture"
        assert sync.provenance.ai_tool is not None
        assert sync.provenance.ai_tool.name == "gpt-image-1"
        assert sync.provenance.ai_tool.version == "2026-04"
        assert sync.provenance.created_time == datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
