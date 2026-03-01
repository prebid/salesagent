"""Base schema classes shared across all schema submodules.

These classes are also re-exported from ``src.core.schemas`` for backward
compatibility.
"""

from adcp.types.base import AdCPBaseModel as LibraryAdCPBaseModel
from pydantic import BaseModel, ConfigDict, model_serializer

from src.core.config import get_pydantic_extra_mode


class NestedModelSerializerMixin:
    """Mixin that ensures nested Pydantic models use their custom model_dump().

    Pydantic's default serialization doesn't automatically call custom model_dump() methods
    on nested models. This mixin introspects all fields and explicitly calls model_dump()
    on any nested BaseModel instances, ensuring internal fields are properly excluded.

    This approach is resilient to schema changes - no hardcoded field names.

    Usage:
        class MyResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
            nested_field: NestedModel
            # Automatically serializes nested_field correctly
    """

    @model_serializer(mode="wrap")
    def _serialize_nested_models(self, serializer, info):
        """Automatically serialize nested Pydantic models using their custom model_dump()."""
        # Get default serialization
        data = serializer(self)

        # Introspect all fields and re-serialize nested Pydantic models
        for field_name, _ in self.__class__.model_fields.items():
            if field_name not in data:
                continue

            field_value = getattr(self, field_name, None)
            if field_value is None:
                continue

            # Handle list of Pydantic models
            if isinstance(field_value, list) and field_value:
                if isinstance(field_value[0], BaseModel):
                    data[field_name] = [item.model_dump(mode=info.mode) for item in field_value]
            # Handle single Pydantic model
            elif isinstance(field_value, BaseModel):
                data[field_name] = field_value.model_dump(mode=info.mode)

        return data


class SalesAgentBaseModel(LibraryAdCPBaseModel):
    """Base model for all internal salesagent schemas.

    Extends the adcp library's AdCPBaseModel to add environment-aware validation:
    - Production: extra="ignore" (forward compatible, accepts future schema fields)
    - Non-production: extra="forbid" (strict, catches bugs early)

    Inherits from library base:
    - model_dump(exclude_none=True) -- AdCP spec compliance
    - model_dump_json(exclude_none=True) -- AdCP spec compliance
    - model_summary() -- human-readable protocol responses

    The validation mode is set at class definition time based on the ENVIRONMENT variable.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())
