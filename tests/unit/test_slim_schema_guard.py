"""Guard: slim schemas cover all required fields of their request models.

Fails immediately when the adcp library is upgraded and adds a new required
field that a slim schema doesn't advertise.  Catching this at make quality
prevents silent runtime failures where an agent omits a required field because
it was never told about it.

See src/core/slim_schemas.py for the slim schema definitions and rationale.
"""

import pytest
from adcp.types import CreateMediaBuyRequest

from src.core.schemas import UpdateMediaBuyRequest
from src.core.slim_schemas import CREATE_MEDIA_BUY_SLIM_SCHEMA, UPDATE_MEDIA_BUY_SLIM_SCHEMA

# (label, request model, slim schema) — one row per slimmed tool.
SLIM_SCHEMAS = [
    ("create_media_buy", CreateMediaBuyRequest, CREATE_MEDIA_BUY_SLIM_SCHEMA),
    ("update_media_buy", UpdateMediaBuyRequest, UPDATE_MEDIA_BUY_SLIM_SCHEMA),
]


def _adcp_required(model) -> set[str]:
    """Required top-level field names, excluding internal (excluded) fields.

    Fields marked ``exclude=True`` (e.g. the internal ``today`` testing field)
    are not part of the AdCP wire contract, so the slim schema need not list
    them even if they were somehow required.
    """
    return {
        name
        for name, field in model.model_fields.items()
        if field.is_required() and not getattr(field, "exclude", False)
    }


@pytest.mark.parametrize("label,model,schema", SLIM_SCHEMAS, ids=[s[0] for s in SLIM_SCHEMAS])
def test_slim_schema_covers_all_required_fields(label, model, schema) -> None:
    """All required top-level fields of the request model must appear
    in the slim schema's properties.

    If this test fails after an adcp upgrade, add the missing field(s) to
    the corresponding slim schema in src/core/slim_schemas.py.
    """
    slim_properties = set(schema.get("properties", {}).keys())

    missing = _adcp_required(model) - slim_properties
    assert not missing, (
        f"adcp library has required fields missing from the {label} slim schema: {missing}.\n"
        f"Update the slim schema in src/core/slim_schemas.py to include them."
    )


@pytest.mark.parametrize("label,model,schema", SLIM_SCHEMAS, ids=[s[0] for s in SLIM_SCHEMAS])
def test_slim_schema_required_list_matches_adcp(label, model, schema) -> None:
    """The slim schema's own 'required' list must match adcp's required fields.

    Ensures the slim schema is self-consistent — it won't accept a call that
    omits a field the adcp model needs.
    """
    slim_required = set(schema.get("required", []))

    missing_from_slim = _adcp_required(model) - slim_required
    assert not missing_from_slim, (
        f"adcp required fields not listed in {label} slim schema's 'required': {missing_from_slim}.\n"
        f"Add them to the 'required' list in the slim schema."
    )


@pytest.mark.parametrize("label,model,schema", SLIM_SCHEMAS, ids=[s[0] for s in SLIM_SCHEMAS])
def test_slim_schema_is_valid_json_schema_object(label, model, schema) -> None:
    """Basic structural sanity — slim schema must be a valid JSON Schema object."""
    assert schema.get("type") == "object"
    assert "properties" in schema
    assert "required" in schema
    assert isinstance(schema["properties"], dict)
    assert isinstance(schema["required"], list)
    assert len(schema["required"]) > 0
