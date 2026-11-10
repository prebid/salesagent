"""Configuration models for Pydantic AI service."""

import logging
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, Field, ValidationError

from src.core.config import DEFAULT_GOOGLE_MODEL, get_settings
from src.core.logging_config import log_safe

if TYPE_CHECKING:
    from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


class ModelSettings(BaseModel):
    """Settings for model behavior."""

    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout: int = Field(default=30, gt=0)


GOOGLE_PROVIDER_ALIASES: frozenset[str] = frozenset({"gemini", "google", "google-gla"})
CANONICAL_GOOGLE_PROVIDER = "google"


def canonicalize_google_provider(provider: str) -> str:
    """Map legacy Google provider names to pydantic-ai 1.99 canonical form."""
    if provider in GOOGLE_PROVIDER_ALIASES:
        return CANONICAL_GOOGLE_PROVIDER
    return provider


def uses_legacy_gemini_api_key(provider: str) -> bool:
    """True when admin AI settings should read tenant.gemini_api_key for this provider."""
    return canonicalize_google_provider(provider) == CANONICAL_GOOGLE_PROVIDER


class TenantAIConfig(BaseModel):
    """Per-tenant AI configuration stored in database.

    This model defines the structure of the `ai_config` JSON column on the Tenant table.
    All fields are optional - tenants inherit platform defaults for any unset values.
    """

    # extra="ignore": forward compatible with future fields. hide_input_in_errors: a
    # ValidationError renders the value it rejected, and a stored row carries the seller's
    # API key, so a malformed row would otherwise put that key in the WARNING `from_tenant` logs.
    model_config = {"extra": "ignore", "hide_input_in_errors": True}

    # Model selection - accepts any Pydantic AI provider string
    # e.g., "google-gla", "anthropic", "openai", "gateway/anthropic", etc.
    provider: str | None = None
    model: str | None = None  # e.g., "gemini-2.0-flash", "claude-sonnet-4-20250514"

    # API key (encrypted in database, decrypted when loaded)
    api_key: str | None = None

    # Observability
    logfire_token: str | None = None

    # Model behavior settings
    settings: ModelSettings = Field(default_factory=ModelSettings)

    @classmethod
    def _parse_stored(cls, value: Any, *, source: str) -> "TenantAIConfig | None":
        """`from_tenant`'s parse of the stored row: the config, or None for a row that does not parse.

        Malformed stored data (a dict or list out of the JSONB column that does not satisfy
        this model) is the seller's broken row, not the buyer's request, so it is logged once
        and answered None, which `from_tenant` turns into `UNUSABLE_AI_CONFIG`. Raising
        pydantic's ValidationError instead reached the buyer as a VALIDATION_ERROR naming a
        field (``settings.temperature``) their request schema does not have.

        Any other Python type cannot come out of the column (`src/core/database/json_type.py`
        refuses everything that is not a dict or list), so it raises TypeError rather than
        being logged as seller data.
        """
        if not isinstance(value, Mapping | list):
            raise TypeError(
                f"{source} must be a JSON object (dict) or None, got {type(value).__name__}. "
                "PostgreSQL JSONB only ever yields dict, list or None, so this value did not "
                "come from the ai_config column."
            )
        try:
            return cls.model_validate(value)
        except ValidationError as exc:
            # log_safe on exc: a ValidationError's message embeds the value it
            # rejected, which is seller-stored JSON, and pydantic renders it over
            # several lines. Without the escape the rejected value picks the line
            # breaks in this log entry.
            logger.warning(
                "Ignoring malformed %s (%s); the AI feature degrades instead of failing the request: %s",
                source,
                type(value).__name__,
                log_safe(exc),
            )
            return None

    @classmethod
    def from_tenant(cls, tenant: "Tenant") -> "SellerAIConfig":
        """Resolve 'given a tenant row, what AI did the seller configure?' - the one owner of that rule.

        Called once per request, by `TenantContext.from_orm_model`; every consumer reads
        `TenantContext.ai_config` rather than asking again. The admin UI, which holds the
        ORM row and no context, calls it on the row.

        Three answers, because the consumers need to tell them apart:

        * a `TenantAIConfig` - the seller's own usable configuration, with its own key:
          `ai_config` when it carries an `api_key`, else the legacy `gemini_api_key` column
          as the canonical Google config, provider and model pinned together;
        * `None` - the seller configured nothing. Only this answer may fall back to the
          platform's own credential, which ranking, order naming and creative review do;
        * `UNUSABLE_AI_CONFIG` - the seller configured AI that cannot run: a stored row
          that does not parse, or one naming a provider with no key. AI is OFF for this
          seller. Answering None here sent the buyer's brief to the operator's Google key
          for a seller who had chosen Anthropic, and for one the settings page had just
          told "AI features will be disabled" (`src/admin/blueprints/settings.py` writes a
          provider/model row with no key when the key field is left blank).

        A row that names no provider and no key (`{}`, or settings alone) configured no
        model, so it falls through to the legacy column like an absent one. A keyless row
        naming Google still takes the legacy key, which belongs to that same vendor.

        It NEVER falls back to the platform environment key - that decision belongs to
        the factory, for the callers that want it.
        """
        legacy = (
            # CANONICAL_GOOGLE_PROVIDER, not the literal "gemini": every consumer of the
            # provider string canonicalizes before comparing or using it. The MODEL is
            # pinned with the provider: left unset, the factory filled it from the
            # platform's PYDANTIC_AI_MODEL, so a deployment configured for Anthropic handed
            # "claude-sonnet-4-…" to a Google client built on this tenant's Gemini key.
            cls(provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key=tenant.gemini_api_key)
            if tenant.gemini_api_key
            else None
        )
        if not tenant.ai_config:
            return legacy

        # log_safe around the whole label: it is interpolated into the WARNING that
        # _parse_stored logs, so the tenant identifier reaches a log line through it.
        config = cls._parse_stored(
            tenant.ai_config, source=log_safe(f"tenant ai_config for tenant {tenant.tenant_id!r}")
        )
        if config is None:
            return UNUSABLE_AI_CONFIG
        if config.api_key:
            return config
        if config.provider is None or (legacy is not None and uses_legacy_gemini_api_key(config.provider)):
            return legacy
        return UNUSABLE_AI_CONFIG


class _UnusableAIConfig(Enum):
    """The type of `UNUSABLE_AI_CONFIG`; an Enum so mypy narrows it with `is`."""

    UNUSABLE = "unusable"


#: The seller configured AI that cannot run (see `TenantAIConfig.from_tenant`).
UNUSABLE_AI_CONFIG: Final = _UnusableAIConfig.UNUSABLE

#: What `TenantAIConfig.from_tenant` answers: the seller's own config, nothing, or unusable.
SellerAIConfig = TenantAIConfig | Literal[_UnusableAIConfig.UNUSABLE] | None


def tenant_google_api_key(seller_ai: SellerAIConfig) -> str | None:
    """The tenant's own Google (Gemini) API key, or ``None``.

    *seller_ai* is what `TenantAIConfig.from_tenant` answered (``TenantContext.ai_config``),
    so the generative creative build reads the seller's key through the same rule as every
    other AI consumer. The key is a Google key only when the provider it is configured with
    canonicalizes to Google; a configuration that names no provider runs on the platform
    provider, as `AIServiceFactory.resolve` resolves it.

    Only the tenant's own configuration is read. The platform default key
    (``GEMINI_API_KEY``) is not consulted here.
    """
    if not isinstance(seller_ai, TenantAIConfig):
        return None
    provider = seller_ai.provider or get_settings().integrations.pydantic_ai_provider
    if canonicalize_google_provider(provider) != CANONICAL_GOOGLE_PROVIDER:
        return None
    return seller_ai.api_key or None


def get_platform_defaults() -> dict:
    """Get platform-level AI configuration from the startup settings.

    Returns:
        dict with platform default settings
    """
    integrations = get_settings().integrations
    provider = integrations.pydantic_ai_provider
    # The settings field defaults to a Google model, so it names a model only when the
    # operator set it. Otherwise the model follows the provider (see default_model_for).
    model = (
        integrations.pydantic_ai_model
        if "pydantic_ai_model" in integrations.model_fields_set
        else default_model_for(provider)
    )
    return {
        "provider": provider,
        "model": model,
        "api_key": _get_provider_api_key(provider),
        "logfire_token": integrations.logfire_token,
    }


def default_model_for(provider: str) -> str | None:
    """The model this codebase can name for `provider` without being told.

    Only Google: "gemini-2.0-flash" is what the deployment default has always meant, and
    it is the pin `policy_check_service` carried at its own call site. For any other
    provider we do not know a model, and None is the honest answer - guessing carries a
    model name across vendors, which is how a Google client ended up asking for
    "claude-sonnet-4-…" (404 on every call).
    """
    if canonicalize_google_provider(provider) == CANONICAL_GOOGLE_PROVIDER:
        return DEFAULT_GOOGLE_MODEL
    return None


def _get_provider_api_key(provider: str) -> str | None:
    """Get the API key for a specific provider from the startup settings.

    Args:
        provider: The provider name (gemini, openai, anthropic, etc.)

    Returns:
        API key if found, None otherwise
    """
    return get_settings().integrations.provider_api_key(canonicalize_google_provider(provider))
