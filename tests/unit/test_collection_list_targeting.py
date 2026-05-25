"""Schema tests for CollectionListReference and Targeting.collection_list / _exclude.

CollectionListReference is imported from the adcp library (4.3+) at the internal
generated path because it is not yet re-exported on the public adcp.types namespace.
The spec-drift guards below ensure the imported class still matches AdCP spec.

Round-trip tests prove data survives the persistence path:
    Targeting -> model_dump -> JSON -> dict -> Targeting

This is the path package_config takes through MediaPackage.JSONType().
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.schemas import CollectionListReference, Targeting

_SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "latest" / "_schemas_latest_core_collection-list-ref_json.json"
)


# ---------------------------------------------------------------------------
# CollectionListReference shape
# ---------------------------------------------------------------------------
class TestCollectionListReferenceShape:
    def test_required_fields_only(self):
        ref = CollectionListReference(agent_url="https://gov.example", list_id="collections_v1")
        assert str(ref.agent_url).startswith("https://gov.example")
        assert ref.list_id == "collections_v1"
        assert ref.auth_token is None

    def test_with_auth_token(self):
        ref = CollectionListReference(
            agent_url="https://gov.example",
            list_id="c1",
            auth_token="bearer.token.value",
        )
        assert ref.auth_token == "bearer.token.value"

    def test_agent_url_required(self):
        with pytest.raises(ValidationError):
            CollectionListReference(list_id="c1")  # type: ignore[call-arg]

    def test_list_id_required(self):
        with pytest.raises(ValidationError):
            CollectionListReference(agent_url="https://gov.example")  # type: ignore[call-arg]

    def test_list_id_min_length_one(self):
        with pytest.raises(ValidationError):
            CollectionListReference(agent_url="https://gov.example", list_id="")

    def test_extra_forbid_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            CollectionListReference(  # type: ignore[call-arg]
                agent_url="https://gov.example",
                list_id="c1",
                bogus_field="x",
            )


# ---------------------------------------------------------------------------
# Targeting accepts both list types
# ---------------------------------------------------------------------------
class TestTargetingListReferences:
    def test_collection_list_field_present(self):
        t = Targeting(
            collection_list=CollectionListReference(agent_url="https://gov.example", list_id="collections_v1")
        )
        assert t.collection_list is not None
        assert t.collection_list.list_id == "collections_v1"

    def test_collection_list_exclude_field_present(self):
        t = Targeting(
            collection_list_exclude=CollectionListReference(agent_url="https://gov.example", list_id="dnf_v1")
        )
        assert t.collection_list_exclude is not None
        assert t.collection_list_exclude.list_id == "dnf_v1"

    def test_property_list_and_collection_list_coexist(self):
        from adcp.types import PropertyListReference

        t = Targeting(
            property_list=PropertyListReference(agent_url="https://gov.example", list_id="props_v1"),
            collection_list=CollectionListReference(agent_url="https://gov.example", list_id="collections_v1"),
        )
        assert t.property_list.list_id == "props_v1"
        assert t.collection_list.list_id == "collections_v1"

    def test_collection_list_dict_form_coerces(self):
        """Pydantic must coerce nested dicts to CollectionListReference (the JSON path)."""
        t = Targeting(
            collection_list={"agent_url": "https://gov.example", "list_id": "collections_v1"},
        )
        assert isinstance(t.collection_list, CollectionListReference)
        assert t.collection_list.list_id == "collections_v1"


# ---------------------------------------------------------------------------
# Round-trip through DB JSON path
# ---------------------------------------------------------------------------
class TestRoundTripThroughJson:
    def test_collection_list_survives_json_roundtrip(self):
        """Simulates MediaPackage.package_config storage: Targeting -> dump -> JSON -> dict -> Targeting."""
        original = Targeting(
            collection_list=CollectionListReference(agent_url="https://gov.example", list_id="collections_v1"),
            collection_list_exclude=CollectionListReference(agent_url="https://gov.example", list_id="dnf_v1"),
        )
        dumped = original.model_dump(exclude_none=True, mode="json")
        raw = json.loads(json.dumps(dumped))
        rehydrated = Targeting(**raw)

        assert rehydrated.collection_list.list_id == "collections_v1"
        assert rehydrated.collection_list_exclude.list_id == "dnf_v1"

    def test_both_list_types_survive_roundtrip(self):
        """Storyboard scenario: create with both lists, swap on update — round-trip preserves both."""
        from adcp.types import PropertyListReference

        original = Targeting(
            property_list=PropertyListReference(agent_url="https://gov.example", list_id="acme_outdoor_allowlist_v1"),
            collection_list=CollectionListReference(
                agent_url="https://gov.example", list_id="acme_outdoor_collections_v1"
            ),
        )
        dumped = original.model_dump(exclude_none=True, mode="json")
        raw = json.loads(json.dumps(dumped))
        rehydrated = Targeting(**raw)

        assert rehydrated.property_list.list_id == "acme_outdoor_allowlist_v1"
        assert rehydrated.collection_list.list_id == "acme_outdoor_collections_v1"

    def test_serialized_keys_present_at_top_level(self):
        """Storyboard validates field paths like .targeting_overlay.collection_list.list_id —
        the dict must surface collection_list at the top level, not nested under some wrapper."""
        t = Targeting(collection_list=CollectionListReference(agent_url="https://gov.example", list_id="c1"))
        dumped = t.model_dump(exclude_none=True, mode="json")
        assert "collection_list" in dumped
        assert dumped["collection_list"]["list_id"] == "c1"


# ---------------------------------------------------------------------------
# Spec-drift guards: local model must match AdCP collection-list-ref.json
# ---------------------------------------------------------------------------
class TestCollectionListReferenceSpecDrift:
    """Guard against drift between the imported CollectionListReference and AdCP spec.

    The class is sourced from the adcp library's internal generated path; these
    guards pin the contract so any upstream regen drift surfaces here instead of
    silently breaking the storyboard's literal field assertions.
    """

    @pytest.fixture(scope="class")
    def spec(self) -> dict:
        if not _SPEC_PATH.exists():
            pytest.skip(f"AdCP spec cache not present at {_SPEC_PATH}")
        return json.loads(_SPEC_PATH.read_text())

    def test_field_set_matches_spec(self, spec: dict):
        spec_fields = set(spec["properties"].keys())
        local_fields = set(CollectionListReference.model_fields.keys())
        assert spec_fields == local_fields, (
            f"CollectionListReference drift vs spec.\n"
            f"  spec only:  {spec_fields - local_fields}\n"
            f"  local only: {local_fields - spec_fields}"
        )

    def test_required_fields_match_spec(self, spec: dict):
        spec_required = set(spec["required"])
        local_required = {name for name, info in CollectionListReference.model_fields.items() if info.is_required()}
        assert spec_required == local_required, (
            f"CollectionListReference required-field drift vs spec.\n"
            f"  spec required:  {spec_required}\n"
            f"  local required: {local_required}"
        )

    def test_additional_properties_forbid_matches_spec(self, spec: dict):
        # Spec sets additionalProperties: false; local must enforce extra="forbid".
        assert spec.get("additionalProperties") is False
        assert CollectionListReference.model_config.get("extra") == "forbid"

    def test_targeting_carries_both_collection_fields(self):
        """Targeting must surface both collection_list (inclusion) and
        collection_list_exclude (exclusion) per AdCP core/targeting.json:193-200."""
        targeting_fields = set(Targeting.model_fields.keys())
        assert "collection_list" in targeting_fields
        assert "collection_list_exclude" in targeting_fields
