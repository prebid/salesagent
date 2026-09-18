"""The environment, read once, as typed state.

This module is the ONE reader of the process environment. It is read when the application
is composed (``load_settings``), into a :class:`Settings` object whose fields and derived
properties are what the rest of the tree depends on. No module reads ``os.environ`` after
import, and no request-time code asks what environment it is in: it reads a named fact off
the settings, or it is handed the component the composition root selected for that fact.

Two shapes of fact live here:

- **Values**: a port, a domain, a key, a limit. Plain fields.
- **Allowances and selections**: whether loopback webhook targets are accepted, whether the
  debug routes exist, whether the creative registry serves the checked-in reference formats.
  These are named properties derived from the fields, so a call site reads the allowance it
  needs rather than the raw flag that implies it. ``ADCP_TESTING`` implies six different
  allowances; each has its own name, and the composition root uses them to pick components.

``load_settings`` rebuilds the object (a test that changes the environment calls it);
``get_settings`` returns the current one, building it on first use so a script that never
composed an app still gets a consistent view. Nothing builds it at import: a module that
needs one runtime fact while its classes are being defined (the request DTOs' extra mode)
reads :class:`RuntimeSettings` alone, so a bad credential fails where the app is composed,
not when a schema is imported.

An empty value is an unset value. CI and the compose files hand a process ``ADCP_TESTING=""``
or ``DB_PORT=""`` (``${VAR:-}``), and the helpers this module replaced read those as the
default; ``env_ignore_empty`` keeps that reading.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore", env_ignore_empty=True)


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


class RuntimeSettings(BaseSettings):
    """Where and how this process runs."""

    model_config = _ENV

    environment: str = "development"
    production: bool = False
    fly_app_name: str | None = None
    adcp_sales_port: int = 8080
    adcp_sales_host: str = "0.0.0.0"
    skip_nginx: bool = False
    skip_cron: bool = False
    allowed_origins: str = "http://localhost:8000"
    admin_ui_url: str = "http://localhost:8001"
    sales_agent_domain: str | None = None
    admin_domain: str | None = None
    super_admin_domain: str | None = None
    support_email: str = "support@example.com"
    adcp_agent_url: str | None = None
    adcp_multi_tenant: bool = False
    adcp_log_dir: Path = Path("logs")
    adcp_run_background_schedulers: bool = True
    flask_secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))
    flask_debug: bool = False
    admin_server_type: str = "waitress"

    @property
    def is_production(self) -> bool:
        """One answer for the three spellings a deployment used to set.

        ``PRODUCTION=true``, ``ENVIRONMENT=production`` and a Fly.io app name all meant
        "this is production" to some site and not to others; security-sensitive checks
        drifted on the difference. Any of the three is production.
        """
        return self.production or self.environment.lower() == "production" or bool(self.fly_app_name)

    @property
    def is_single_tenant(self) -> bool:
        return not self.adcp_multi_tenant

    @property
    def allowed_origin_list(self) -> list[str]:
        return _csv(self.allowed_origins)

    @property
    def admin_domain_or_derived(self) -> str | None:
        if self.admin_domain:
            return self.admin_domain
        return f"admin.{self.sales_agent_domain}" if self.sales_agent_domain else None

    def sales_agent_url(self, protocol: str = "https") -> str | None:
        return f"{protocol}://{self.sales_agent_domain}" if self.sales_agent_domain else None

    @property
    def local_base_url(self) -> str:
        """The URL this process answers on when no domain is configured."""
        return f"http://localhost:{self.adcp_sales_port}"

    @property
    def session_cookie_domain(self) -> str | None:
        return f".{self.sales_agent_domain}" if self.sales_agent_domain else None

    # --- production-derived selections; runtime facts only, so a module that needs one
    # while its classes are being defined can read this group without composing the rest.

    @property
    def structured_logging(self) -> bool:
        return self.is_production

    @property
    def verbose_auth_log(self) -> bool:
        return not self.is_production

    @property
    def pydantic_extra_mode(self) -> Literal["ignore", "forbid"]:
        """Production ignores undeclared request fields (a newer buyer is served); everywhere
        else they are a hard rejection (an unimplemented spec field is loud)."""
        return "ignore" if self.is_production else "forbid"


class TestingSettings(BaseSettings):
    """Settings that say A SUITE IS RUNNING. Every one of them is a defect.

    A production path that consults any of these serves a different seller under test than
    in deployment, which makes the suite's verdict conditional on the suite being what ran
    it (CLAUDE.md pattern 11). So this class is a WORK LIST, not a configuration surface:
    it may only lose fields, which ``tests/unit/test_testing_settings_only_shrinks.py``
    pins. Nothing may be added.

    The remaining field's production readers, and what has to happen before it can go:

    * ``adcp_testing`` -- one decision, ``webhook_validator.py:196``, which loosens
      ``EgressPolicy.check_registration``'s loopback check while a suite runs. The
      replacement is reachable loopback origins in the test environments -- the same work
      the outbound private-address hatch needs. That hatch's env var is deliberately not
      spelled out here: ``test_architecture_no_outbound_insecure_hatch`` counts a literal
      mention as a declaration site, which is the right reading for a flag that can
      disable the address gate, so naming it in prose would quietly widen its pin. The
      seven properties on this class that used to fork on ``adcp_testing`` are already
      pinned shrink-only (GH #2255).

    SEVEN FIELDS WENT, and they are the shape of what "removing a test flag" means.
    ``adcp_auth_test_mode`` decided whether ``create_app`` COMPOSED a test-credential login
    blueprint, so the app under test differed from the deployed one at composition -- a
    frame above the request paths this rule usually polices. Its six ``test_*`` credentials
    were that blueprint's password table. A suite's verdict could turn on whether the flag
    happened to be set: two template tests passed on the CI box, which set it, and failed on
    a laptop, which did not. The route is deleted; a test that needs an admin session signs
    one (``tests/helpers/admin_session.py``), and a deployment reaches its first admin
    through its identity provider, with per-tenant Setup Mode covering the interval before
    SSO is switched on.

    Provisioning facts a deployment legitimately sets -- seed a demo tenant, seed sample
    data, skip migrations -- are NOT here; they are :class:`ProvisioningSettings`. They sat
    in this class and that was the confusion worth removing: a name implying everything
    inside is a test artifact hides which fields are actually defects, and a demo
    deployment genuinely wants a demo tenant.
    """

    model_config = _ENV

    adcp_testing: bool = False


class ProvisioningSettings(BaseSettings):
    """What an operator asks a fresh deployment to create on first boot.

    Real deployment inputs, read once at init (``src/core/database/database.py``,
    ``scripts/setup/init_database.py``): a demo deployment wants a demo tenant, and
    ``skip_migrations`` is an ops decision about who runs alembic. None of them asks
    whether a suite is running, which is why they are not :class:`TestingSettings`.
    """

    model_config = _ENV

    create_demo_tenant: bool = False
    create_sample_data: bool = False
    skip_migrations: bool = False


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection and pool."""

    model_config = _ENV

    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "adcp"
    db_user: str = "adcp"
    db_password: str = ""
    db_sslmode: str = "prefer"
    use_pgbouncer: bool = False
    database_query_timeout: int = 30
    database_connect_timeout: int = 10
    database_pool_timeout: int = 30
    db_pool_size: int = 10
    db_max_overflow: int = 20


class AuthSettings(BaseSettings):
    """Operator authentication and the seller's own credentials to third parties."""

    model_config = _ENV

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None
    google_credentials_file: str | None = None
    oauth_provider: str = "google"
    oauth_discovery_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: str = "openid email profile"
    super_admin_emails: str = ""
    super_admin_domains: str = ""
    tenant_management_emails: str | None = None
    tenant_management_domains: str | None = None
    tenant_management_api_key: str | None = None
    sync_api_key: str | None = None
    encryption_key: str | None = None
    gam_oauth_client_id: str = ""
    gam_oauth_client_secret: str = ""
    gcp_project_id: str | None = None
    google_application_credentials: str | None = None
    google_application_credentials_json: str | None = None

    @field_validator("gam_oauth_client_id")
    @classmethod
    def _gam_client_id_shape(cls, v: str) -> str:
        if v and not v.endswith(".apps.googleusercontent.com"):
            raise ValueError("GAM_OAUTH_CLIENT_ID must end with '.apps.googleusercontent.com'")
        return v

    @field_validator("gam_oauth_client_secret")
    @classmethod
    def _gam_client_secret_shape(cls, v: str) -> str:
        if v and not v.startswith("GOCSPX-"):
            raise ValueError("GAM_OAUTH_CLIENT_SECRET must start with 'GOCSPX-'")
        return v

    @property
    def super_admin_email_list(self) -> list[str]:
        return [e.lower() for e in _csv(self.super_admin_emails)]

    @property
    def super_admin_domain_list(self) -> list[str]:
        return [d.lower() for d in _csv(self.super_admin_domains)]

    @property
    def gam_oauth_configured(self) -> bool:
        return bool(self.gam_oauth_client_id and self.gam_oauth_client_secret)

    @property
    def tenant_management_email_list_source(self) -> str:
        """The operator list the database seed stores: the tenant-management spelling wins."""
        return self.tenant_management_emails or self.super_admin_emails

    @property
    def tenant_management_domain_list_source(self) -> str:
        return self.tenant_management_domains or self.super_admin_domains


class IntegrationSettings(BaseSettings):
    """Other services this seller talks to."""

    model_config = _ENV

    creative_agent_url: str | None = None
    approximated_api_key: str | None = None
    approximated_backend_url: str = "adcp-sales-agent.fly.dev"
    approximated_proxy_ip: str = "37.16.24.200"
    pydantic_ai_provider: str = "gemini"
    pydantic_ai_model: str = "gemini-2.0-flash"
    logfire_token: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    aws_access_key_id: str | None = None

    def provider_api_key(self, provider: str) -> str | None:
        """The API key for an AI *provider* name as ``services.ai`` spells it."""
        keys = {
            "google": self.gemini_api_key,
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "groq": self.groq_api_key,
            "bedrock": self.aws_access_key_id,
        }
        return keys.get(provider)


class LimitSettings(BaseSettings):
    """Operator-tunable ceilings and intervals."""

    model_config = _ENV

    idempotency_max_active_attempts_per_scope: int = 1000
    idempotency_insert_rate_window_seconds: int = 10
    idempotency_max_inserts_per_window: int = 300
    delivery_webhook_interval: int = 3600
    media_buy_status_check_interval: int = 60
    adcp_outbound_allow_private: bool = False
    adcp_outbound_backoff_base_seconds: float = Field(default=1.0, gt=0)
    adcp_webhook_delivery_timeout_seconds: float = Field(default=10.0, gt=0)
    adcp_webhook_breaker_failure_threshold: int = Field(default=5, gt=0)
    adcp_webhook_breaker_success_threshold: int = Field(default=2, gt=0)
    adcp_webhook_breaker_timeout_seconds: int = Field(default=60, gt=0)


class ToolingSettings(BaseSettings):
    """Knobs the repo's own scripts and audits read; nothing the application serves depends
    on them, so they are not part of :class:`Settings`. A script reads them where it starts."""

    model_config = _ENV

    adcp_home: Path | None = None
    adcp_req_path: Path = Path.home() / "projects" / "adcp-req"
    bdd_liveness_artifact: Path | None = None
    storyboard_ledger_path: Path | None = None
    allow_live_creative_agent: bool = False


@dataclass(frozen=True)
class Settings:
    """Everything the environment says, as one object.

    A plain composite, deliberately not a ``BaseSettings``: the groups read the environment,
    and this object only holds them. As a ``BaseSettings`` its six field names were themselves
    environment variables, so a shell with ``TESTING=1`` or ``DATABASE=x`` could not start
    the process.
    """

    runtime: RuntimeSettings
    testing: TestingSettings
    provisioning: ProvisioningSettings
    database: DatabaseSettings
    auth: AuthSettings
    integrations: IntegrationSettings
    limits: LimitSettings

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            runtime=RuntimeSettings(),
            testing=TestingSettings(),
            provisioning=ProvisioningSettings(),
            database=DatabaseSettings(),
            auth=AuthSettings(),
            integrations=IntegrationSettings(),
            limits=LimitSettings(),
        )

    # --- the allowances ADCP_TESTING implies, each under its own name ---------------

    @property
    def debug_routes_enabled(self) -> bool:
        """The `/debug/*` and `/_internal/*` routes exist at all."""
        return self.testing.adcp_testing

    @property
    def reference_formats_only(self) -> bool:
        """The creative registry serves the checked-in reference formats, never a network."""
        return self.testing.adcp_testing

    @property
    def loopback_webhooks_allowed(self) -> bool:
        """A buyer webhook may target localhost over plain HTTP (a capture server)."""
        return self.testing.adcp_testing

    @property
    def mock_delivery_seed_enabled(self) -> bool:
        """The mock ad server reads seeded delivery rows; the table has no production writer."""
        return self.testing.adcp_testing

    @property
    def mock_adapter_counts_as_configured(self) -> bool:
        """The setup checklist treats the mock adapter as a configured ad server."""
        return self.testing.adcp_testing

    @property
    def relaxed_brand_validation(self) -> bool:
        """get_products accepts simple test values where it would demand a real brand."""
        return self.testing.adcp_testing

    @property
    def unit_tests_may_not_open_a_database(self) -> bool:
        """Under test with no DATABASE_URL, opening an engine is a test bug, not a fallback."""
        return self.testing.adcp_testing and not self.database.database_url

    # --- production-derived selections ------------------------------------------------

    @property
    def publisher_auto_verify_allowed(self) -> bool:
        """A publisher partner is verified without an adagents.json check: a local server
        is in nobody's file. Anywhere that is not production."""
        return not self.runtime.is_production

    @property
    def structured_logging(self) -> bool:
        return self.runtime.structured_logging

    @property
    def verbose_auth_log(self) -> bool:
        return self.runtime.verbose_auth_log

    @property
    def pydantic_extra_mode(self) -> Literal["ignore", "forbid"]:
        return self.runtime.pydantic_extra_mode


_settings: Settings | None = None


def load_settings() -> Settings:
    """Read the environment and make the result the current settings.

    Called by each composition root when it starts, and by a test after it changes the
    environment. Validation failures raise here, at startup, not at the first request.
    """
    global _settings
    _settings = Settings.from_environment()
    return _settings


def get_settings() -> Settings:
    """The current settings, built on first use."""
    global _settings
    if _settings is None:
        _settings = Settings.from_environment()
    return _settings


def validate_configuration() -> None:
    """Load the settings at startup and report what is configured."""
    try:
        settings = load_settings()
    except Exception as e:
        raise RuntimeError(f"Configuration validation failed: {e}") from e

    print("✅ Configuration validation passed")
    print(f"   GAM OAuth: {'✅ Configured' if settings.auth.gam_oauth_configured else '❌ Not configured'}")
    print(f"   Database: {'✅ Configured' if settings.database.database_url else '❌ Not configured'}")
    print(
        f"   Gemini API: {'✅ Configured' if settings.integrations.gemini_api_key else '⚪ Not configured (tenants use own keys)'}"
    )
    print(
        f"   Super Admin: {'✅ Configured' if settings.auth.super_admin_emails else '⚪ Not configured (use per-tenant OIDC)'}"
    )


def is_production() -> bool:
    return get_settings().runtime.is_production


def get_pydantic_extra_mode() -> Literal["ignore", "forbid"]:
    """The request DTOs' extra mode, read while the schema modules define their classes.

    Reads :class:`RuntimeSettings` alone and never builds :class:`Settings`: importing a
    schema must not be where a bad credential fails.
    """
    return RuntimeSettings().pydantic_extra_mode
