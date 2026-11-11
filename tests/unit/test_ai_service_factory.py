"""Tests for AI service factory and configuration."""

import logging
import os
from unittest.mock import patch

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError, UserError

from src.core.config import load_settings
from src.services.ai import (
    AIServiceFactory,
    ModelSettings,
    TenantAIConfig,
    get_platform_defaults,
)
from src.services.ai.config import (
    CANONICAL_GOOGLE_PROVIDER,
    DEFAULT_GOOGLE_MODEL,
    GOOGLE_PROVIDER_ALIASES,
    UNUSABLE_AI_CONFIG,
    uses_legacy_gemini_api_key,
)
from src.services.ai.factory import PROVIDER_MODELS, AIProviderError, ResolvedAIConfig, run_agent
from tests.helpers.settings_injection import PlatformAICredential


def credential_of(model) -> str | None:
    """The API key the built model will actually authenticate with.

    Reaches through the pydantic-ai model to the vendor client because that is the only
    place the answer exists: `build` returns a Model, and which SECRET it carries
    is exactly the property under test. Anthropic/OpenAI/Groq clients expose `api_key`
    directly; the Google client keeps it on its inner `_api_client`, Mistral on its SDK
    configuration and Cohere on its client wrapper.
    """
    client = model.client
    if hasattr(client, "api_key"):
        return client.api_key
    if hasattr(client, "sdk_configuration"):  # Mistral
        return client.sdk_configuration.security.api_key
    if hasattr(client, "_client_wrapper"):  # Cohere
        return client._client_wrapper._token
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


class TestTenantAIConfigFromTenantParse:
    """How from_tenant parses the stored ai_config row: the one parse of that column."""

    def test_well_formed_row_is_parsed(self, tenant_row):
        """A well-formed row with a key parses into the seller's own config."""
        config = TenantAIConfig.from_tenant(
            tenant_row(ai_config={"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "k"})
        )
        assert config == TenantAIConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="k")

    def test_malformed_settings_are_unusable_with_one_warning(self, tenant_row, caplog):
        """A settings block that violates ModelSettings bounds must NOT raise.

        Regression guard: TenantAIConfig.model_validate raises pydantic's
        ValidationError, which subclasses ValueError, so a malformed seller-side
        ai_config row reached the buyer as a VALIDATION_ERROR/correctable envelope
        naming 'settings.temperature' — a field that does not exist in the request
        schema. A broken config row must switch the AI feature off, not fail the request.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            config = TenantAIConfig.from_tenant(
                tenant_row(ai_config={"provider": "gemini", "api_key": "k", "settings": {"temperature": 9}})
            )

        assert config is UNUSABLE_AI_CONFIG
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one WARNING, got {[r.message for r in warnings]}"

    def test_list_from_the_jsonb_column_is_unusable_instead_of_raising(self, tenant_row, caplog):
        """A list is a shape the JSONB column can hold, so it is malformed DATA: unusable, logged once.

        The factory's old `elif tenant_ai_config:` branch bound such a value straight to
        `config`, and the next line (`config.api_key`) died with AttributeError.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            config = TenantAIConfig.from_tenant(tenant_row(ai_config=["provider", "gemini"]))

        assert config is UNUSABLE_AI_CONFIG
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    @pytest.mark.parametrize("value", ["gemini", 42, object()])
    def test_a_value_the_column_cannot_hold_raises_type_error(self, tenant_row, value, caplog):
        """A str/int/object cannot come out of the JSONB column, so it RAISES rather than being logged.

        `src/core/database/json_type.py` raises TypeError itself for anything that is not
        a dict or list. Logging such a value as malformed seller data would hide whatever
        put it on the row; `code-patterns.md` names that shape as wrong.

        The message names the offending type and the tenant's ai_config, because the
        traceback is the only clue.
        """
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            with pytest.raises(TypeError) as excinfo:
                TenantAIConfig.from_tenant(tenant_row(ai_config=value))

        assert type(value).__name__ in str(excinfo.value)
        assert "ai_config for tenant 't1'" in str(excinfo.value)
        assert [r.message for r in caplog.records if r.levelno == logging.WARNING] == [], (
            "a value the column cannot hold is raised, not logged-and-swallowed"
        )


class TestTenantAIConfigFromTenant:
    """TenantAIConfig.from_tenant owns 'given a tenant row, what AI did the seller configure?'."""

    def test_ai_config_wins_over_legacy_key(self, tenant_row):
        """A tenant's own ai_config supersedes the legacy gemini_api_key column."""
        config = TenantAIConfig.from_tenant(
            tenant_row(ai_config={"provider": "anthropic", "api_key": "tenant-key"}, gemini_api_key="legacy-key")
        )

        assert config == TenantAIConfig(provider="anthropic", api_key="tenant-key")

    def test_legacy_key_alone_becomes_canonical_google_config(self, tenant_row):
        """With no ai_config, the legacy key is surfaced as a Google-provider config.

        The provider is CANONICAL_GOOGLE_PROVIDER rather than the literal "gemini":
        every consumer canonicalizes the string before comparing or using it, so the
        two spellings are interchangeable downstream.

        The MODEL travels with the provider. Left None, the factory filled it from the
        platform's PYDANTIC_AI_MODEL, which names whatever vendor the DEPLOYMENT runs —
        so a deployment set to Anthropic produced a Google client asking for
        "claude-sonnet-4-…" on this tenant's Gemini key, and every call 404'd.
        """
        config = TenantAIConfig.from_tenant(tenant_row(ai_config=None, gemini_api_key="legacy-key"))

        assert config == TenantAIConfig(
            provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key="legacy-key"
        )

    def test_legacy_key_is_read_through_the_decrypting_property(self, tenant_row):
        """The legacy path reads the ORM property, so it gets the decrypted key."""
        tenant = tenant_row()
        tenant.gemini_api_key = "legacy-secret"

        config = TenantAIConfig.from_tenant(tenant)

        assert isinstance(config, TenantAIConfig)
        assert config.api_key == "legacy-secret", "must read the decrypting property, not the raw column"

    def test_provider_without_a_key_is_unusable_not_nothing(self, tenant_row):
        """A row naming a provider with no api_key is UNUSABLE: AI is off for this seller.

        `src/admin/blueprints/settings.py` writes exactly {"provider": …, "model": …} when
        the seller submits the AI form with the key field blank, and flashes "no API key
        configured. AI features will be disabled." Answering None ("configured nothing")
        let ranking and naming fall back to the OPERATOR's platform key for a seller who
        had just been told AI was off.

        The platform key is set on purpose: the answer is about the seller's choice, not
        about whether a key exists anywhere.
        """
        with PlatformAICredential("platform-key"):
            config = TenantAIConfig.from_tenant(tenant_row(ai_config={"provider": "anthropic", "model": "claude-x"}))

        assert config is UNUSABLE_AI_CONFIG

    def test_keyless_google_row_takes_the_legacy_key_of_the_same_vendor(self, tenant_row):
        """A keyless row naming Google does not shadow a legacy Gemini key, which is Google's."""
        config = TenantAIConfig.from_tenant(
            tenant_row(ai_config={"provider": "google", "model": "gemini-2.0-flash"}, gemini_api_key="legacy-key")
        )

        assert config == TenantAIConfig(
            provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key="legacy-key"
        )

    def test_keyless_foreign_row_does_not_borrow_the_legacy_google_key(self, tenant_row):
        """A seller who chose Anthropic and left the key blank is not switched to Google."""
        config = TenantAIConfig.from_tenant(
            tenant_row(ai_config={"provider": "anthropic", "model": "claude-x"}, gemini_api_key="legacy-key")
        )

        assert config is UNUSABLE_AI_CONFIG

    def test_both_absent_is_nothing_configured(self, tenant_row):
        """Nothing configured means None — from_tenant NEVER reaches for the platform key."""
        with PlatformAICredential("platform-key"):
            assert TenantAIConfig.from_tenant(tenant_row(ai_config=None)) is None

    @pytest.mark.parametrize("ai_config", [{}, {"settings": {"temperature": 0.5}}], ids=["empty", "settings-only"])
    def test_a_row_naming_no_model_falls_through_to_the_legacy_key(self, tenant_row, ai_config):
        """A row with no provider and no key configured no model, so it does not shadow the legacy column."""
        config = TenantAIConfig.from_tenant(tenant_row(ai_config=ai_config, gemini_api_key="legacy-key"))

        assert isinstance(config, TenantAIConfig)
        assert config.api_key == "legacy-key"

    @pytest.mark.parametrize("ai_config", [{}, {"settings": {"temperature": 0.5}}], ids=["empty", "settings-only"])
    def test_a_row_naming_no_model_and_no_legacy_key_is_nothing(self, tenant_row, ai_config):
        assert TenantAIConfig.from_tenant(tenant_row(ai_config=ai_config)) is None

    def test_malformed_ai_config_is_unusable(self, tenant_row, caplog):
        """A row that does not parse is UNUSABLE — never a config the factory refills, never nothing.

        The api_key in the row is valid; only `settings.temperature` is out of bounds.
        Answering None let ranking fall back to the platform's Google key, so the seller
        who had deliberately chosen Anthropic had the buyer's brief and product catalog
        sent to Google. One WARNING naming the tenant is how the seller finds out.
        """
        malformed = {"provider": "anthropic", "api_key": "sk-ant-real", "settings": {"temperature": 9}}

        with PlatformAICredential("platform-key"):
            with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
                config = TenantAIConfig.from_tenant(tenant_row(ai_config=malformed, gemini_api_key="legacy-key"))

        assert config is UNUSABLE_AI_CONFIG
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one WARNING, got {[r.getMessage() for r in warnings]}"
        assert "t1" in warnings[0].getMessage(), "the WARNING must name the tenant whose row is broken"

    def test_malformed_row_does_not_log_its_key(self, tenant_row, caplog):
        """A ValidationError renders the value it rejected; the row's key must not reach the log."""
        with caplog.at_level(logging.WARNING, logger="src.services.ai.config"):
            config = TenantAIConfig.from_tenant(
                tenant_row(ai_config={"provider": "anthropic", "api_key": ["sk-ant-SECRET"]})
            )

        assert config is UNUSABLE_AI_CONFIG
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "api_key" in logged, "the WARNING still names the field that failed"
        assert "sk-ant-SECRET" not in logged

    def test_tenant_context_carries_the_resolution(self, tenant_row):
        """TenantContext.from_orm_model resolves once; the context holds the answer, not the column."""
        from src.core.tenant_context import TenantContext

        context = TenantContext.from_orm_model(
            tenant_row(
                virtual_host="t1.example.com",
                account_sandbox=False,
                ai_config={"provider": "groq", "api_key": "ctx-key"},
            )
        )

        assert context.ai_config == TenantAIConfig(provider="groq", api_key="ctx-key")
        assert "ctx-key" not in repr(context)
        assert not hasattr(context, "gemini_api_key"), "the legacy credential is not carried on the context"

    def test_tenant_config_wins_while_a_platform_key_is_present(self, tenant_row):
        """Anti-vacuity guard: the tenant's config must win over a set platform key.

        A platform GEMINI_API_KEY makes the factory build a model on its own, so a test
        that only asserts "AI was enabled" passes even when the tenant's own
        configuration was dropped on the floor. Pinning the resolution is what fails if
        from_tenant returns None here.
        """
        config = TenantAIConfig.from_tenant(
            tenant_row(
                ai_config={"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "tenant-key"}
            )
        )

        with PlatformAICredential("platform-key"):
            resolved = AIServiceFactory().resolve(config)

        assert resolved == ResolvedAIConfig(
            provider="anthropic", model="claude-sonnet-4-20250514", api_key="tenant-key"
        )

    def test_legacy_key_wins_while_a_platform_key_is_present(self, tenant_row):
        """Same guard for the legacy column — the tenant's own key must not be shadowed."""
        config = TenantAIConfig.from_tenant(tenant_row(gemini_api_key="legacy-key"))

        with PlatformAICredential("platform-key"):
            resolved = AIServiceFactory().resolve(config)

        assert resolved == ResolvedAIConfig(
            provider=CANONICAL_GOOGLE_PROVIDER, model=DEFAULT_GOOGLE_MODEL, api_key="legacy-key"
        )


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
        api_key=None and the factory built no model even when
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
                "provider='google' must map to GEMINI_API_KEY; api_key=None would make the factory build no model"
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
            load_settings()  # the settings are cached; this block changed the environment
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
    """AIServiceFactory.build is the one call: the model a configuration runs on, or None."""

    def test_build_with_platform_defaults(self):
        """Configured nothing builds on the platform defaults."""
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(
            os.environ,
            {"PYDANTIC_AI_PROVIDER": "gemini", "PYDANTIC_AI_MODEL": "gemini-2.0-flash", "GEMINI_API_KEY": "test-key"},
            clear=False,
        ):
            model = AIServiceFactory().build()

        assert isinstance(model, GoogleModel)
        assert credential_of(model) == "test-key"

    def test_build_with_google_canonical_provider(self):
        """provider='google' (pydantic-ai 1.99.0 canonical) builds a GoogleModel on the tenant's key."""
        from pydantic_ai.models.google import GoogleModel

        model = AIServiceFactory().build(
            TenantAIConfig(provider="google", model="gemini-2.0-flash", api_key="tenant-specific-key")
        )

        assert isinstance(model, GoogleModel)
        assert credential_of(model) == "tenant-specific-key"

    def test_tenant_google_uses_platform_api_key_when_tenant_key_missing(self):
        """A Google config with no key of its own takes the platform's Google key: same vendor."""
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-gemini-key"}, clear=True):
            model = AIServiceFactory().build(TenantAIConfig(provider="google", model="gemini-2.0-flash"))

        assert isinstance(model, GoogleModel)
        assert credential_of(model) == "platform-gemini-key"

    def test_no_resolved_api_key_builds_nothing(self):
        """No tenant or platform key builds no model, rather than leaving pydantic-ai to find one."""
        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()

            assert factory.build(TenantAIConfig(provider="google", model="gemini-2.0-flash")) is None
            assert factory.build() is None

    def test_build_with_tenant_config(self):
        """The tenant's config wins over the platform defaults."""
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
            model = AIServiceFactory().build(
                TenantAIConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="tenant-key")
            )

        assert isinstance(model, AnthropicModel)
        assert credential_of(model) == "tenant-key"

    def test_resolve_on_the_platform_defaults(self):
        """What `build` would run on with nothing configured: the platform's own provider, model and key."""
        with patch.dict(
            os.environ,
            {"PYDANTIC_AI_PROVIDER": "gemini", "PYDANTIC_AI_MODEL": "gemini-2.0-flash", "GEMINI_API_KEY": "test-key"},
            clear=False,
        ):
            resolved = AIServiceFactory().resolve()

        assert resolved == ResolvedAIConfig(provider="google", model="gemini-2.0-flash", api_key="test-key")

    def test_unusable_seller_config_builds_nothing_with_a_platform_key_present(self):
        """The seller configured AI that cannot run: the platform's credential is no substitute.

        This replaces a test that asserted the opposite (a malformed row degrading to the
        platform's provider, model and key), which is the path that sent a seller's buyer
        brief to the operator's Google key after the seller had chosen Anthropic.
        """
        with PlatformAICredential("platform-key"):
            factory = AIServiceFactory()

            assert factory.resolve(UNUSABLE_AI_CONFIG) is None
            assert factory.build(UNUSABLE_AI_CONFIG) is None
            assert factory.build(None) is not None, "the platform key alone does build: the refusal is UNUSABLE's"

    def test_platform_fallback_off_builds_only_the_sellers_own(self):
        """The policy check's rule: no seller configuration, no model, whatever the platform has."""
        with PlatformAICredential("platform-key"):
            factory = AIServiceFactory()

            assert factory.build(None, platform_fallback=False) is None
            own = TenantAIConfig(provider="google", model="gemini-2.0-flash", api_key="seller-key")
            assert credential_of(factory.build(own, platform_fallback=False)) == "seller-key"

    def test_platform_key_is_never_lent_to_another_vendor(self):
        """The platform's Google secret must not travel to api.anthropic.com.

        Provider and api_key were once resolved from independent expressions, so a tenant
        row naming "anthropic" produced an AnthropicModel whose client carried the
        platform's GEMINI_API_KEY. `get_products` is auth-OPTIONAL discovery, so an
        anonymous buyer's brief was enough to trigger it. With no Anthropic key of the
        tenant's own, nothing is built at all.
        """
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
            tenant = TenantAIConfig(provider="anthropic", model="claude-sonnet-4-20250514")

            assert factory.resolve(tenant) is None
            assert factory.build(tenant) is None
            # The tenant's own key is enough on its own.
            assert credential_of(factory.build(tenant.model_copy(update={"api_key": "sk-ant"}))) == "sk-ant"

    def test_missing_model_for_a_foreign_provider_builds_nothing(self):
        """A model name from another vendor is not a model for this one.

        The Admin UI writes model="" when the seller leaves the field blank, so
        {"provider": "anthropic", "api_key": …} with no model is a real stored shape.
        Filling it from PYDANTIC_AI_MODEL handed "gemini-2.0-flash" to an Anthropic
        client, and every call 404'd.
        """
        with patch.dict(
            os.environ,
            {"PYDANTIC_AI_PROVIDER": "gemini", "PYDANTIC_AI_MODEL": "gemini-2.0-flash", "GEMINI_API_KEY": "SECRET"},
            clear=True,
        ):
            factory = AIServiceFactory()
            keyed_but_modelless = TenantAIConfig(provider="anthropic", api_key="sk-ant-tenant")

            assert factory.resolve(keyed_but_modelless) is None
            assert factory.build(keyed_but_modelless) is None

    def test_legacy_google_config_runs_on_google_under_an_anthropic_platform(self, tenant_row):
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
            clear=False,
        ):
            model = AIServiceFactory().build(TenantAIConfig.from_tenant(tenant_row(gemini_api_key="AIza-TENANT")))

        assert isinstance(model, GoogleModel)
        assert model.model_name == DEFAULT_GOOGLE_MODEL
        assert credential_of(model) == "AIza-TENANT"

    def test_the_table_names_the_providers_this_factory_builds(self):
        """Membership, pinned: dropping an entry turns a seller's configuration into "no AI".

        Before the table, a set of explicit-key providers stood beside the ``elif`` chain
        that built them, and nothing compared the two. One statement now decides both what
        resolves and what builds; this is the check that it still says what it said.
        """
        assert set(PROVIDER_MODELS) == {"google", "anthropic", "openai", "groq", "mistral", "cohere"}

    @pytest.mark.parametrize("provider", sorted(PROVIDER_MODELS))
    def test_every_provider_in_the_table_builds_its_model_on_the_configured_key(self, provider):
        """The table is what can be built: each entry builds the class it names, on the seller's key.

        `resolve` admits exactly the table's keys, so an entry whose classes no longer
        resolve, or that drops the key, fails here rather than on a live request.
        """
        import pkgutil

        config = TenantAIConfig(provider=provider, model="model-x", api_key=f"key-{provider}")

        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()
            resolved = factory.resolve(config)
            model = factory.build(config)

        assert resolved == ResolvedAIConfig(provider=provider, model="model-x", api_key=f"key-{provider}")
        assert isinstance(model, pkgutil.resolve_name(PROVIDER_MODELS[provider][0]))
        assert model.model_name == "model-x"
        assert credential_of(model) == f"key-{provider}"

    @pytest.mark.parametrize("provider", ["gateway/anthropic", "bedrock", "vertex"])
    def test_a_provider_outside_the_table_builds_nothing(self, provider):
        """No explicit-key integration means nowhere to put the seller's key, so no model.

        Handing pydantic-ai a "provider:model" string instead let it authenticate with
        whatever it found in the environment — a different credential than the one the
        seller configured.
        """
        assert provider not in PROVIDER_MODELS
        with patch.dict(os.environ, {"GEMINI_API_KEY": "platform-key"}, clear=True):
            factory = AIServiceFactory()
            config = TenantAIConfig(provider=provider, model="claude-x", api_key="sk-tenant")

            assert factory.resolve(config) is None
            assert factory.build(config) is None

    def test_model_receives_api_key_via_provider(self):
        """Factory passes API key directly via Provider, not environment variables."""
        from pydantic_ai.models.openai import OpenAIChatModel

        with patch.dict(os.environ, {}, clear=True):
            model = AIServiceFactory().build(
                TenantAIConfig(provider="openai", model="gpt-4o", api_key="tenant-openai-key")
            )

            assert isinstance(model, OpenAIChatModel)
            assert os.environ.get("OPENAI_API_KEY") is None

    @pytest.mark.parametrize("refusal", [ImportError("the SDK is not installed"), UserError("unusable model")])
    def test_a_model_pydantic_ai_refuses_to_construct_builds_nothing(self, refusal):
        """A construction refusal is "AI unavailable" (None), the answer the caller already handles."""

        def refuse(*args, **kwargs):
            raise refusal

        with patch("src.services.ai.factory.pkgutil.resolve_name", return_value=refuse):
            assert AIServiceFactory().build(TenantAIConfig(provider="openai", model="gpt-4o", api_key="k")) is None

    def test_a_defect_while_constructing_propagates(self):
        def broken(*args, **kwargs):
            raise KeyError("defect")

        with patch("src.services.ai.factory.pkgutil.resolve_name", return_value=broken):
            with pytest.raises(KeyError, match="defect"):
                AIServiceFactory().build(TenantAIConfig(provider="openai", model="gpt-4o", api_key="k"))


def _agent_whose_model_raises(exc: Exception):
    """A real pydantic-ai agent whose model call raises *exc*, the way a vendor client would."""
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    def fail(messages, info):
        raise exc

    return Agent(FunctionModel(fail))


class TestRunAgent:
    """run_agent is the one place a provider failure is told apart from a defect."""

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(lambda: ModelHTTPError(status_code=503, model_name="m", body="down"), id="ModelHTTPError"),
            pytest.param(lambda: UserError("unusable model"), id="UserError"),
            pytest.param(lambda: httpx.ConnectError("unreachable"), id="httpx.ConnectError"),
            pytest.param(lambda: httpx.RemoteProtocolError("dropped"), id="httpx.RemoteProtocolError"),
        ],
    )
    async def test_a_provider_failure_raises_ai_provider_error(self, exc):
        cause = exc()

        with pytest.raises(AIProviderError) as excinfo:
            await run_agent(_agent_whose_model_raises(cause), "prompt")

        assert excinfo.value.__cause__ is cause

    @pytest.mark.parametrize("exc", [ValueError("defect"), KeyError("defect"), TypeError("defect")])
    async def test_any_other_exception_propagates_unchanged(self, exc):
        with pytest.raises(type(exc)) as excinfo:
            await run_agent(_agent_whose_model_raises(exc), "prompt")

        assert excinfo.value is exc
