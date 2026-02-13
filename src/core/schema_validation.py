"""
JSON Schema validation utilities for AdCP API responses.

This module provides utilities for generating JSON Schema from Pydantic models
and creating a schema registry for serving via API.
"""

from typing import Any

from pydantic import BaseModel


def get_model_schema(model_class: type[BaseModel]) -> dict[str, Any]:
    """Generate JSON Schema for a Pydantic model.

    Args:
        model_class: Pydantic model class to generate schema for

    Returns:
        JSON Schema dictionary
    """
    return model_class.model_json_schema()


def create_schema_registry() -> dict[str, dict[str, Any]]:
    """Create a registry of all available schemas for serving via API.

    Returns:
        Dictionary mapping schema names to JSON Schema objects
    """
    from src.core.schemas import (
        GetMediaBuyDeliveryResponse,
        GetProductsResponse,
        GetSignalsResponse,
        ListAuthorizedPropertiesResponse,
        ListCreativeFormatsResponse,
        ListCreativesResponse,
        SyncCreativesResponse,
        UpdatePerformanceIndexResponse,
    )

    # Core response models to include in schema registry
    # Note: Union types (CreateMediaBuyResponse, UpdateMediaBuyResponse) are excluded
    # because they are type aliases, not concrete classes
    response_models: list[type[BaseModel]] = [
        GetProductsResponse,
        ListCreativeFormatsResponse,
        ListAuthorizedPropertiesResponse,
        GetSignalsResponse,
        SyncCreativesResponse,
        ListCreativesResponse,
        GetMediaBuyDeliveryResponse,
        UpdatePerformanceIndexResponse,
    ]

    schema_registry: dict[str, dict[str, Any]] = {}
    for model_class in response_models:
        schema_name = model_class.__name__.lower().replace("response", "")
        schema_registry[schema_name] = get_model_schema(model_class)

    return schema_registry
