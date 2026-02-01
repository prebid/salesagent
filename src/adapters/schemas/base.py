"""
Base schema classes for adapter configurations.

Each adapter extends these base classes with adapter-specific fields.
Schemas provide:
- Type validation via Pydantic
- Field documentation via descriptions
- Configuration structure enforcement
"""

from pydantic import BaseModel, ConfigDict, Field


class BaseConnectionConfig(BaseModel):
    """Base schema for adapter connection configuration.

    Defines credentials and settings needed to connect to an ad server.
    Stored in AdapterConfig.config_json at the tenant level.
    """

    model_config = ConfigDict(extra="forbid")

    manual_approval_required: bool = Field(
        default=False,
        description="Require human approval for operations like create_media_buy",
    )


class BaseProductConfig(BaseModel):
    """Base schema for product-level adapter configuration.

    Defines settings specific to how a product is configured in the ad server.
    Stored in Product.implementation_config.
    """

    model_config = ConfigDict(extra="forbid")


class BaseInventoryConfig(BaseModel):
    """Base schema for inventory profile configuration.

    Defines how inventory is structured and targeted.
    Stored in InventoryProfile.inventory_config.
    """

    model_config = ConfigDict(extra="forbid")
