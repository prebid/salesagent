"""SpringServe adapter configuration schemas.

Connection schema supports two auth flows, in priority order:

  1. **Email + password (canonical)** -- ``email`` + ``password``. The client
     mints a token at ``POST /api/v0/auth`` on first use, caches it with TTL
     tracking (2 hours), and auto-refreshes on 401 or expiry. Recommended
     for production.

  2. **Pre-minted token (escape hatch)** -- ``api_token``. Useful when a
     partner provisions a token for us out-of-band or for tests against a
     shared sandbox. No auto-refresh -- rotate manually when the 2-hour
     TTL expires.

Exactly one of (email + password) OR api_token must be set. Both
``password`` and ``api_token`` are encrypted at rest with Fernet.

SpringServe is single-environment -- there is no separate staging host;
test accounts share ``console.springserve.com`` with production. We keep
an ``environment`` field anyway so the schema mirrors other adapters and
leaves room if SpringServe adds a sandbox host later.
"""

from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from src.adapters._secret_fields import decrypt_secret_value, encrypt_secret_value
from src.adapters.base import BaseConnectionConfig, BaseProductConfig

# SpringServe is a single-host platform; this dict exists for parity with
# other adapters and to give us a hook if a sandbox host appears.
SPRINGSERVE_HOSTS = {
    "production": "https://console.springserve.com/api/v0",
}


class SpringServeConnectionConfig(BaseConnectionConfig):
    """Email-grant or pre-minted-token config for the SpringServe API.

    Exactly one of (email + password) or api_token is required.
    """

    email: str | None = Field(
        default=None,
        description="SpringServe API user email (used for token minting)",
        json_schema_extra={"ui_order": 1},
    )
    password: str | None = Field(
        default=None,
        description="SpringServe API user password -- used to mint tokens via /api/v0/auth",
        json_schema_extra={"secret": True, "ui_order": 2},
    )
    api_token: str | None = Field(
        default=None,
        description=(
            "Pre-minted API token (advanced/testing). When set, "
            "email + password are ignored. Token has a 2-hour TTL and "
            "must be rotated manually."
        ),
        json_schema_extra={"secret": True, "ui_order": 3},
    )
    environment: Literal["production"] = Field(
        default="production",
        description="Which SpringServe environment to target",
        json_schema_extra={"ui_order": 4, "enum": ["production"]},
    )
    default_demand_partner_id: int | None = Field(
        default=None,
        description=(
            "Fallback SpringServe Demand Partner ID for principals without an "
            "explicit springserve mapping. The Demand Partner is the top-level "
            "commercial parent of all Campaigns / Demand Tags this adapter creates."
        ),
        json_schema_extra={"ui_order": 5},
    )
    demand_class: Literal["line_item", "tag"] = Field(
        default="line_item",
        description=(
            "How this tenant's buyers ship demand. 'line_item' = buyers ship raw "
            "creative assets that SpringServe hosts; the adapter uploads via "
            "POST /videos and binds via line_item_ratios on the demand tag. 'tag' = "
            "buyers ship a third-party VAST/audio URL; SpringServe passes through "
            "to that URL and no creative binding happens. The two classes have "
            "different UI affordances in SpringServe (Line Item class has a "
            "Creatives tab; Tag class does not), so this is a per-tenant "
            "provisioning decision tied to the buyer integration model."
        ),
        json_schema_extra={"ui_order": 6, "enum": ["line_item", "tag"]},
    )
    enable_key_value_targeting: bool = Field(
        default=False,
        description=(
            "Whether to translate AdCP signals into demand_tag_keys sub-resource "
            "entries on each created demand tag. Off by default — for most "
            "publishers, content / device / audience are expressed through "
            "supply-tag selection and demand-tag priorities, not KV targeting. "
            "Turn this on only when the publisher has free-form KV keys that "
            "aren't reflected in their supply taxonomy and you want AdCP signals "
            "to drive those keys directly."
        ),
        json_schema_extra={"ui_order": 7},
    )

    @property
    def base_url(self) -> str:
        return SPRINGSERVE_HOSTS[self.environment]

    @field_serializer("password")
    def _encrypt_password(self, value: str | None) -> str | None:
        return encrypt_secret_value(value)

    @field_validator("password", mode="after")
    @classmethod
    def _decrypt_password(cls, value: str | None) -> str | None:
        return decrypt_secret_value(value)

    @field_serializer("api_token")
    def _encrypt_token(self, value: str | None) -> str | None:
        return encrypt_secret_value(value)

    @field_validator("api_token", mode="after")
    @classmethod
    def _decrypt_token(cls, value: str | None) -> str | None:
        return decrypt_secret_value(value)

    @model_validator(mode="after")
    def _require_credentials(self) -> "SpringServeConnectionConfig":
        """Require either (email + password) or api_token."""
        has_password_grant = bool(self.email) and bool(self.password)
        has_token = bool(self.api_token)
        if not has_password_grant and not has_token:
            raise ValueError("SpringServe config requires either (email + password) or api_token")
        return self


class SpringServeProductConfig(BaseProductConfig):
    """Per-product SpringServe inventory + targeting selection.

    Fields are minimal for Stage 1; Stage 2 fleshes them out against the
    payloads observed on a live Talpa account. The shape follows the
    SpringServe Demand Tag API -- each AdCP package is realised as one
    SpringServe Demand Tag under a parent Campaign, so most product config
    maps directly to demand-tag fields.
    """

    # Inventory selection -- the supply tags this product is allowed to win.
    # Sourced from the synced inventory cache (Stage 5).
    supply_tag_ids: list[int] = Field(
        default_factory=list,
        description="SpringServe Supply Tag IDs this product can win against",
    )
    supply_partner_ids: list[int] = Field(
        default_factory=list,
        description="SpringServe Supply Partner IDs (broader scope than supply tags)",
    )

    # Player / environment targeting -- video defaults; audio uses a
    # different shape verified during Stage 3.
    player_sizes: list[str] = Field(
        default_factory=list,
        description="Allowed player sizes (e.g., 'large', 'medium', 'small') for video supply",
    )
    environments: list[str] = Field(
        default_factory=list,
        description="Delivery environments (e.g., 'app', 'web', 'ctv')",
    )
    device_types: list[str] = Field(
        default_factory=list,
        description="Device types (e.g., 'mobile', 'desktop', 'ctv', 'tablet')",
    )

    # Content classification
    content_genres: list[str] = Field(
        default_factory=list,
        description="Content genre IDs/labels (verify exact field name in Stage 2)",
    )

    # Note: audio vs video is determined by the Product's canonical
    # ``format_ids``. Audio formats route to audio creative MIME types; other
    # supported formats route to video. No denormalised flag on the adapter
    # config (Stage 3 wires the MIME negotiation).

    # Pricing / priority
    priority: int | None = Field(
        default=None,
        description="Demand-tag priority (higher = won first by SpringServe's auction)",
    )

    # Escape hatch for raw SpringServe demand-tag fields the schema doesn't
    # surface yet. Stage 2 will replace usage of this with typed fields.
    extra_demand_tag_fields: dict[str, object] = Field(
        default_factory=dict,
        description="Raw demand-tag field overrides (escape hatch; will narrow in Stage 2)",
    )
