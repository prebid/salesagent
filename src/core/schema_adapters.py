"""Schema adapters: Simple API on top of auto-generated schemas.

This module provides thin wrappers around auto-generated schemas that:
1. Import from schemas_generated/ (always in sync with AdCP spec)
2. Provide simple, ergonomic API (like manual schemas)
3. Add custom validators/methods where needed
4. Serve as single import point for all code

Pattern:
- Generated schemas: RootModel[Union[...]] (complex but spec-perfect)
- Adapter schemas: BaseModel (simple API, delegates to generated)

Benefits:
- Always in sync with AdCP spec (auto-regenerate)
- Simple API for application code
- Custom validators/logic added here
- No schema drift bugs
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.core.schemas import AdCPBaseModel
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest as _GeneratedGetProductsRequest,
)

# Import generated schemas
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest1 as _GeneratedGetProductsRequest1,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest2 as _GeneratedGetProductsRequest2,
)


class GetProductsRequest(BaseModel):
    """Adapter for GetProductsRequest - simple API on top of generated schema.

    This provides a simple, flat API while using the generated schemas underneath.
    The generated schema uses RootModel[Union[...]] for oneOf, which is spec-compliant
    but complex to use. This adapter hides that complexity.

    Usage:
        # Simple construction (just like manual schemas)
        req = GetProductsRequest(promoted_offering="https://example.com", brief="Video ads")

        # With brand_manifest
        req = GetProductsRequest(
            brand_manifest={"name": "Acme", "url": "https://acme.com"},
            brief="Display ads"
        )

    Under the hood:
        - Converts to correct generated schema variant (GetProductsRequest1 or GetProductsRequest2)
        - Validates against AdCP JSON Schema
        - Provides simple field access (no .root needed)
    """

    # Fields match both generated variants (union of all fields)
    brief: str = Field("", description="Natural language description of campaign requirements")
    promoted_offering: str | None = Field(
        None, description="DEPRECATED: Use brand_manifest instead. What is being promoted."
    )
    brand_manifest: dict[str, Any] | str | None = Field(
        None, description="Brand information manifest (inline object or URL string)"
    )
    filters: dict[str, Any] | None = Field(None, description="Structured filters for product discovery")
    min_exposures: int | None = Field(None, description="Minimum exposures needed for measurement validity")
    strategy_id: str | None = Field(None, description="Optional strategy ID for linking operations")
    webhook_url: str | None = Field(None, description="URL for async task completion notifications")

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_promoted_offering(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Convert promoted_offering to brand_manifest for backward compatibility."""
        if not isinstance(values, dict):
            return values

        # If only promoted_offering provided, convert to brand_manifest
        if values.get("promoted_offering") and not values.get("brand_manifest"):
            # Create minimal brand manifest from promoted_offering
            offering = values["promoted_offering"]
            if isinstance(offering, str) and offering.startswith("http"):
                values["brand_manifest"] = {"url": offering}
            else:
                values["brand_manifest"] = {"name": offering}

        return values

    def to_generated(self) -> _GeneratedGetProductsRequest:
        """Convert to the generated schema for protocol validation.

        This creates the appropriate generated schema variant (GetProductsRequest1 or
        GetProductsRequest2) based on which fields are present.

        Returns:
            Generated schema instance that can be validated against AdCP JSON Schema
        """
        # Convert filters to match generated schema format
        # Generated schema expects format_ids as list[FormatId] objects, not strings
        filters_dict = None
        if self.filters:
            # Handle both dict and Pydantic model cases
            if isinstance(self.filters, dict):
                filters_dict = self.filters.copy()
            else:
                filters_dict = self.filters.model_dump(exclude_none=True)

            # Convert format_ids from strings to FormatId objects if present
            if "format_ids" in filters_dict and filters_dict["format_ids"]:
                filters_dict["format_ids"] = [
                    {"agent_url": "https://creatives.adcontextprotocol.org", "id": fmt_id}
                    for fmt_id in filters_dict["format_ids"]
                ]

        # Determine which variant to use
        variant: _GeneratedGetProductsRequest1 | _GeneratedGetProductsRequest2
        if self.promoted_offering and not self.brand_manifest:
            # Use variant 1 (requires promoted_offering)
            variant = _GeneratedGetProductsRequest1(
                promoted_offering=self.promoted_offering,
                brief=self.brief or None,
                filters=filters_dict,  # type: ignore[arg-type]
            )
        elif self.brand_manifest:
            # Use variant 2 (requires brand_manifest)
            variant = _GeneratedGetProductsRequest2(
                promoted_offering=self.promoted_offering,
                brand_manifest=self.brand_manifest,  # type: ignore[arg-type]
                brief=self.brief or None,
                filters=filters_dict,  # type: ignore[arg-type]
            )
        else:
            # Fallback - shouldn't happen due to validator
            raise ValueError("Either promoted_offering or brand_manifest must be provided")

        # Wrap in RootModel
        return _GeneratedGetProductsRequest(root=variant)  # type: ignore[arg-type]

    @classmethod
    def from_generated(cls, generated: _GeneratedGetProductsRequest) -> "GetProductsRequest":
        """Create adapter from generated schema.

        Args:
            generated: Generated schema instance (from protocol validation)

        Returns:
            Adapter instance with simple API
        """
        # Extract data from the RootModel union
        data = generated.root.model_dump()

        return cls(**data)

    def model_dump_adcp_compliant(self, **kwargs) -> dict[str, Any]:
        """Dump as AdCP-compliant dict (validates against JSON Schema).

        This converts to generated schema first, ensuring full spec compliance.
        """
        generated = self.to_generated()
        return generated.model_dump(**kwargs)


# ============================================================================
# GetProductsResponse Adapter
# ============================================================================


class GetProductsResponse(AdCPBaseModel):
    """Adapter for GetProductsResponse - adds __str__() for protocol abstraction.

    The generated schema is spec-compliant but lacks human-readable message generation.
    This adapter wraps it and adds __str__() for MCP/A2A protocol layer use.

    Example:
        resp = GetProductsResponse(products=[...])
        # AdCP payload: spec-compliant (no message field)
        payload = resp.model_dump()
        # Protocol message: human-readable via __str__()
        message = str(resp)  # "Found 5 products that match your requirements."
    """

    model_config = {"arbitrary_types_allowed": True}

    # Fields from generated schema (flexible - accepts dicts or objects)
    products: list[Any] = Field(..., description="List of matching products")
    status: str | None = Field(None, description="Response status")
    errors: list[Any] | None = Field(None, description="Task-specific errors")

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.products)

        # Base message
        if count == 0:
            base_msg = "No products matched your requirements."
        elif count == 1:
            base_msg = "Found 1 product that matches your requirements."
        else:
            base_msg = f"Found {count} products that match your requirements."

        # Check if this looks like an anonymous response (all pricing_options have no rates)
        # Products can be dicts or objects, so we need to handle both
        if count > 0:
            all_missing_pricing = True
            for p in self.products:
                if isinstance(p, dict):
                    pricing_options = p.get("pricing_options", [])
                    if pricing_options and any(po.get("rate") is not None for po in pricing_options):
                        all_missing_pricing = False
                        break
                else:
                    if hasattr(p, "pricing_options") and p.pricing_options:
                        if any(po.rate is not None for po in p.pricing_options):
                            all_missing_pricing = False
                            break

            if all_missing_pricing:
                return f"{base_msg} Please connect through an authorized buying agent for pricing data."

        return base_msg


# Example: How to add this pattern to other schemas
class GetProductsRequestAdapter:
    """Documentation of the adapter pattern for other developers.

    To add an adapter for a new schema:

    1. Import the generated schema(s):
       from src.core.schemas_generated._schemas_v1_... import GeneratedModel

    2. Create adapter class:
       class MyModel(BaseModel):
           # Simple, flat fields
           field1: str
           field2: str | None

           def to_generated(self) -> GeneratedModel:
               # Convert to generated schema
               ...

           @classmethod
           def from_generated(cls, generated: GeneratedModel) -> "MyModel":
               # Convert from generated schema
               ...

    3. Add custom validators/methods:
       @model_validator
       def my_custom_validation(self):
           # Custom logic that can't be in JSON Schema
           ...

    4. Use in code:
       # Construction is simple
       obj = MyModel(field1="value")

       # Protocol validation uses generated
       generated = obj.to_generated()
       validated_data = generated.model_dump()

    Benefits:
    - Application code uses simple API
    - Protocol validation uses spec-compliant generated schema
    - Custom logic added only where needed
    - Automatic sync with AdCP spec (regenerate schemas)
    """

    pass


# ============================================================================
# CreateMediaBuyRequest Adapter
# ============================================================================

from datetime import datetime


class CreateMediaBuyRequest(BaseModel):
    """Adapter for CreateMediaBuyRequest with custom timezone validation.

    The generated schema validates field presence, but can't validate
    timezone-awareness of datetime fields (runtime check). This adapter
    adds that custom validation.

    Example:
        req = CreateMediaBuyRequest(
            buyer_ref="buy_123",
            packages=[{...}],
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC) + timedelta(days=30),
            budget={"total": 5000, "currency": "USD"}
        )
    """

    # Core fields (simplified - full schema has many more)
    buyer_ref: str = Field(..., description="Buyer's reference ID")
    packages: list[dict[str, Any]] = Field(..., description="Package configurations")
    start_time: datetime | str = Field(..., description="Campaign start time or 'asap'")
    end_time: datetime = Field(..., description="Campaign end time")
    budget: dict[str, Any] = Field(..., description="Budget configuration")

    # Optional fields
    promoted_offering: str | None = Field(None, description="DEPRECATED: Use brand_manifest")
    brand_manifest: dict[str, Any] | str | None = Field(None, description="Brand information")
    brief: str | None = Field(None, description="Campaign brief")

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Custom validator: Ensure datetime fields are timezone-aware.

        This validation CAN'T be in JSON Schema because it's a runtime check
        on Python datetime objects. The JSON Schema just validates ISO 8601 strings.
        """
        if isinstance(self.start_time, datetime) and self.start_time != "asap" and self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone)")
        if isinstance(self.end_time, datetime) and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self


# ============================================================================
# Product Adapter
# ============================================================================


class Product(BaseModel):
    """Adapter for Product with custom dump methods.

    The generated schema has all fields from AdCP spec, but we need
    custom dump methods to filter internal fields when sending to protocol.

    Example:
        product = Product(
            product_id="prod_123",
            name="Display 300x250",
            formats=[{"format_id": "display_300x250", "name": "Display"}],
            pricing_options=[{...}]
        )
    """

    # Core fields
    product_id: str = Field(..., description="Unique product identifier")
    name: str = Field(..., description="Product name")
    formats: list[dict[str, Any]] = Field(..., description="Supported creative formats")
    pricing_options: list[dict[str, Any]] = Field(..., description="Available pricing models")

    # Optional fields
    description: str | None = Field(None, description="Product description")
    targeting_template: dict[str, Any] | None = Field(None, description="Default targeting")
    countries: list[str] | None = Field(None, description="Available countries")

    def model_dump_adcp_compliant(self, **kwargs) -> dict[str, Any]:
        """Dump as AdCP-compliant dict (filters internal fields).

        This is the key method that filters out any internal fields that
        shouldn't be sent over the protocol.
        """
        # Filter out internal fields
        data = self.model_dump(exclude_none=True, **kwargs)

        # Remove any internal-only fields that shouldn't go to protocol
        # (Add here if needed)

        return data


# ============================================================================
# ListCreativeFormatsResponse Adapter
# ============================================================================

from src.core.schemas_generated._schemas_v1_media_buy_list_creative_formats_response_json import (
    ListCreativeFormatsResponse as _GeneratedListCreativeFormatsResponse,
)


class ListCreativeFormatsResponse(AdCPBaseModel):
    """Adapter for ListCreativeFormatsResponse - adds __str__() for protocol abstraction.

    The generated schema is spec-compliant but lacks human-readable message generation.
    This adapter wraps it and adds __str__() for MCP/A2A protocol layer use.

    Example:
        resp = ListCreativeFormatsResponse(formats=[...])
        # AdCP payload: spec-compliant (no message field)
        payload = resp.model_dump()
        # Protocol message: human-readable via __str__()
        message = str(resp)  # "Found 5 creative formats."
    """

    model_config = {"arbitrary_types_allowed": True}

    # Fields from generated schema (flexible - accepts dicts or objects)
    formats: list[Any] = Field(..., description="Full format definitions per AdCP spec")
    status: str | None = Field("completed", description="Task status")
    creative_agents: list[Any] | None = Field(None, description="Creative agents providing additional formats")
    errors: list[Any] | None = Field(None, description="Task-specific errors and warnings")

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.formats)
        if count == 0:
            return "No creative formats are currently supported."
        elif count == 1:
            return "Found 1 creative format."
        else:
            return f"Found {count} creative formats."

    def to_generated(self) -> _GeneratedListCreativeFormatsResponse:
        """Convert to generated schema for protocol validation."""
        return _GeneratedListCreativeFormatsResponse(**self.model_dump())

    @classmethod
    def from_generated(cls, generated: _GeneratedListCreativeFormatsResponse) -> "ListCreativeFormatsResponse":
        """Create adapter from generated schema."""
        return cls(**generated.model_dump())


# ============================================================================
# ListAuthorizedPropertiesResponse Adapter
# ============================================================================

from src.core.schemas_generated._schemas_v1_media_buy_list_authorized_properties_response_json import (
    ListAuthorizedPropertiesResponse as _GeneratedListAuthorizedPropertiesResponse,
)


class ListAuthorizedPropertiesResponse(AdCPBaseModel):
    """Adapter for ListAuthorizedPropertiesResponse - adds __str__() for protocol abstraction.

    The generated schema is spec-compliant but lacks human-readable message generation.
    This adapter wraps it and adds __str__() for MCP/A2A protocol layer use.

    Example:
        resp = ListAuthorizedPropertiesResponse(properties=[...])
        # AdCP payload: spec-compliant (no message field)
        payload = resp.model_dump()
        # Protocol message: human-readable via __str__()
        message = str(resp)  # "Found 3 authorized properties."
    """

    model_config = {"arbitrary_types_allowed": True}

    # Fields from generated schema (flexible - accepts dicts or objects)
    properties: list[Any] = Field(..., description="Array of authorized properties")
    tags: dict[str, Any] = Field(default_factory=dict, description="Metadata for tags")
    primary_channels: list[str] | None = Field(None, description="Primary advertising channels")
    primary_countries: list[str] | None = Field(None, description="Primary countries (ISO 3166-1 alpha-2)")
    portfolio_description: str | None = Field(None, description="Markdown portfolio description", max_length=5000)
    errors: list[Any] | None = Field(None, description="Task-specific errors and warnings")

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.properties)
        if count == 0:
            return "No authorized properties found."
        elif count == 1:
            return "Found 1 authorized property."
        else:
            return f"Found {count} authorized properties."

    def to_generated(self) -> _GeneratedListAuthorizedPropertiesResponse:
        """Convert to generated schema for protocol validation."""
        return _GeneratedListAuthorizedPropertiesResponse(**self.model_dump())

    @classmethod
    def from_generated(
        cls, generated: _GeneratedListAuthorizedPropertiesResponse
    ) -> "ListAuthorizedPropertiesResponse":
        """Create adapter from generated schema."""
        return cls(**generated.model_dump())


# ============================================================================
# Request Adapters (simple pass-through for now)
# ============================================================================

from src.core.schemas_generated._schemas_v1_media_buy_list_authorized_properties_request_json import (
    ListAuthorizedPropertiesRequest as _GeneratedListAuthorizedPropertiesRequest,
)
from src.core.schemas_generated._schemas_v1_media_buy_list_creative_formats_request_json import (
    ListCreativeFormatsRequest as _GeneratedListCreativeFormatsRequest,
)


class ListCreativeFormatsRequest(BaseModel):
    """Adapter for ListCreativeFormatsRequest - simple pass-through to generated schema."""

    type: str | None = Field(None, description="Filter by format type")
    standard_only: bool | None = Field(None, description="Only return IAB standard formats")
    category: str | None = Field(None, description="Filter by category")
    format_ids: list[str] | None = Field(None, description="Filter by specific format IDs")

    def to_generated(self) -> _GeneratedListCreativeFormatsRequest:
        """Convert to generated schema for protocol validation."""
        return _GeneratedListCreativeFormatsRequest(**self.model_dump())


class ListAuthorizedPropertiesRequest(BaseModel):
    """Adapter for ListAuthorizedPropertiesRequest - simple pass-through to generated schema."""

    tags: list[str] | None = Field(None, description="Filter properties by specific tags")

    def to_generated(self) -> _GeneratedListAuthorizedPropertiesRequest:
        """Convert to generated schema for protocol validation."""
        return _GeneratedListAuthorizedPropertiesRequest(**self.model_dump())


# ============================================================================
# CreateMediaBuyResponse Adapter
# ============================================================================


class CreateMediaBuyResponse(AdCPBaseModel):
    """Adapter for CreateMediaBuyResponse - adds __str__() and internal field handling.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.

    Example:
        resp = CreateMediaBuyResponse(
            buyer_ref="buy_123",
            media_buy_id="mb_456",
            workflow_step_id="ws_789"  # Internal field
        )
        payload = resp.model_dump()  # AdCP-compliant (excludes workflow_step_id)
        db_data = resp.model_dump_internal()  # Includes workflow_step_id
        message = str(resp)  # "Media buy mb_456 created successfully."
    """

    model_config = {"arbitrary_types_allowed": True}

    # Required AdCP domain fields
    buyer_ref: str = Field(..., description="Buyer's reference identifier")

    # Optional AdCP domain fields
    media_buy_id: str | None = None
    creative_deadline: Any | None = None
    packages: list[Any] | None = Field(default_factory=list)
    errors: list[Any] | None = None

    # Internal fields (excluded from AdCP responses)
    workflow_step_id: str | None = None

    def model_dump(self, **kwargs):
        """AdCP-compliant dump (excludes internal fields)."""
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            exclude.add("workflow_step_id")
            kwargs["exclude"] = exclude
        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage."""
        kwargs.pop("exclude", None)
        return super().model_dump(**kwargs)

    def __str__(self) -> str:
        """Return human-readable message for protocol layer."""
        if self.media_buy_id:
            return f"Media buy {self.media_buy_id} created successfully."
        return f"Media buy {self.buyer_ref} created."


# ============================================================================
# UpdateMediaBuyResponse Adapter
# ============================================================================


class UpdateMediaBuyResponse(AdCPBaseModel):
    """Adapter for UpdateMediaBuyResponse - adds __str__() for protocol abstraction.

    Per AdCP PR #113, protocol fields excluded from domain response.
    """

    model_config = {"arbitrary_types_allowed": True}

    buyer_ref: str = Field(..., description="Buyer's reference identifier")
    media_buy_id: str | None = None
    affected_packages: list[Any] | None = Field(default_factory=list)
    errors: list[Any] | None = None

    def __str__(self) -> str:
        """Return human-readable message for protocol layer."""
        if self.media_buy_id:
            return f"Media buy {self.media_buy_id} updated successfully."
        return f"Media buy {self.buyer_ref} updated."


# ============================================================================
# SyncCreativesResponse Adapter
# ============================================================================


class SyncCreativesResponse(AdCPBaseModel):
    """Adapter for SyncCreativesResponse - adds __str__() for protocol abstraction.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.

    Official spec: /schemas/v1/media-buy/sync-creatives-response.json
    """

    model_config = {"arbitrary_types_allowed": True}

    # Required fields (per official spec)
    creatives: list[Any] = Field(..., description="Results for each creative processed")

    # Optional fields (per official spec)
    dry_run: bool | None = Field(None, description="Whether this was a dry run (no actual changes made)")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        # Count actions from creatives list
        created = sum(1 for c in self.creatives if isinstance(c, dict) and c.get("action") == "created")
        updated = sum(1 for c in self.creatives if isinstance(c, dict) and c.get("action") == "updated")
        deleted = sum(1 for c in self.creatives if isinstance(c, dict) and c.get("action") == "deleted")
        failed = sum(1 for c in self.creatives if isinstance(c, dict) and c.get("action") == "failed")

        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        if deleted:
            parts.append(f"{deleted} deleted")
        if failed:
            parts.append(f"{failed} failed")

        if parts:
            msg = f"Creative sync completed: {', '.join(parts)}"
        else:
            msg = "Creative sync completed: no changes"

        if self.dry_run:
            msg += " (dry run)"

        return msg


# ============================================================================
# GetMediaBuyDeliveryResponse Adapter
# ============================================================================


class GetMediaBuyDeliveryResponse(AdCPBaseModel):
    """Adapter for GetMediaBuyDeliveryResponse - adds __str__()."""

    model_config = {"arbitrary_types_allowed": True}

    # Required AdCP fields
    reporting_period: Any = Field(..., description="Date range for the report")
    currency: str = Field(..., pattern=r"^[A-Z]{3}$", description="ISO 4217 currency code")
    media_buy_deliveries: list[Any] = Field(..., description="Array of delivery data for each media buy")

    # Optional AdCP fields (webhook-specific)
    notification_type: str | None = None
    partial_data: bool | None = None
    unavailable_count: int | None = None
    sequence_number: int | None = None
    next_expected_at: str | None = None
    errors: list[Any] | None = None

    def __str__(self) -> str:
        """Return human-readable message for protocol layer."""
        count = len(self.media_buy_deliveries)
        if count == 0:
            return "No delivery data available."
        elif count == 1:
            return "Delivery data for 1 media buy."
        return f"Delivery data for {count} media buys."


# ============================================================================
# GetSignalsResponse Adapter
# ============================================================================


class GetSignalsResponse(AdCPBaseModel):
    """Adapter for GetSignalsResponse - adds __str__()."""

    model_config = {"arbitrary_types_allowed": True}

    message: str = Field(..., description="Human-readable summary")
    context_id: str = Field(..., description="Session continuity identifier")
    signals: list[Any] = Field(..., description="Array of matching signals")
    errors: list[Any] | None = None

    def __str__(self) -> str:
        """Return message field (it's in the spec for this one)."""
        return self.message


# ============================================================================
# ActivateSignalResponse Adapter
# ============================================================================


class ActivateSignalResponse(AdCPBaseModel):
    """Adapter for ActivateSignalResponse - adds __str__()."""

    model_config = {"arbitrary_types_allowed": True}

    task_id: str = Field(..., description="Unique identifier for tracking")
    status: str = Field(..., description="Current status (pending/processing/deployed/failed)")
    decisioning_platform_segment_id: str | None = None
    estimated_activation_duration_minutes: float | None = None
    deployed_at: str | None = None
    errors: list[Any] | None = None

    def __str__(self) -> str:
        """Return human-readable message for protocol layer."""
        if self.status == "deployed":
            return f"Signal activated successfully (platform ID: {self.decisioning_platform_segment_id})."
        elif self.status == "processing":
            eta = (
                f" (ETA: {self.estimated_activation_duration_minutes} min)"
                if self.estimated_activation_duration_minutes
                else ""
            )
            return f"Signal activation in progress{eta}."
        elif self.status == "pending":
            return f"Signal activation pending (task ID: {self.task_id})."
        elif self.status == "failed":
            return f"Signal activation failed (task ID: {self.task_id})."
        return f"Signal activation status: {self.status}."


# ============================================================================
# ListCreativesResponse Adapter
# ============================================================================


class ListCreativesResponse(AdCPBaseModel):
    """Adapter for ListCreativesResponse - adds __str__()."""

    model_config = {"arbitrary_types_allowed": True}

    query_summary: Any = Field(..., description="Summary of the query")
    pagination: Any = Field(..., description="Pagination information")
    creatives: list[Any] = Field(..., description="Array of creative assets")
    context_id: str | None = None
    format_summary: dict[str, int] | None = None
    status_summary: dict[str, int] | None = None

    def __str__(self) -> str:
        """Generate human-readable message from query_summary."""
        total = self.query_summary.total_matching
        returned = self.query_summary.returned
        if total == 0:
            return "No creatives found."
        elif returned == total:
            return f"Found {total} creative{'s' if total != 1 else ''}."
        else:
            return f"Found {total} creatives, showing {returned}."


# ============================================================================
# Template for Adding More Adapters
# ============================================================================

# TODO: Add adapters for remaining models as needed
