"""Configuration models for Pydantic AI service."""

import logging
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class ModelSettings(BaseModel):
    """Settings for model behavior."""

    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout: int = Field(default=30, gt=0)


GOOGLE_PROVIDER_ALIASES: frozenset[str] = frozenset({"gemini", "google", "google-gla"})
CANONICAL_GOOGLE_PROVIDER = "google"

# The model that pairs with the Google provider. Named here because a provider and a
# model must travel together: a model name is only meaningful to the vendor it belongs
# to, so a Google client handed a "claude-*" name 404s on every call. This is the pin
# src/services/policy_check_service.py hard-coded at its own call site.
DEFAULT_GOOGLE_MODEL = "gemini-2.0-flash"
DEFAULT_PLATFORM_PROVIDER = "gemini"


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

    model_config = {"extra": "ignore"}  # Forward compatible with future fields

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
    def coerce(cls, value: Any, *, source: str = "tenant ai_config") -> "TenantAIConfig":
        """Tolerant parse of a STORED AI configuration value; caller mistakes still raise.

        Two failure modes arrive here and they need opposite answers:

        * **Malformed stored data** - a dict or list that came out of the JSONB column
          but does not satisfy this model (a settings block outside ModelSettings'
          bounds, a list where an object belongs). The seller's row is broken; the
          buyer's request is not. That degrades to a bare TenantAIConfig() with one
          WARNING. Strict parsing here raised pydantic's ValidationError, which
          subclasses ValueError and so was normalised by every transport into a
          VALIDATION_ERROR/correctable envelope naming a field (e.g.
          "settings.temperature") that does not exist in the buyer's request schema.
        * **A caller passing the wrong Python type** - a str, an int, an arbitrary
          object. `src/core/database/json_type.py` raises TypeError itself for anything
          that is not a dict or list, so these shapes CANNOT come from the column; they
          only come from a caller that passed the wrong argument (the reason this exists
          is `build_order_name_context(..., gemini_key)` landing a key string in the
          `tenant_ai_config` parameter). That raises TypeError naming the parameter and
          the offending type. Degrading it instead hid the mistake behind a log line and
          silently ran the AI feature on some other configuration.

        `source` names the value being parsed; it appears in both messages so the log
        or traceback identifies which tenant and which parameter is at fault.
        """
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, Mapping | list):
            raise TypeError(
                f"{source} must be a JSON object (dict) or None, got {type(value).__name__}. "
                "PostgreSQL JSONB only ever yields dict, list or None, so this value comes "
                "from a caller passing the wrong argument - pass it by keyword."
            )
        try:
            return cls.model_validate(value)
        except ValidationError as exc:
            logger.warning(
                "Ignoring malformed %s (%s); the AI feature degrades instead of failing the request: %s",
                source,
                type(value).__name__,
                exc,
            )
            return cls()

    @classmethod
    def from_tenant(cls, tenant: Any) -> "TenantAIConfig | None":
        """Resolve 'given a tenant, what is its AI configuration?' - the one owner of that rule.

        Accepts every shape a tenant travels as: the ORM Tenant row, TenantContext /
        LazyTenantContext, and the plain tenant dict. The field names `ai_config` and
        `gemini_api_key` are identical across all three; only the ACCESS MECHANISM
        differs (the ORM row has no `.get()`, the plain dict has no attribute access),
        which is why the read goes through `_read_tenant_field`.

        USABILITY IS THE CONTRACT. A config comes back only when the tenant has its OWN
        usable credential, because that is what every consumer asks this question for.
        Resolution order:

        1. `ai_config` parses AND carries a non-empty `api_key` -> that config.
        2. Otherwise the legacy `gemini_api_key` column -> the canonical Google config,
           provider and model pinned together.
        3. Otherwise None.

        A row carrying provider/model but NO api_key is not a tenant configuration.
        `src/admin/blueprints/settings.py` writes exactly that shape when the seller
        leaves the key field blank, and tells them "AI features will be disabled" as it
        does. Returning it as a real config opened the policy gate in
        `src/core/tools/products.py` for those tenants and ran `check_brief_compliance`
        on the OPERATOR's platform credential - a live LLM call, and a BLOCKED verdict
        there is a new buyer-visible rejection. None makes that gate skip, and lets
        ranking fall back to the platform's own coherent provider+model+key instead.

        A MALFORMED row resolves to nothing, never to a different vendor. `coerce`
        degrades it to a bare config, which carries no api_key, so it falls through to
        step 2 and then to None - it is never returned as if it were the tenant's
        configuration. Returning the bare config let the factory fill in the PLATFORM's
        provider and key, so a seller who had deliberately configured Anthropic had the
        buyer's brief and product catalog sent to Google, unranked-advisory-free.

        The absent-test is FALSY, not `is None`: an empty `ai_config` dict (`{}`)
        carries no provider and no key, so it must fall through to the legacy field
        rather than shadow it. This resolves the divergence with
        `src/core/utils/naming.py`, which tested `is None` while every other site
        tested falsiness.

        It NEVER falls back to the platform environment key - that decision belongs to
        the caller, because the two consumers (ranking, policy) want different answers
        to it.
        """
        if tenant is None:
            return None

        tenant_id = _read_tenant_field(tenant, "tenant_id")
        source = f"tenant ai_config for tenant {tenant_id!r}" if tenant_id else "tenant ai_config"

        ai_config = _read_tenant_field(tenant, "ai_config")
        if ai_config:
            config = cls.coerce(ai_config, source=source)
            if config.api_key:
                return config

        legacy_key = _read_tenant_field(tenant, "gemini_api_key")
        if legacy_key:
            # CANONICAL_GOOGLE_PROVIDER, not the literal "gemini": every consumer of
            # the provider string canonicalizes before comparing or using it
            # (canonicalize_google_provider at config.py _get_provider_api_key /
            # build_model_string, factory.create_model, and uses_legacy_gemini_api_key),
            # so the two spellings are interchangeable downstream.
            #
            # The MODEL is pinned with the provider. Left unset, the factory filled it
            # from the platform's PYDANTIC_AI_MODEL - so a deployment configured for
            # Anthropic handed "claude-sonnet-4-…" to a Google client built on this
            # tenant's Gemini key, and every call 404'd.
            return cls(provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key=legacy_key)

        return None


def _read_tenant_field(tenant: Any, field: str) -> Any:
    """Read one field from a tenant in whichever shape it arrived.

    Plain dicts answer to `.get()`; ORM rows and TenantContext answer to attribute
    access. Missing fields read as None rather than raising, so a partial projection
    degrades to "not configured" instead of failing the request.
    """
    if isinstance(tenant, Mapping):
        return tenant.get(field)
    return getattr(tenant, field, None)


def get_platform_defaults() -> dict:
    """Get platform-level AI configuration from environment variables.

    Returns:
        dict with platform default settings
    """
    provider = os.getenv("PYDANTIC_AI_PROVIDER", DEFAULT_PLATFORM_PROVIDER)
    return {
        "provider": provider,
        "model": os.getenv("PYDANTIC_AI_MODEL") or default_model_for(provider),
        "api_key": _get_provider_api_key(provider),
        "logfire_token": os.getenv("LOGFIRE_TOKEN"),
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
    """Get the API key for a specific provider from environment.

    Args:
        provider: The provider name (gemini, openai, anthropic, etc.)

    Returns:
        API key if found, None otherwise
    """
    provider = canonicalize_google_provider(provider)
    provider_env_vars = {
        "google": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "groq": "GROQ_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",  # Bedrock uses AWS credentials
    }
    env_var = provider_env_vars.get(provider)
    if env_var:
        return os.getenv(env_var)
    return None


def build_model_string(provider: str, model: str) -> str:
    """Build the Pydantic AI model string.

    Pydantic AI uses format: "provider:model" (e.g., "google:gemini-2.0-flash")

    Args:
        provider: Provider name (e.g., "google-gla", "anthropic", "gateway/openai")
        model: Model name

    Returns:
        Pydantic AI model string
    """
    provider = canonicalize_google_provider(provider)
    return f"{provider}:{model}"
