"""
Test fixtures for AdCP Sales Agent.

This module provides reusable test data and mock objects for testing.
"""

from .builders import (
    RequestBuilder,
    ResponseBuilder,
    TargetingBuilder,
    TestDataBuilder,
)
from .builders import (
    create_test_tenant_with_principal as create_test_tenant_with_principal,
)
from .factories import CreativeFactory, MediaBuyFactory, PrincipalFactory, ProductFactory, TenantFactory
from .mocks import (
    MockAdapter,
    MockCursor,
    MockDatabase,
    MockGeminiResponse,
    MockGeminiService,
    MockHTTPRequest,
    MockHTTPResponse,
    MockOAuthProvider,
)

__all__ = [
    # Factories
    "TenantFactory",
    "PrincipalFactory",
    "ProductFactory",
    "MediaBuyFactory",
    "CreativeFactory",
    # Mocks
    "MockDatabase",
    "MockCursor",
    "MockAdapter",
    "MockGeminiService",
    "MockGeminiResponse",
    "MockOAuthProvider",
    "MockHTTPRequest",
    "MockHTTPResponse",
    # Builders
    "RequestBuilder",
    "ResponseBuilder",
    "TargetingBuilder",
    "TestDataBuilder",
    # Data loaders
    "load_fixture_data",
    "get_sample_creative",
    "get_sample_product",
]

import json
from pathlib import Path


def load_fixture_data(filename: str) -> dict:
    """Load JSON fixture data from the data directory."""
    fixture_path = Path(__file__).parent / "data" / filename
    with open(fixture_path) as f:
        return json.load(f)


def get_sample_creative(format_type: str = "display_300x250"):
    """Get a sample creative for testing."""
    return load_fixture_data("sample_creatives.json").get(format_type)


def get_sample_product(product_type: str = "run_of_site"):
    """Get a sample product for testing."""
    return load_fixture_data("sample_products.json").get(product_type)
