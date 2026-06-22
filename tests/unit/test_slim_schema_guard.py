"""Guard: slim schema covers all required fields of CreateMediaBuyRequest.

Fails immediately when the adcp library is upgraded and adds a new required
field that our slim schema doesn't advertise.  Catching this at make quality
prevents silent runtime failures where an agent omits a required field because
it was never told about it.

See src/core/slim_schemas.py for the slim schema definition and rationale.
"""

from adcp.types import CreateMediaBuyRequest

from src.core.slim_schemas import CREATE_MEDIA_BUY_SLIM_SCHEMA


def test_slim_schema_covers_all_required_fields() -> None:
    """All required top-level fields of CreateMediaBuyRequest must appear
    in the slim schema's properties.

    If this test fails after an adcp upgrade, add the missing field(s) to
    CREATE_MEDIA_BUY_SLIM_SCHEMA in src/core/slim_schemas.py.
    """
    adcp_required = {name for name, field in CreateMediaBuyRequest.model_fields.items() if field.is_required()}

    slim_properties = set(CREATE_MEDIA_BUY_SLIM_SCHEMA.get("properties", {}).keys())

    missing = adcp_required - slim_properties
    assert not missing, (
        f"adcp library has required fields missing from the slim schema: {missing}.\n"
        f"Update CREATE_MEDIA_BUY_SLIM_SCHEMA in src/core/slim_schemas.py to include them."
    )


def test_slim_schema_required_list_matches_adcp() -> None:
    """The slim schema's own 'required' list must match adcp's required fields.

    Ensures the slim schema is self-consistent — it won't accept a call that
    omits a field the adcp model needs.
    """
    adcp_required = {name for name, field in CreateMediaBuyRequest.model_fields.items() if field.is_required()}

    slim_required = set(CREATE_MEDIA_BUY_SLIM_SCHEMA.get("required", []))

    missing_from_slim = adcp_required - slim_required
    assert not missing_from_slim, (
        f"adcp required fields not listed in slim schema's 'required': {missing_from_slim}.\n"
        f"Add them to the 'required' list in CREATE_MEDIA_BUY_SLIM_SCHEMA."
    )


def test_slim_schema_is_valid_json_schema_object() -> None:
    """Basic structural sanity — slim schema must be a valid JSON Schema object."""
    assert CREATE_MEDIA_BUY_SLIM_SCHEMA.get("type") == "object"
    assert "properties" in CREATE_MEDIA_BUY_SLIM_SCHEMA
    assert "required" in CREATE_MEDIA_BUY_SLIM_SCHEMA
    assert isinstance(CREATE_MEDIA_BUY_SLIM_SCHEMA["properties"], dict)
    assert isinstance(CREATE_MEDIA_BUY_SLIM_SCHEMA["required"], list)
    assert len(CREATE_MEDIA_BUY_SLIM_SCHEMA["required"]) > 0
