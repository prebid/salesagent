"""Unit tests for ``_merge_creative_enrichment`` (media_buy_create).

The helper folds an adapter's push result (platform_creative_id + seller-side
concept enrichment) into a creative's ``data`` blob. Both enrichments are
fill-only-when-absent so an existing value — notably a future buyer-supplied
concept — is never overwritten. See #1506.
"""

from src.core.schemas import AssetStatus
from src.core.tools.media_buy_create import _merge_creative_enrichment


def _gam_status(**overrides) -> AssetStatus:
    base = {
        "creative_id": "gam_123",
        "status": "approved",
        "concept_id": "gam-order-789",
        "concept_name": "GAM Order 789",
        "concept_source": "gam_order",
    }
    base.update(overrides)
    return AssetStatus(**base)


def test_fills_concept_and_platform_id_when_absent():
    result = _merge_creative_enrichment({"assets": {}}, _gam_status())

    assert result["platform_creative_id"] == "gam_123"
    assert result["concept_id"] == "gam-order-789"
    assert result["concept_name"] == "GAM Order 789"
    assert result["concept_source"] == "gam_order"
    # Existing keys are preserved.
    assert result["assets"] == {}


def test_never_overwrites_existing_concept():
    """A buyer-supplied concept (future spec field) must take precedence over the fallback."""
    existing = {"concept_id": "buyer-summer-2026", "concept_name": "Summer 2026", "concept_source": "buyer"}

    result = _merge_creative_enrichment(existing, _gam_status())

    assert result["concept_id"] == "buyer-summer-2026"
    assert result["concept_name"] == "Summer 2026"
    assert result["concept_source"] == "buyer"
    # platform_creative_id is still filled (independent of concept precedence).
    assert result["platform_creative_id"] == "gam_123"


def test_never_overwrites_existing_platform_creative_id():
    existing = {"platform_creative_id": "already_synced_999"}

    result = _merge_creative_enrichment(existing, _gam_status())

    assert result["platform_creative_id"] == "already_synced_999"


def test_no_concept_written_when_status_has_none():
    """Adapters that don't derive a concept (e.g. non-GAM) leave the blob concept-free."""
    status = AssetStatus(creative_id="c1", status="approved")

    result = _merge_creative_enrichment({"assets": {}}, status)

    assert "concept_id" not in result
    assert "concept_name" not in result
    assert "concept_source" not in result
    assert result["platform_creative_id"] == "c1"


def test_concept_source_defaults_when_omitted():
    """A concept with no explicit source is still marked as adapter-derived, not authoritative."""
    status = _gam_status(concept_source=None)

    result = _merge_creative_enrichment({}, status)

    assert result["concept_id"] == "gam-order-789"
    assert result["concept_source"] == "adapter_enrichment"


def test_does_not_mutate_input():
    existing = {"assets": {}}

    _merge_creative_enrichment(existing, _gam_status())

    assert existing == {"assets": {}}


def test_handles_none_existing_data():
    result = _merge_creative_enrichment(None, _gam_status())

    assert result["concept_id"] == "gam-order-789"
    assert result["platform_creative_id"] == "gam_123"
