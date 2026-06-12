"""FreeWheel adapter configuration schemas.

Connection schema supports three auth flows:

  1. **OAuth2 client_credentials (API-Access)** — ``client_id`` +
     ``client_secret``. The client mints a short-lived (~1h) bearer from the
     API-Access token service (``token_url``, a different host than the data
     plane) and auto-refreshes. Pair with ``environment="sandbox"`` to target
     ``api.sandbox.freewheel.tv``.

  2. **OAuth2 password grant** — ``username`` + ``password``. The client mints
     a bearer at ``POST {base_url}/auth/token`` on first use, caches it with
     TTL tracking, and auto-refreshes on 401 or expiry.

  3. **Pre-minted bearer token (escape hatch)** — ``api_token``. Used when a
     partner provisions a token out-of-band, or for testing. No auto-refresh —
     when the TTL expires, rotate manually.

At least one of (client_id+client_secret), (username+password), or api_token
must be set. ``password``, ``api_token``, and ``client_secret`` are encrypted
at rest with Fernet — same pattern as ``TritonConnectionConfig.password``.
"""

from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from src.adapters._secret_fields import decrypt_secret_value, encrypt_secret_value
from src.adapters.base import BaseConnectionConfig, BaseProductConfig
from src.adapters.freewheel._transport import DEFAULT_CLIENT_CREDENTIALS_TOKEN_URL

# Environment -> API host mapping. Tokens are environment-scoped — a token
# minted for one environment won't work against another.
FREEWHEEL_HOSTS = {
    "production": "https://api.freewheel.tv",
    "staging": "https://api.stg.freewheel.tv",
    "sandbox": "https://api.sandbox.freewheel.tv",
}


class FreeWheelConnectionConfig(BaseConnectionConfig):
    """Auth config for the FreeWheel Publisher API. At least one of
    (client_id + client_secret), (username + password), or api_token is
    required. See the module docstring for the three auth flows."""

    username: str | None = Field(
        default=None,
        description="FreeWheel User ID for OAuth2 password-grant authentication",
        json_schema_extra={"ui_order": 1},
    )
    password: str | None = Field(
        default=None,
        description="FreeWheel password — used to mint bearer tokens via /auth/token",
        json_schema_extra={"secret": True, "ui_order": 2},
    )
    api_token: str | None = Field(
        default=None,
        description=(
            "Pre-minted bearer token (advanced/testing). When set, "
            "username+password are ignored. Token has a ~7-day TTL and "
            "must be rotated manually."
        ),
        json_schema_extra={"secret": True, "ui_order": 3},
    )
    client_id: str | None = Field(
        default=None,
        description="API-Access OAuth2 client ID (client_credentials grant)",
        json_schema_extra={"ui_order": 6},
    )
    client_secret: str | None = Field(
        default=None,
        description="API-Access OAuth2 client secret — used to mint bearer tokens via the token service",
        json_schema_extra={"secret": True, "ui_order": 7},
    )
    token_url: str = Field(
        default=DEFAULT_CLIENT_CREDENTIALS_TOKEN_URL,
        description="API-Access token service endpoint for the client_credentials grant",
        json_schema_extra={"ui_order": 8},
    )
    environment: Literal["production", "staging", "sandbox"] = Field(
        default="production",
        description="Which FreeWheel environment to target",
        json_schema_extra={"ui_order": 4, "enum": ["production", "staging", "sandbox"]},
    )
    default_advertiser_id: str | None = Field(
        default=None,
        description="Fallback FreeWheel advertiser ID for principals without explicit freewheel mappings",
        json_schema_extra={"ui_order": 5},
    )

    @property
    def base_url(self) -> str:
        return FREEWHEEL_HOSTS[self.environment]

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

    @field_serializer("client_secret")
    def _encrypt_client_secret(self, value: str | None) -> str | None:
        return encrypt_secret_value(value)

    @field_validator("client_secret", mode="after")
    @classmethod
    def _decrypt_client_secret(cls, value: str | None) -> str | None:
        return decrypt_secret_value(value)

    @field_validator("token_url", mode="after")
    @classmethod
    def _token_url_must_be_https(cls, value: str) -> str:
        # A tenant admin who can edit connection config must not be able to
        # point the client_credentials mint at an attacker host and exfiltrate
        # the client_secret.
        if not value.startswith("https://"):
            raise ValueError(f"token_url must be an https:// URL — got {value!r}")
        return value

    @model_validator(mode="after")
    def _require_credentials(self) -> "FreeWheelConnectionConfig":
        """Require one of (client_id + client_secret), (username + password), or api_token."""
        has_client_credentials = bool(self.client_id) and bool(self.client_secret)
        has_password_grant = bool(self.username) and bool(self.password)
        has_token = bool(self.api_token)
        if not (has_client_credentials or has_password_grant or has_token):
            raise ValueError(
                "FreeWheel config requires one of: (client_id + client_secret), (username + password), or api_token"
            )
        return self


class FreeWheelProductConfig(BaseProductConfig):
    """Per-product FreeWheel inventory + targeting selection.

    Configuration follows FreeWheel's real data model (Sites + content
    groupings, not raw "placement IDs" — placements are created per buy
    by the adapter, not pre-built inventory). All IDs reference rows in
    the local ``freewheel_inventory`` cache populated by
    :class:`FreeWheelInventorySync`; the product setup UI picks from that
    cache rather than asking publishers to type IDs.

    Targeting fields are stored on the product so the adapter can apply
    them at buy-creation time. End-to-end delivery currently depends on
    additional v4 scopes (``ad_unit_nodes`` write, ``creative_instances``
    write); see :mod:`src.adapters.freewheel.adapter` for the blocker
    list. Until those land, the product config is captured but not yet
    fully exercised against live delivery.
    """

    # Inventory targeting — references freewheel_inventory cache
    site_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel Site IDs this product delivers into",
    )
    site_section_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel Site Section IDs (sub-sections within a site)",
    )
    video_group_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel Video Group IDs. On Talpa-style networks these are "
            "audience-segmented groupings (e.g., 'DOELGROEP INDEX 150+ | "
            "Sociale D13+') and are the canonical targeting primitive."
        ),
    )
    series_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel Series IDs (specific shows)",
    )
    ad_unit_package_id: int | None = Field(
        default=None,
        description=(
            "FreeWheel Ad Unit Package ID (e.g., 'Pre-Mid' = pre+mid-roll, "
            "'Pre-Mid-Post' = pre+mid+post-roll). Determines which slot "
            "positions the product can deliver into."
        ),
    )

    # Audience targeting
    viewership_profile_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel viewership profile IDs (from standard_attributes."
            "viewership_profiles). Network-standardized audiences "
            "(e.g., 'Adults', 'Adults 25-34'). For richer custom audience "
            "targeting, also see ``video_group_ids`` (publisher-curated) and "
            "``audience_item_ids`` (Data Suite, gated)."
        ),
    )
    audience_item_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel Audience Item IDs from the Data Suite API. Requires "
            "AUDIENCE_TAB + FW_DATA_SUITE_BUY_SIDE + FW_DATA_SUITE_SELL_SIDE "
            "features enabled at the network level. Stored on the product but "
            "not currently exercised against the live API (token-side feature "
            "flag denied for our Talpa setup)."
        ),
    )

    # Content classification
    genre_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel content genre IDs (from standard_attributes.genres)",
    )
    content_daypart_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel content daypart IDs (from standard_attributes."
            "content_dayparts). Talpa exposes 'Daytime', 'Latenight', 'Primetime'."
        ),
    )
    content_duration_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel content duration IDs (from standard_attributes."
            "content_durations). Buckets: 'Short Form', 'Mid Form', 'Long Form'."
        ),
    )
    content_territory_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel content territory IDs (from standard_attributes."
            "content_territories). Geographic content scoping."
        ),
    )
    language_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel language IDs (from standard_attributes.languages)",
    )

    # Delivery context
    device_type_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel device type IDs (from standard_attributes.device_types). "
            "76 entries on Talpa — CTV apps, mobile, desktop, etc."
        ),
    )
    os_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel OS IDs (from standard_attributes.oss) — Android/iOS/Windows",
    )
    environment_ids: list[int] = Field(
        default_factory=list,
        description="FreeWheel environment IDs (from standard_attributes.environments) — App vs Web",
    )
    stream_type_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel stream type IDs (from standard_attributes.stream_types). "
            "Talpa: Digital DAI, Linear Addressable, Live, Live Events, etc."
        ),
    )
    subscription_model_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel subscription model IDs (from standard_attributes."
            "subscription_models). Talpa: FAST, Full Ad Load, Light Ad Load."
        ),
    )

    # Privacy + compliance
    addressability_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel addressability category IDs (from standard_attributes."
            "addressabilities). Cookies / Device ID / Federated Segments / etc."
        ),
    )
    privacy_signal_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel privacy signal IDs (from standard_attributes.privacies). "
            "Talpa: Has Privacy Signal(s), Privacy Signal Opt-Out Eligible, "
            "No Privacy Signal."
        ),
    )
    tv_rating_ids: list[int] = Field(
        default_factory=list,
        description=(
            "FreeWheel TV Rating IDs (from standard_attributes.tv_ratings) "
            "restricting which rated content this product delivers against."
        ),
    )

    # Pricing / priority
    priority: int | None = Field(
        default=None,
        description="Line item priority (lower = higher priority)",
    )
    price_model: str | None = Field(
        default=None,
        description=(
            "FreeWheel pricing model for ad_unit_nodes (e.g., ACTUAL_ECPM, "
            "FIXED_PRICE). Optional; defaults to the network's policy."
        ),
    )

    # Escape hatches kept from the previous schema for forward compatibility
    targeting_profile_id: str | None = Field(
        default=None,
        description="Optional pre-built FreeWheel targeting profile ID",
    )
    custom_targeting: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "FreeWheel custom key-value targeting (e.g. {'genre': "
            "['sports','news']}). Requires v4 custom_keys scope on the "
            "bearer; currently denied for our test token."
        ),
    )
