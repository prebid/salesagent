"""Tests for AI service factory and configuration."""

import logging
import os
from unittest.mock import patch

import pytest

from src.services.ai import (
    AIServiceFactory,
    ModelSettings,
    TenantAIConfig,
    build_model_string,
    get_platform_defaults,
)
from src.services.ai.config import (
    CANONICAL_GOOGLE_PROVIDER,
    DEFAULT_GOOGLE_MODEL,
    GOOGLE_PROVIDER_ALIASES,
    uses_legacy_gemini_api_key,
)


def credential_of(model) -> str | None:
    """The API key the built model will actually authenticate with.

    Reaches through the pydantic-ai model to the vendor client because that is the only
    place the answer exists: `create_model` returns a Model, and which SECRET it carries
    is exactly the property under test. Anthropic/OpenAI/Groq clients expose `api_key`
    directly; the Google client keeps it on its inner `_api_client`.
    """
    client = model.client
    if hasattr(client, "api_key"):
        return client.api_key
    return getattr(getattr(client, "_api_client", None), "api_key", None)


class TestTenantAIConfig:
    """Tests for TenantAIConfig model."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = TenantAIConfig()
        assert config.provider is None
        assert config.model is None
        assert config.api_key is None
        assert config.logfire_token is None
        assert config.settings.temperature == 0.3
        assert config.settings.timeout == 30

    def test_parse_from_dict(self):
        """Config can be parsed from dict (database JSON)."""
        config = TenantAIConfig.model_validate(
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "settings": {"temperature": 0.5}}
        )
        assert config.provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"
        assert config.settings.temperature == 0.5

    def test_extra_fields_ignored(self):
        """Unknown fields are ignored for forward compatibility."""
        config = TenantAIConfig.model_validate(
            {
                "provider": "gemini",
                "future_field": "some value",
            }
        )
        assert config.provider == "gemini"
        assert not hasattr(config, "future_field")


class TestTenantAIConfigCoerce:
    """TenantAIConfig.coerce is the one tolerant parse of a stored ai_config value."""

    def test_dict_is_parsed(self):
        """A well-formed dict parses into a real config."""
        config = TenantAIConfig.coerce({"provider": "anthropic", "model": "claude-sonnet-4-20250514"})
        assert config.provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"

    def test_existing_instance_passes_through_unchanged(self):
        """An already-parsed TenantAIConfig is returned as the same object, not re-validated."""
        original = TenantAIConfig(provider="openai", api_key="k")
        assert TenantAIConfig.coerce(original) is original

    def test_none_yields_platform_defaults(self):
        """None means 'nothing configured' — a bare config, so the factory uses platform defaults."""
        config = TenantAIConfig.coerce(None)
        assert config.provider is None
        assert config.api_key is None

    def test_malformed_settings_degrade_to_defaults_with_one_warning(self, caplog):
        """A settings block that violates ModelSettings bounds must NOT raise.

        Regression guard: TenantAIConfig.model_validate raises pydantic's
        ValidationError, which subclasses ValueError, so a malformed seller-side
        ai_config row reached the buyer as a VALIDATION_ERROR/correctable envelope
        naming 'settings.temperature' — a field that does not exist in the request
        schema. A broken config row must degrade the AI feature, not fail the request.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            config = TenantAIConfig.coerce({"provider": "gemini", "settings": {"temperature": 9}})

        assert config.provider is None, "malformed config must fall back to platform defaults entirely"
        assert config.settings.temperature == 0.3
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one WARNING, got {[r.message for r in warnings]}"

    def test_list_from_the_jsonb_column_degrades_instead_of_raising(self, caplog):
        """A list is a shape the JSONB column can hold, so it is malformed DATA: degrade.

        Before coerce, the factory's `elif tenant_ai_config:` branch bound such a value
        straight to `config`, and the next line (`config.api_key`) died with AttributeError.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            config = TenantAIConfig.coerce(["provider", "gemini"])

        assert config == TenantAIConfig()
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    @pytest.mark.parametrize("value", ["gemini", 42, object()])
    def test_caller_type_mistake_raises_type_error(self, value, caplog):
        """A str/int/object can only come from a caller bug, so it must RAISE.

        `src/core/database/json_type.py` raises TypeError itself for anything that is
        not a dict or list, so these shapes never come out of the ai_config column. They
        come from a caller passing the wrong argument — the live example being
        `build_order_name_context(..., gemini_key)`, which lands an API-key string in
        the `tenant_ai_config` parameter. Degrading that to platform defaults hid the
        mistake behind a log line and quietly ran the AI feature on somebody else's
        configuration; `code-patterns.md` names exactly that shape as wrong.

        The message must name both the offending type and the parameter, because the
        caller's only clue is this exception.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            with pytest.raises(TypeError) as excinfo:
                TenantAIConfig.coerce(value)

        assert type(value).__name__ in str(excinfo.value)
        assert "ai_config" in str(excinfo.value)
        assert [r.message for r in caplog.records if r.levelno == logging.WARNING] == [], (
            "a caller mistake is raised, not logged-and-swallowed"
        )

    def test_source_label_names_the_offending_parameter(self):
        """`source` identifies which value was wrong — the caller's only clue."""
        with pytest.raises(TypeError, match="naming.tenant_ai_config"):
            TenantAIConfig.coerce("AIzaSy...", source="naming.tenant_ai_config")

    def test_empty_dict_yields_platform_defaults(self):
        """An empty ai_config is valid and simply carries nothing."""
        assert TenantAIConfig.coerce({}) == TenantAIConfig()


class TestTenantAIConfigFromTenant:
    """TenantAIConfig.from_tenant owns 'given a tenant, what is its AI configuration?'."""

    def test_ai_config_wins_over_legacy_key(self):
        """A tenant's own ai_config supersedes the legacy gemini_api_key column."""
        tenant = {"ai_config": {"provider": "anthropic", "api_key": "tenant-key"}, "gemini_api_key": "legacy-key"}

        config = TenantAIConfig.from_tenant(tenant)

        assert config is not None
        assert config.provider == "anthropic"
        assert config.api_key == "tenant-key"

    def test_legacy_key_alone_becomes_canonical_google_config(self):
        """With no ai_config, the legacy key is surfaced as a Google-provider config.

        The provider is CANONICAL_GOOGLE_PROVIDER rather than the literal "gemini":
        every consumer canonicalizes the string before comparing or using it, so the
        two spellings are interchangeable downstream.

        The MODEL travels with the provider. Left None, the factory filled it from the
        platform's PYDANTIC_AI_MODEL, which names whatever vendor the DEPLOYMENT runs —
        so a deployment set to Anthropic produced a Google client asking for
        "claude-sonnet-4-…" on this tenant's Gemini key, and every call 404'd.
        `policy_check_service` pinned "gemini-2.0-flash" at its own call site for
        exactly this reason; the pin belongs here, with the provider it goes with.
        """
        config = TenantAIConfig.from_tenant({"ai_config": None, "gemini_api_key": "legacy-key"})

        assert config is not None
        assert config.provider == CANONICAL_GOOGLE_PROVIDER
        assert config.api_key == "legacy-key"
        assert config.model == DEFAULT_GOOGLE_MODEL

    def test_provider_and_model_without_a_key_is_not_a_tenant_config(self):
        """A row with provider/model but no api_key resolves to None, not to a config.

        `src/admin/blueprints/settings.py` writes exactly {"provider": …, "model": …}
        when the seller submits the AI form with the key field blank, and flashes "no
        API key configured. AI features will be disabled." Returning that row as a real
        configuration opened the policy gate in `_get_products_impl` for those tenants,
        which then ran `check_brief_compliance` — a live LLM call — on the OPERATOR's
        platform credential, and a BLOCKED verdict there is a buyer-visible rejection
        the seller never asked for.

        The platform key is set here on purpose: the answer must be None because the
        TENANT has no usable credential, not because no key exists anywhere.
        """
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=False):
            assert TenantAIConfig.from_tenant({"ai_config": {"provider": "anthropic", "model": "claude-x"}}) is None

    def test_keyless_ai_config_falls_through_to_the_legacy_key(self):
        """An unusable ai_config does not shadow a legacy key that IS usable."""
        config = TenantAIConfig.from_tenant(
            {"ai_config": {"provider": "google", "model": "gemini-2.0-flash"}, "gemini_api_key": "legacy-key"}
        )

        assert config is not None
        assert config.api_key == "legacy-key"
        assert config.provider == CANONICAL_GOOGLE_PROVIDER

    def test_both_absent_returns_none(self):
        """Nothing configured means None — from_tenant NEVER reaches for the platform key."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=False):
            assert TenantAIConfig.from_tenant({"ai_config": None, "gemini_api_key": None}) is None

    def test_empty_ai_config_falls_through_to_legacy_key(self):
        """The absent-test is falsy, not `is None`.

        An empty ai_config dict carries no provider and no key, so it must not shadow
        the legacy column. src/core/utils/naming.py tested `is None` and so returned an
        empty config here, disabling AI for a tenant that had a working legacy key.
        """
        config = TenantAIConfig.from_tenant({"ai_config": {}, "gemini_api_key": "legacy-key"})

        assert config is not None
        assert config.api_key == "legacy-key"

    def test_malformed_ai_config_resolves_to_nothing_not_a_bare_config(self, caplog):
        """A malformed row degrades to None — never to a config the factory refills.

        This test previously asserted `config == TenantAIConfig()`, which pinned the
        defect: a bare config is falsy in no way the caller can see, so the ranking path
        treated it as "the tenant is configured", the factory filled in the PLATFORM's
        provider and key, and the seller who had deliberately chosen Anthropic had the
        buyer's brief and product catalog sent to Google — with no advisory, because
        `is_ai_enabled` said True on the platform key.

        The api_key in the row is REAL and valid; only `settings.temperature` is out of
        bounds. Returning None anyway is the point: a row we cannot parse is a row we
        cannot act on, and one WARNING naming the tenant is how the seller finds out.
        """
        malformed = {
            "tenant_id": "t1",
            "ai_config": {"provider": "anthropic", "api_key": "sk-ant-real", "settings": {"temperature": 9}},
        }

        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=False):
            with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
                config = TenantAIConfig.from_tenant(malformed)

        assert config is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one WARNING, got {[r.getMessage() for r in warnings]}"
        assert "t1" in warnings[0].getMessage(), "the WARNING must name the tenant whose row is broken"

    def test_malformed_ai_config_still_falls_through_to_the_legacy_key(self):
        """A broken ai_config does not cost the tenant a legacy key that still works."""
        config = TenantAIConfig.from_tenant(
            {"ai_config": {"settings": {"temperature": 9}}, "gemini_api_key": "legacy-key"}
        )

        assert config is not None
        assert config.api_key == "legacy-key"

    def test_wrong_typed_ai_config_raises(self):
        """A str in the ai_config position is a caller mistake, and still raises here."""
        with pytest.raises(TypeError, match="str"):
            TenantAIConfig.from_tenant({"ai_config": "AIzaSy...", "gemini_api_key": None})

    def test_missing_fields_read_as_not_configured(self):
        """A partial projection that omits both fields degrades to None, not KeyError."""
        assert TenantAIConfig.from_tenant({"tenant_id": "t1"}) is None

    def test_none_tenant_returns_none(self):
        assert TenantAIConfig.from_tenant(None) is None

    def test_orm_tenant_row_shape(self):
        """The ORM Tenant row has no .get() — from_tenant must read it by attribute."""
        from src.core.database.models import Tenant

        tenant = Tenant(
            tenant_id="t1",
            name="T",
            subdomain="t1",
            ai_config={"provider": "openai", "api_key": "orm-key"},
        )
        assert not hasattr(tenant, "get"), "the ORM row's access mechanism is attributes, not .get()"

        config = TenantAIConfig.from_tenant(tenant)

        assert config is not None
        assert config.provider == "openai"
        assert config.api_key == "orm-key"

    def test_orm_tenant_row_legacy_key_is_decrypted(self):
        """The legacy path reads the ORM property, so it gets the decrypted key."""
        from cryptography.fernet import Fernet

        from src.core.database.models import Tenant

        with patch.dict(os.environ, {"ENCRYPTION_KEY": Fernet.generate_key().decode()}, clear=False):
            tenant = Tenant(tenant_id="t1", name="T", subdomain="t1")
            tenant.gemini_api_key = "legacy-secret"

            config = TenantAIConfig.from_tenant(tenant)

            assert config is not None
            assert config.api_key == "legacy-secret", "must read the decrypting property, not the raw column"
            assert config.provider == CANONICAL_GOOGLE_PROVIDER

    def test_tenant_context_shape(self):
        """TenantContext is the projection the real production chain produces."""
        from src.core.tenant_context import TenantContext

        tenant = TenantContext(tenant_id="t1", ai_config={"provider": "groq", "api_key": "ctx-key"})

        config = TenantAIConfig.from_tenant(tenant)

        assert config is not None
        assert config.provider == "groq"
        assert config.api_key == "ctx-key"

    def test_tenant_context_legacy_key_only(self):
        """TenantContext carrying only the legacy key resolves through the fallback."""
        from src.core.tenant_context import TenantContext

        config = TenantAIConfig.from_tenant(TenantContext(tenant_id="t1", gemini_api_key="legacy-key"))

        assert config is not None
        assert config.provider == CANONICAL_GOOGLE_PROVIDER
        assert config.api_key == "legacy-key"

    def test_result_drives_the_factory(self):
        """The returned config is what the factory consumes — no dict round-trip needed."""
        config = TenantAIConfig.from_tenant({"gemini_api_key": "legacy-key"})

        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()
            assert factory.is_ai_enabled(config) is True
            assert factory.get_effective_config(config)["provider"] == CANONICAL_GOOGLE_PROVIDER

    def test_tenant_config_wins_while_a_platform_key_is_present(self):
        """Anti-vacuity guard: the tenant's config must win over a set platform key.

        A platform GEMINI_API_KEY makes is_ai_enabled() return True on its own, so a
        test that only asserts "AI was enabled" passes even when the tenant's own
        configuration was dropped on the floor. Pinning the factory's reported source
        to "tenant" is what actually fails if from_tenant returns None here.
        """
        tenant = {"ai_config": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "tenant-key"}}
        config = TenantAIConfig.from_tenant(tenant)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=True):
            effective = AIServiceFactory().get_effective_config(config)

        assert effective["source"] == "tenant"
        assert effective["provider"] == "anthropic"
        assert effective["model"] == "claude-sonnet-4-20250514"

    def test_legacy_key_wins_while_a_platform_key_is_present(self):
        """Same guard for the legacy column — the tenant's own key must not be shadowed."""
        config = TenantAIConfig.from_tenant({"gemini_api_key": "legacy-key"})

        assert config is not None
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=True):
            assert AIServiceFactory().get_effective_config(config)["source"] == "tenant"
        assert config.api_key == "legacy-key", "the tenant's own key, not the platform key"


class TestModelSettings:
    """Tests for ModelSettings model."""

    def test_temperature_bounds(self):
        """Temperature must be between 0 and 2."""
        with pytest.raises(ValueError):
            ModelSettings(temperature=-0.1)
        with pytest.raises(ValueError):
            ModelSettings(temperature=2.1)

        # Valid values
        ModelSettings(temperature=0)
        ModelSettings(temperature=2)
        ModelSettings(temperature=1.5)

    def test_timeout_positive(self):
        """Timeout must be positive."""
        with pytest.raises(ValueError):
            ModelSettings(timeout=0)
        with pytest.raises(ValueError):
            ModelSettings(timeout=-1)


class TestUsesLegacyGeminiApiKey:
    """Admin legacy gemini_api_key migration applies to all Google provider aliases."""

    @pytest.mark.parametrize("provider", sorted(GOOGLE_PROVIDER_ALIASES))
    def test_applies_for_google_aliases(self, provider):
        assert uses_legacy_gemini_api_key(provider) is True

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "groq"])
    def test_does_not_apply_for_non_google_providers(self, provider):
        assert uses_legacy_gemini_api_key(provider) is False


class TestBuildModelString:
    """Tests for build_model_string function."""

    def test_gemini_provider(self):
        """Gemini maps to google prefix (google-gla deprecated in pydantic-ai 1.99.0)."""
        result = build_model_string("gemini", "gemini-2.0-flash")
        assert result == "google:gemini-2.0-flash"

    def test_google_gla_provider(self):
        """Legacy google-gla alias maps to pydantic-ai canonical google prefix."""
        result = build_model_string("google-gla", "gemini-2.0-flash")
        assert result == "google:gemini-2.0-flash"

    def test_openai_provider(self):
        """OpenAI uses openai prefix."""
        result = build_model_string("openai", "gpt-4o")
        assert result == "openai:gpt-4o"

    def test_anthropic_provider(self):
        """Anthropic uses anthropic prefix."""
        result = build_model_string("anthropic", "claude-sonnet-4-20250514")
        assert result == "anthropic:claude-sonnet-4-20250514"


class TestGetPlatformDefaults:
    """Tests for get_platform_defaults function."""

    def test_defaults_from_env(self):
        """Platform defaults come from environment variables."""
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "openai",
                "PYDANTIC_AI_MODEL": "gpt-4o",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        ):
            defaults = get_platform_defaults()
            assert defaults["provider"] == "openai"
            assert defaults["model"] == "gpt-4o"
            assert defaults["api_key"] == "test-key"

    def test_defaults_fallback(self):
        """Defaults fall back to gemini when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            defaults = get_platform_defaults()
            assert defaults["provider"] == "gemini"
            assert defaults["model"] == "gemini-2.0-flash"

    def test_google_canonical_provider_resolves_gemini_api_key(self):
        """PYDANTIC_AI_PROVIDER=google must resolve GEMINI_API_KEY, not return None.

        Regression guard: before this fix, _get_provider_api_key('google') returned
        None (missing from the mapping), so get_platform_defaults() produced
        api_key=None and AIServiceFactory.is_ai_enabled() returned False even when
        GEMINI_API_KEY was set.
        """
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "google",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=False,
        ):
            defaults = get_platform_defaults()
            assert defaults["api_key"] == "test-gemini-key", (
                "provider='google' must map to GEMINI_API_KEY; api_key=None would make is_ai_enabled() return False"
            )

    def test_model_default_is_not_borrowed_across_vendors(self):
        """PYDANTIC_AI_PROVIDER without PYDANTIC_AI_MODEL must not default to Google's model.

        "gemini-2.0-flash" was the unconditional default, so an operator who set only
        PYDANTIC_AI_PROVIDER=anthropic got a platform default pair that cannot work.
        None is the honest answer: we do not know a model for that vendor.
        """
        with patch.dict(os.environ, {"PYDANTIC_AI_PROVIDER": "anthropic"}, clear=True):
            defaults = get_platform_defaults()
            assert defaults["provider"] == "anthropic"
            assert defaults["model"] is None

        with patch.dict(os.environ, {"PYDANTIC_AI_PROVIDER": "google-gla"}, clear=True):
            assert get_platform_defaults()["model"] == DEFAULT_GOOGLE_MODEL

    def test_google_gla_provider_resolves_gemini_api_key(self):
        """PYDANTIC_AI_PROVIDER=google-gla (legacy DB value) also resolves GEMINI_API_KEY."""
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "google-gla",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=False,
        ):
            defaults = get_platform_defaults()
            assert defaults["api_key"] == "test-gemini-key"


class TestAIServiceFactory:
    """Tests for AIServiceFactory class."""

    def test_create_model_with_platform_defaults(self):
        """Factory creates model using platform defaults."""
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            model = factory.create_model()
            # Now returns a Model instance instead of a string
            assert isinstance(model, GoogleModel)

    def test_create_model_with_google_canonical_provider(self):
        """provider='google' (pydantic-ai 1.99.0 canonical) injects API key, not env fallback.

        Regression guard: factory must normalize 'google' -> GoogleModel with explicit
        GoogleProvider(api_key=...), not fall through to the string-return else-branch
        which would silently bypass tenant API key injection.
        """
        from pydantic_ai.models.google import GoogleModel

        factory = AIServiceFactory()
        tenant_config = {
            "provider": "google",
            "model": "gemini-2.0-flash",
            "api_key": "tenant-specific-key",
        }
        model = factory.create_model(tenant_ai_config=tenant_config)
        assert isinstance(model, GoogleModel), (
            "provider='google' must return a GoogleModel, not a plain string; "
            "returning a string bypasses API key injection and leaks auth to env vars"
        )

    def test_tenant_google_uses_platform_api_key_when_tenant_key_missing(self):
        """Tenant provider='google' without api_key inherits platform GEMINI_API_KEY."""
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-gemini-key"}, clear=True):
            factory = AIServiceFactory()
            model = factory.create_model(
                tenant_ai_config={"provider": "google", "model": "gemini-2.0-flash"},
            )
            assert isinstance(model, GoogleModel)

    def test_create_model_raises_when_google_has_no_resolved_api_key(self):
        """No tenant or platform key must raise instead of pydantic-ai env fallback."""
        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()
            from src.core.exceptions import AdCPConfigurationError

            with pytest.raises(AdCPConfigurationError, match="No API key available"):
                factory.create_model(
                    tenant_ai_config={"provider": "google", "model": "gemini-2.0-flash"},
                )

    def test_create_model_with_tenant_config(self):
        """Factory uses tenant config over platform defaults."""
        from pydantic_ai.models.anthropic import AnthropicModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "platform-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            tenant_config = {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": "tenant-key",
            }
            model = factory.create_model(tenant_ai_config=tenant_config)
            # Returns AnthropicModel for anthropic provider
            assert isinstance(model, AnthropicModel)

    def test_create_model_with_override(self):
        """Explicit overrides take highest priority — and do not import the platform key.

        OPENAI_API_KEY is set here because the platform (gemini) key is no longer lent
        to the overridden provider; the assertion on the credential is what makes that
        visible instead of leaving the model's authentication unexamined.
        """
        from pydantic_ai.models.openai import OpenAIChatModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "platform-gemini-secret",
                "OPENAI_API_KEY": "platform-openai-key",
            },
            clear=True,
        ):
            factory = AIServiceFactory()
            tenant_config = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
            model = factory.create_model(
                tenant_ai_config=tenant_config,
                provider_override="openai",
                model_override="gpt-4o",
            )
            # Override to openai returns OpenAIChatModel
            assert isinstance(model, OpenAIChatModel)
            assert model.model_name == "gpt-4o"
            assert credential_of(model) != "platform-gemini-secret"

    def test_get_effective_config_platform(self):
        """Effective config shows platform source when no tenant config."""
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            effective = factory.get_effective_config()
            assert effective["provider"] == "gemini"
            assert effective["model"] == "gemini-2.0-flash"
            assert effective["has_api_key"] is True
            assert effective["source"] == "platform"

    def test_get_effective_config_tenant(self):
        """Effective config shows tenant source when tenant config provided."""
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "platform-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            tenant_config = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
            effective = factory.get_effective_config(tenant_ai_config=tenant_config)
            assert effective["provider"] == "anthropic"
            assert effective["model"] == "claude-sonnet-4-20250514"
            assert effective["source"] == "tenant"

    def test_malformed_tenant_config_does_not_raise_out_of_is_ai_enabled(self):
        """A malformed ai_config row must not surface as an exception to the caller.

        Regression guard for the get_products ranking path: is_ai_enabled() raised
        pydantic ValidationError (a ValueError) from inside the ranking try-block,
        which the `except (ImportError, RuntimeError, OSError)` tuple there does not
        catch, so every transport reported VALIDATION_ERROR/correctable to the buyer.
        """
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=False):
            factory = AIServiceFactory()
            assert factory.is_ai_enabled({"settings": {"temperature": 9}}) is True

    def test_non_dict_tenant_config_does_not_raise_out_of_is_ai_enabled(self):
        """A list where a dict was expected used to die with AttributeError."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=False):
            factory = AIServiceFactory()
            assert factory.is_ai_enabled(["provider", "gemini"]) is True

    def test_malformed_tenant_config_degrades_to_a_COHERENT_platform_configuration(self):
        """A malformed row degrades to the platform's own provider+model+key, as one set.

        This test used to assert only `isinstance(model, GoogleModel)` for a row naming
        provider "openai", which read as "the tenant's vendor may be swapped" and said
        nothing about which secret went with it. The contract is narrower: an unparseable
        row contributes NOTHING, so all three fields come from the platform and they
        match each other. The buyer-facing paths never reach this state at all —
        `TenantAIConfig.from_tenant` returns None for a malformed row (asserted in
        TestTenantAIConfigFromTenant), so this is the direct-caller fallback only.
        """
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "platform-key",
            },
            clear=True,
        ):
            factory = AIServiceFactory()
            model = factory.create_model(tenant_ai_config={"provider": "openai", "settings": {"timeout": -1}})

            assert isinstance(model, GoogleModel)
            assert model.model_name == "gemini-2.0-flash"
            assert credential_of(model) == "platform-key"

    def test_platform_key_is_never_lent_to_another_vendor(self):
        """The platform's Google secret must not travel to api.anthropic.com.

        `create_model` resolved provider and api_key from independent expressions, so a
        tenant row naming "anthropic" produced an AnthropicModel whose client carried
        the platform's GEMINI_API_KEY. `get_products` is auth-OPTIONAL discovery, so an
        anonymous buyer's brief was enough to trigger it.

        ANTHROPIC_API_KEY is present so the model still builds: the assertion is about
        WHICH credential it carries, and a test where no model can be built could pass
        for the wrong reason.
        """
        from pydantic_ai.models.anthropic import AnthropicModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "PLATFORM-GEMINI-SECRET",
                "ANTHROPIC_API_KEY": "PLATFORM-ANTHROPIC-KEY",
            },
            clear=True,
        ):
            factory = AIServiceFactory()
            model = factory.create_model(
                tenant_ai_config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
            )

            assert isinstance(model, AnthropicModel)
            assert credential_of(model) != "PLATFORM-GEMINI-SECRET", "the platform's Google key reached Anthropic"

    def test_ai_is_disabled_when_the_platform_key_belongs_to_another_vendor(self):
        """A tenant provider with no credential of its own is NOT AI-enabled.

        `is_ai_enabled` counted any key from any source, so it green-lit a request that
        could only run by borrowing the wrong vendor's secret. Consumers gate on this,
        so False here is what makes them skip AI (and, in get_products, emit the
        unranked advisory) instead of making the call.
        """
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "PLATFORM-GEMINI-SECRET",
            },
            clear=True,
        ):
            factory = AIServiceFactory()

            assert factory.is_ai_enabled({"provider": "anthropic", "model": "claude-sonnet-4-20250514"}) is False
            # …and the same answer is reported to the admin UI and to PolicyCheckService,
            # which gates on has_api_key.
            effective = factory.get_effective_config({"provider": "anthropic", "model": "claude-sonnet-4-20250514"})
            assert effective["has_api_key"] is False
            # The tenant's own key is still enough on its own.
            assert factory.is_ai_enabled({"provider": "anthropic", "model": "claude-x", "api_key": "sk-ant"}) is True

    def test_missing_model_for_a_foreign_provider_disables_ai_and_raises_if_forced(self):
        """A model name from another vendor is not a model for this one.

        The Admin UI writes model="" when the seller leaves the field blank, so
        {"provider": "anthropic", "api_key": …} with no model is a real stored shape.
        Filling it from PYDANTIC_AI_MODEL handed "gemini-2.0-flash" to an Anthropic
        client — every call 404s, and in the ranking path that 404 is a RuntimeError
        that comes back to the buyer as a silently unranked list.
        """
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "PLATFORM-GEMINI-SECRET",
            },
            clear=True,
        ):
            factory = AIServiceFactory()
            keyed_but_modelless = {"provider": "anthropic", "api_key": "sk-ant-tenant"}

            assert factory.is_ai_enabled(keyed_but_modelless) is False
            assert factory.get_effective_config(keyed_but_modelless)["model"] is None

            from src.core.exceptions import AdCPConfigurationError

            with pytest.raises(AdCPConfigurationError, match="No model configured"):
                factory.create_model(tenant_ai_config=keyed_but_modelless)

    def test_legacy_google_config_runs_on_google_under_an_anthropic_platform(self):
        """End to end for the legacy column: provider, model and key are all Google.

        With PYDANTIC_AI_PROVIDER=anthropic the platform model names a Claude release.
        Before the pin, `from_tenant`'s legacy config carried provider="google" and no
        model, so the factory paired that Claude name with a Google client built on the
        tenant's Gemini key: a model that 404s on every request.
        """
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "anthropic",
                "PYDANTIC_AI_MODEL": "claude-sonnet-4-20250514",
                "ANTHROPIC_API_KEY": "PLATFORM-ANTHROPIC-KEY",
            },
            clear=True,
        ):
            config = TenantAIConfig.from_tenant({"tenant_id": "t1", "gemini_api_key": "AIza-TENANT"})
            factory = AIServiceFactory()

            assert factory.is_ai_enabled(config) is True
            model = factory.create_model(config)

            assert isinstance(model, GoogleModel)
            assert model.model_name == DEFAULT_GOOGLE_MODEL
            assert credential_of(model) == "AIza-TENANT"

    def test_unsupported_provider_refuses_to_drop_a_configured_key(self):
        """The string-fallback branch cannot inject a key, so it must not pretend to.

        Returning "gateway/anthropic:model" leaves pydantic-ai to authenticate with
        whatever it finds in the environment — a different credential than the one the
        seller configured. No quiet failures: say so.
        """
        from src.core.exceptions import AdCPConfigurationError

        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=True):
            factory = AIServiceFactory()

            with pytest.raises(AdCPConfigurationError, match="cannot be used"):
                factory.create_model(
                    tenant_ai_config={"provider": "gateway/anthropic", "model": "claude-x", "api_key": "sk-tenant"}
                )

            # With no key to drop, the string fallback still works.
            assert factory.create_model(tenant_ai_config={"provider": "gateway/anthropic", "model": "claude-x"}) == (
                "gateway/anthropic:claude-x"
            )

    def test_model_receives_api_key_via_provider(self):
        """Factory passes API key directly via Provider, not environment variables."""
        from pydantic_ai.models.openai import OpenAIChatModel

        # Clear the environment to prove we're not relying on env vars
        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()
            tenant_config = {"provider": "openai", "model": "gpt-4o", "api_key": "tenant-openai-key"}
            model = factory.create_model(tenant_ai_config=tenant_config)
            # Model is created successfully
            assert isinstance(model, OpenAIChatModel)
            # API key is NOT set in environment (we pass it directly to Provider)
            assert os.environ.get("OPENAI_API_KEY") is None
