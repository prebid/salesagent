"""Schema tests for CollectionListReference and Targeting.collection_list / _exclude.

Mirrors AdCP 3.0.1 spec at /schemas/3.0.1/core/collection-list-ref.json and
/schemas/3.0.1/core/targeting.json:189-200. Local extension because adcp Python
library 3.12.0's TargetingOverlay codegen lags the spec.

Round-trip tests prove data survives the persistence path:
    Targeting -> model_dump -> JSON -> dict -> Targeting

This is the path package_config takes through MediaPackage.JSONType().
"""

import json

import pytest
from pydantic import ValidationError

from src.core.schemas import CollectionListReference, Targeting


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
