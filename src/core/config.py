"""Configuration management for Prebid Sales Agent.

Provides Pydantic-based configuration classes for type-safe, validated configuration
management using environment variables.
"""

import os
from typing import Any, Literal, TypedDict

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.signing_contract import CACHE_MAX_AGE_SECONDS, BrandAgentType

# AdCP 3.1.1 `security.mdx` §per-keyid cap, restated by the signed-requests test-kit
# as `production_min_per_keyid_cap_requests: 1000000`.
_PRODUCTION_MIN_PER_KEYID_CAP = 1_000_000

# Characters that make an override key a PATTERN rather than one counterparty's keyid.
_KEYID_PATTERN_CHARS = "*?%[]"


class CounterpartyRegistryEntry(TypedDict):
    """One configured counterparty's key material, as the request path consumes it.

    The four keys are exactly what
    :func:`~src.core.signing.request_verifier_middleware.build_registry_resolution`
    reads. Declaring them here makes the settings boundary refuse a malformed
    entry, so the request path cannot meet one: ``SigningConfig`` is a
    ``BaseSettings`` with ``extra="forbid"``, which pydantic propagates into this
    ``TypedDict``, so a missing key raises ``missing`` and a misspelled one
    raises both ``missing`` for the key it failed to spell and
    ``extra_forbidden`` naming the misspelling.
    """

    agent_url: str
    jwks_uri: str
    key_origin: str
    jwks: dict[str, Any]


def _validate_explicit_keyid(key: str, field_name: str) -> None:
    """Refuse an empty or pattern-shaped key on a per-keyid config map.

    Shared by every per-keyid map on SigningConfig (override maps AND the
    counterparty registry) so "explicit keyids only" is one rule, not one
    reimplementation per field — a pattern key on ANY of them would lower a
    protection globally, which is refused everywhere identically.
    """
    if not key.strip():
        raise ValueError(f"{field_name}: a key must be an explicit keyid, not empty")
    if any(char in key for char in _KEYID_PATTERN_CHARS):
        raise ValueError(
            f"{field_name}: key {key!r} looks like a pattern. Keys name explicit "
            "keyids only — a pattern would lower the protection globally, which is refused."
        )


def _test_kit_relaxation_forbidden_signal() -> str | None:
    """Which production signal (if any) forbids test-kit signing relaxations.

    Deliberately the UNION of every production signal an entrypoint in this
    codebase checks today: ``ENVIRONMENT=production`` (``is_production()``),
    ``PRODUCTION=true`` (``is_admin_production()``), and ``FLY_APP_NAME`` set
    (``scripts/run_server.py``). NOT a reuse of ``is_production()`` or
    ``is_admin_production()`` — those are narrower checks scoped to their own
    subsystems (schema validation strictness, admin auth), and widening either
    to include ``FLY_APP_NAME`` would change behavior for callers this ticket
    has not tested. The blast radius a test-kit relaxation opens — a keyid
    alone becoming sufficient to be trusted as a counterparty — warrants the
    widest, most paranoid union rather than reusing a narrower predicate.
    Any non-empty value of ``PRODUCTION``/``FLY_APP_NAME`` counts (matching
    ``run_server.py``'s looser reading), not only ``"true"``.
    """
    if os.getenv("ENVIRONMENT", "").strip().lower() == "production":
        return "ENVIRONMENT"
    if os.getenv("PRODUCTION", "").strip():
        return "PRODUCTION"
    if os.getenv("FLY_APP_NAME", "").strip():
        return "FLY_APP_NAME"
    return None


class GAMOAuthConfig(BaseSettings):
    """Google Ad Manager OAuth configuration."""

    client_id: str = Field(default="", description="GAM OAuth Client ID from Google Cloud Console")
    client_secret: str = Field(default="", description="GAM OAuth Client Secret from Google Cloud Console")

    model_config = SettingsConfigDict(env_prefix="GAM_OAUTH_", case_sensitive=False)

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, v):
        """Validate GAM OAuth Client ID format (only if provided)."""
        if not v:
            return v  # Allow empty - validation happens when GAM adapter is used
        if not v.endswith(".apps.googleusercontent.com"):
            raise ValueError("GAM OAuth Client ID must end with '.apps.googleusercontent.com'")
        return v

    @field_validator("client_secret")
    @classmethod
    def validate_client_secret(cls, v):
        """Validate GAM OAuth Client Secret format (only if provided)."""
        if not v:
            return v  # Allow empty - validation happens when GAM adapter is used
        if not v.startswith("GOCSPX-"):
            raise ValueError("GAM OAuth Client Secret must start with 'GOCSPX-'")
        return v


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str | None = Field(default=None, description="Database connection URL")
    type: str = Field(default="postgresql", description="Database type")

    model_config = SettingsConfigDict(env_prefix="DATABASE_", case_sensitive=False)


class ServerConfig(BaseSettings):
    """Server configuration."""

    adcp_sales_port: int = Field(default=8080, description="MCP server port")
    admin_ui_port: int = Field(default=8001, description="Admin UI port")
    a2a_port: int = Field(default=8091, description="A2A server port")

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)


class GoogleOAuthConfig(BaseSettings):
    """Google OAuth configuration for admin UI."""

    client_id: str | None = Field(default=None, description="Google OAuth Client ID")
    client_secret: str | None = Field(default=None, description="Google OAuth Client Secret")
    credentials_file: str | None = Field(default=None, description="Path to Google OAuth credentials file")

    model_config = SettingsConfigDict(env_prefix="GOOGLE_", case_sensitive=False)


class SuperAdminConfig(BaseSettings):
    """Super admin configuration."""

    emails: str = Field(default="", description="Comma-separated list of super admin emails")
    domains: str | None = Field(default=None, description="Comma-separated list of super admin domains")

    model_config = SettingsConfigDict(env_prefix="SUPER_ADMIN_", case_sensitive=False)

    @property
    def email_list(self) -> list[str]:
        """Get super admin emails as a list."""
        return [email.strip() for email in self.emails.split(",") if email.strip()]

    @property
    def domain_list(self) -> list[str]:
        """Get super admin domains as a list."""
        if not self.domains:
            return []
        return [domain.strip() for domain in self.domains.split(",") if domain.strip()]


class SigningConfig(BaseSettings):
    """Agent-level posture for RFC 9421 message signing (#1291).

    Not to be confused with ``adcp.signing.SigningConfig``, which is the SDK's
    auto-signing bundle — alias at any import site that touches both.

    Originally scoped to OUR OWN key material (A2); A4 added the replay knobs and B1
    the inbound-verifier knobs, so the scope is now "everything about signing that is
    a property of the DEPLOYMENT rather than of a tenant".

    The split the key fields encode: the STORE KIND is agent-level (one process, one
    key store), while each key's LOCATION is per-tenant and lives on the
    ``signing_keys`` row's ``private_key_ref``. Each tenant is a distinct seller
    identity with its own brand domain and therefore its own key material, so a
    single agent-level key location is unimplementable. Verifier POSTURE is likewise
    per-tenant and lives in the tenant's declaration
    (:class:`src.core.signing.posture.RequestSigningPosture`), never here — the knobs
    below are transport limits and a kill switch, not policy.
    """

    provider: Literal["in_memory", "kms"] = Field(
        default="in_memory", description="SigningProvider implementation: in_memory (default) or kms"
    )
    allowed_key_ref_schemes: str = Field(
        default="db,env,file",
        description=(
            "Comma-separated private_key_ref schemes this deployment will resolve. "
            "db: the encrypted PEM on the signing_keys row — the only scheme this agent MINTS. "
            "env: a PEM handed to the process by the orchestrator, for single-tenant deployments. "
            "file: read-only, for material someone else provisioned onto a mounted secret"
        ),
    )
    key_passphrase_env: str | None = Field(
        default=None,
        description="Name of the env var holding the PEM passphrase (the passphrase itself is never a config value)",
    )

    # -- Replay store (#1291 A4) -------------------------------------------
    # These are agent-level because the replay store is a property of the shared
    # deployment, not of a tenant: one nonce is accepted at most once across every
    # worker, whichever tenant's virtual host it arrived at.
    per_keyid_cap: int = Field(
        default=1_000_000,
        description="Live replay entries per keyid before request_signature_rate_abuse (spec floor: 1,000,000)",
    )
    per_keyid_cap_overrides: dict[str, int] = Field(
        default_factory=dict,
        description="Per-counterparty cap override, keyed by explicit keyid (test-kit counterparties -> 100)",
    )
    replay_ttl_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="Per-counterparty clamp (seconds) on replay row lifetime, keyed by explicit keyid",
    )
    replay_claim_ttl_seconds: float = Field(
        default=60.0,
        description="Lifetime written by the atomic claim before remember() raises it to the signature's own TTL",
    )

    # -- Configured counterparty registry (#1291 B4) -----------------------
    counterparty_registry: dict[str, CounterpartyRegistryEntry] = Field(
        default_factory=dict,
        description=(
            "Per-keyid registered counterparty entries ({agent_url, jwks_uri, key_origin, jwks}), "
            "keyed by explicit keyid. Consulted by request_verifier_middleware._resolution_for as "
            "a FALLBACK when a signed request's principal carries no agent_url to walk (the "
            "signed_requests_runner conformance suite sends no bearer at all). NEVER consulted "
            "when a principal-derived walk exists but FAILS -- that path returns whatever is "
            "cached (possibly nothing) on its own, so a config-seeded keyid can never silently "
            "replace a real, briefly-unreachable counterparty's onboarded identity. Refused "
            "entirely in production (see the model validator below) -- this is a conformance-"
            "grading key-trust source, never a substitute for real onboarding."
        ),
    )

    # -- Inbound verifier (#1291 B1) ---------------------------------------
    verifier_enabled: bool = Field(
        default=True,
        description=(
            "Kill switch for the inbound RFC 9421 verifier middleware. False makes every "
            "request pass through untouched, so a rollback is a flag flip and not a deploy"
        ),
    )
    max_skew_seconds: int = Field(
        default=60,
        description="Clock skew the verifier tolerates on a signature's created/expires params",
    )
    max_window_seconds: int = Field(
        default=300,
        description="Longest signature validity window the verifier accepts (spec ceiling)",
    )
    max_signed_body_bytes: int = Field(
        default=10 * 1024 * 1024,
        description=(
            "Cap on the request body the verifier buffers before hashing. Bounds the memory a "
            "pre-auth caller can make one worker hold; over-cap requests are rejected with 413"
        ),
    )
    agent_resolution_ttl_seconds: float = Field(
        default=3600.0,
        description=(
            "How long a resolved counterparty AgentResolution (jwks + jwks_uri + key_origins) "
            "stays usable before the brand.json walk is repeated. The whole resolution is cached, "
            "not just the JWKS, because expected_key_origins must be passed on every verify"
        ),
    )
    agent_resolution_refetch_cooldown_seconds: float = Field(
        default=30.0,
        description=(
            "Quiet period after a failed counterparty resolution. Without it every signed request "
            "from a counterparty with a broken brand.json starts a fresh 3-hop outbound walk"
        ),
    )
    counterparty_agent_type: BrandAgentType = Field(
        default="buying",
        description=(
            "brand.json agents[] type used to resolve a signing counterparty's JWKS. The agents "
            "that sign requests TO a sales agent are the buy side, so their brand.json entry is "
            "the buying agent, not the sales one. Typed as the SDK's Literal rather than str, so "
            "an env override naming a type the resolver cannot resolve is refused HERE, at the "
            "settings boundary, instead of 401-ing every signed counterparty with nothing naming "
            "the cause. The refusal reads the permitted set from the Literal itself, so an SDK "
            "that adds an agent type cannot leave a hand-written copy behind"
        ),
    )
    # There is deliberately NO `allow_private_destinations` knob. Plan step 7 proposed
    # one; `tests/unit/test_architecture_no_private_destinations.py` forbids any src/
    # call site from passing it, and that guard is right: a configurable SSRF pin is a
    # pin an operator can remove, and key discovery follows a counterparty-supplied
    # URL. The SDK default (pinned) is what production gets. B4's sandbox counterparty
    # needs the relaxation only inside tests, which is where it stays.

    # -- Revocation, checklist step 9 (#1291 A5) ---------------------------
    # Step 9 has two halves and this group carries both: a locally-seeded
    # revoked set (membership) and the posture for a list we could not read
    # (staleness). Grounded in `git -C ~/projects/adcp show
    # v3.1.1:docs/building/by-layer/L1/security.mdx` :1238, :1328, :1333.
    revoked_keyids: str = Field(
        default="",
        description=(
            "Comma-separated counterparty keyids this deployment treats as revoked, regardless of "
            "any published list. Monotone in the fail-closed direction — it can only ADD rejections "
            "— which is what makes it a posture and not a backdoor; there is deliberately no "
            "un-revoke knob. Set to test-revoked-2026 on the conformance-grading deployment"
        ),
    )
    require_revocation_list: bool = Field(
        default=False,
        description=(
            "Whether a counterparty that publishes NO readable revocation list is rejected with "
            "request_signature_revocation_stale instead of served. False today because nobody in "
            "the ecosystem publishes one yet; flip it to True once counterparties this deployment "
            "accepts serve /.well-known/governance-revocations.json. It does NOT govern a list "
            "that HAS loaded and then aged out — that rejection is unconditional (security.mdx :1333)"
        ),
    )
    revocation_grace_multiplier: float = Field(
        default=4.0,
        description=(
            "Multiples of the list's declared polling interval tolerated beyond next_update before "
            "request_signature_revocation_stale. security.mdx :1333 requires 4x; the SDK default is "
            "2.0, so this is passed explicitly at the single construction site"
        ),
    )
    revocation_issuer_origin: str | None = Field(
        default=None,
        description=(
            "Pins the revocation-list issuer origin for every counterparty, overriding the "
            "per-counterparty derivation from brand_json_url (security.mdx :1328). For a deployment "
            "fronted by one governance issuer, and for the conformance sandbox"
        ),
    )

    # -- Trust-root publication (#1291 A3) ---------------------------------
    grace_seconds: int = Field(
        default=2 * CACHE_MAX_AGE_SECONDS,
        description=(
            "How long a revoked key keeps appearing (with its revoked_at marker) in the published "
            "trust root. Derived from the published Cache-Control max-age, not configured beside it"
        ),
    )

    # -- Combined revocation-list publication (#1291 A5 follow-up) ---------
    revocation_interval_seconds: int = Field(
        default=CACHE_MAX_AGE_SECONDS,
        ge=CACHE_MAX_AGE_SECONDS,
        le=1800,
        description=(
            "Declared cadence for the published /.well-known/governance-revocations.json list's "
            "next_update. security.mdx :717 states a 60s floor and a 1800s (30 min) ceiling; the "
            "floor ENFORCED here is CACHE_MAX_AGE_SECONDS (300s), not the spec's bare 60s, because "
            ":1103 bounds our published brand.json cache TTL BY this interval and "
            "CACHE_MAX_AGE_SECONDS is a fixed module constant that cannot itself shrink below "
            "300s — any interval under 300s would violate that relation against our own "
            "unmodified brand.json unconditionally. Note the pinned SDK's own consumer "
            "(CachingRevocationChecker) clamps its effective polling interval at "
            "MAX_POLLING_INTERVAL_SECONDS (900s, adcp.signing.revocation_fetcher) regardless of "
            "what we declare above that — a value in (900, 1800] is spec-legal to PUBLISH but "
            "shrinks only OUR OWN consumer's polling, never rejected outright."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="ADCP_SIGNING_", case_sensitive=False)

    @property
    def key_ref_scheme_list(self) -> list[str]:
        """Allowed ``private_key_ref`` schemes as a list.

        A comma-joined ``str`` rather than a ``tuple[str, ...]`` deliberately:
        pydantic-settings treats sequence fields as complex types and JSON-parses
        the env value, so ``ADCP_SIGNING_ALLOWED_KEY_REF_SCHEMES=env,file`` would
        raise at startup and only ``["env","file"]`` would work. This is the gate
        that lets a deployment forbid ``file:`` in production — the one field
        least worth making awkward to set. Same shape as ``SuperAdminConfig``.
        """
        return [scheme.strip() for scheme in self.allowed_key_ref_schemes.split(",") if scheme.strip()]

    @property
    def revoked_keyid_list(self) -> list[str]:
        """Locally-seeded revoked keyids as a list.

        Comma-joined ``str`` for the same reason as
        :attr:`key_ref_scheme_list`: pydantic-settings JSON-parses sequence
        fields, so ``ADCP_SIGNING_REVOKED_KEYIDS=test-revoked-2026`` would raise
        at startup if this were a ``list[str]``.
        """
        return [keyid.strip() for keyid in self.revoked_keyids.split(",") if keyid.strip()]

    @property
    def key_passphrase(self) -> bytes | None:
        """Resolve the configured PEM passphrase, or None.

        Resolved from the environment on every call rather than held as a field:
        CPython cannot zero a ``bytes``, so the SDK's guidance is to source the
        passphrase per use rather than pin a literal in process memory for the
        life of the config object.
        """
        if not self.key_passphrase_env:
            return None
        value = os.getenv(self.key_passphrase_env)
        return value.encode() if value else None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Reject ``kms`` until a KMS provider exists.

        Fires when ``AppConfig()`` is constructed, which ``validate_configuration()``
        does at startup — so selecting an unimplemented provider kills the process
        then, not at the first signature (note §11).
        """
        if v == "kms":
            raise ValueError(
                "ADCP_SIGNING_PROVIDER='kms' is not implemented — no KMS SigningProvider exists yet. Use 'in_memory'."
            )
        return v

    @field_validator("per_keyid_cap")
    @classmethod
    def validate_per_keyid_cap(cls, v: int) -> int:
        """Refuse a GLOBAL cap below the spec floor.

        AdCP 3.1.1 ``security.mdx`` §per-keyid cap and the test-kit's
        ``production_min_per_keyid_cap_requests: 1000000`` put the production floor
        at 1,000,000 live entries per keyid. The test-kit's
        ``grading_target_per_keyid_cap_requests: 100`` is permitted for the test-kit
        COUNTERPARTY only, which is what ``per_keyid_cap_overrides`` is for. Refusing
        the global lowering here is the mechanical form of "never a global lowering":
        a misconfiguration kills the process at startup instead of quietly turning
        every busy signer into ``request_signature_rate_abuse``.
        """
        if v < _PRODUCTION_MIN_PER_KEYID_CAP:
            raise ValueError(
                f"ADCP_SIGNING_PER_KEYID_CAP={v} is below the spec floor of "
                f"{_PRODUCTION_MIN_PER_KEYID_CAP} live entries per keyid. Lower the cap for a single "
                "test counterparty with ADCP_SIGNING_PER_KEYID_CAP_OVERRIDES, never globally."
            )
        return v

    @field_validator("per_keyid_cap_overrides", "replay_ttl_overrides")
    @classmethod
    def validate_overrides_name_explicit_keyids(cls, v: dict[str, float], info: ValidationInfo) -> dict[str, float]:
        """Both override maps name explicit keyids — never a pattern.

        Each map lowers a spec-mandated protection (the cap, and the replay row's
        lifetime) for one counterparty. A wildcard or prefix key would re-introduce
        the global lowering the validator above refuses, by the back door and for a
        value nobody reads as global. Same rule, same reason, so one validator serves
        both fields.
        """
        for key, value in v.items():
            _validate_explicit_keyid(key, info.field_name or "")
            if value <= 0:
                raise ValueError(f"{info.field_name}: override for keyid {key!r} must be positive, got {value}")
        return v

    @field_validator("counterparty_registry")
    @classmethod
    def validate_counterparty_registry_keys(
        cls, v: dict[str, CounterpartyRegistryEntry], info: ValidationInfo
    ) -> dict[str, CounterpartyRegistryEntry]:
        """Registry entries are keyed by explicit keyid too — same rule as the override maps.

        Only the KEY shape is checked here. The VALUE shape needs no check,
        because :class:`CounterpartyRegistryEntry` is the annotation: pydantic
        refuses a malformed entry while building the field, before this runs. A
        loop restating the four required keys would re-derive what the type
        already enforces.
        """
        for key in v:
            _validate_explicit_keyid(key, info.field_name or "")
        return v

    @model_validator(mode="after")
    def validate_test_kit_relaxations_forbidden_in_production(self) -> "SigningConfig":
        """Refuse any non-empty test-kit relaxation under a production signal.

        Placement is deliberate: a ``@model_validator`` fires on EVERY
        ``SigningConfig()``/``AppConfig()`` construction, so every process that
        can reach ``get_config()`` is covered — unlike
        ``validate_configuration()``, which is reachable only through
        ``initialize_application()`` (``scripts/run_server.py``,
        ``src/admin/server.py``); ``src/app.py``'s ASGI lifespan never calls it,
        so a deployment pointing gunicorn/uvicorn at ``src.app:app`` directly
        (the default shape on most platforms) would boot the verifier and the
        registry with that guard never executing. This one cannot be bypassed
        by entrypoint choice.

        Refuses the RELAXATIONS, not construction itself: a production
        deployment with none of these three fields set must still boot.
        """
        signal = _test_kit_relaxation_forbidden_signal()
        if signal is None:
            return self
        for field_name in ("counterparty_registry", "per_keyid_cap_overrides", "replay_ttl_overrides"):
            if getattr(self, field_name):
                raise ValueError(
                    f"{field_name} is a conformance-grading relaxation and must not be set "
                    f"when {signal} signals a production deployment"
                )
        return self

    @field_validator("grace_seconds")
    @classmethod
    def validate_grace_seconds(cls, v: int) -> int:
        """The grace window must EXCEED the cache TTL we publish, not merely equal it.

        ``core/agent-signing-key.json`` allows removal once the TTL has elapsed
        "across all verifiers" — equal values leave zero margin for an
        intermediary that adds its own delay, so a verifier could still be
        serving a cached document from which the key has already vanished
        WITHOUT its revocation marker.
        """
        if v <= CACHE_MAX_AGE_SECONDS:
            raise ValueError(
                f"ADCP_SIGNING_GRACE_SECONDS must exceed the published cache max-age "
                f"({CACHE_MAX_AGE_SECONDS}s), got {v}"
            )
        return v

    @field_validator("replay_claim_ttl_seconds")
    @classmethod
    def validate_replay_claim_ttl(cls, v: float) -> float:
        """A non-positive claim TTL would write an already-dead row — i.e. no replay protection at all."""
        if v <= 0:
            raise ValueError(f"ADCP_SIGNING_REPLAY_CLAIM_TTL_SECONDS must be positive, got {v}")
        return v


class AppConfig(BaseSettings):
    """Main application configuration."""

    gemini_api_key: str | None = Field(
        default=None, description="Platform-level Gemini API key (optional - tenants can configure their own)"
    )
    flask_secret_key: str = Field(default="dev-secret-key-change-in-production", description="Flask secret key")
    debug: bool = Field(default=False, description="Enable debug mode")
    environment: str = Field(default="development", description="Environment: production, staging, or development")

    # Configuration objects
    # BaseSettings subclasses read from environment; mypy doesn't understand this pattern
    gam_oauth: GAMOAuthConfig = Field(default_factory=GAMOAuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    google_oauth: GoogleOAuthConfig = Field(default_factory=GoogleOAuthConfig)
    superadmin: SuperAdminConfig = Field(default_factory=SuperAdminConfig)
    signing: SigningConfig = Field(default_factory=SigningConfig)

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)


# Global configuration instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def validate_configuration() -> None:
    """Validate all configuration at startup.

    Raises:
        ValueError: If required configuration is missing or invalid
        RuntimeError: If configuration validation fails
    """
    try:
        config = get_config()

        # Validate GAM OAuth configuration
        if config.gam_oauth:
            # Configuration validation happens automatically via Pydantic
            pass

        # Note: GEMINI_API_KEY is optional - tenants configure their own AI keys
        # Note: SUPER_ADMIN_EMAILS is optional - per-tenant OIDC with Setup Mode is the default auth flow

        # Signing: an unimplemented provider must kill the process HERE, not at
        # the first signature. The field_validator on SigningConfig already
        # raises; this names the provider explicitly rather than surfacing a raw
        # Pydantic trace.
        if config.signing.provider not in ("in_memory",):
            raise ValueError(
                f"ADCP_SIGNING_PROVIDER={config.signing.provider!r} is not implemented — "
                "the only available SigningProvider is 'in_memory'."
            )

        print("✅ Configuration validation passed")
        print(f"   GAM OAuth: {'✅ Configured' if config.gam_oauth.client_id else '❌ Not configured'}")
        print(f"   Database: {'✅ Configured' if config.database.url else '❌ Not configured'}")
        print(
            f"   Gemini API: {'✅ Configured' if config.gemini_api_key else '⚪ Not configured (tenants use own keys)'}"
        )
        print(
            f"   Super Admin: {'✅ Configured' if config.superadmin.emails else '⚪ Not configured (use per-tenant OIDC)'}"
        )

    except Exception as e:
        raise RuntimeError(f"Configuration validation failed: {str(e)}") from e


def get_gam_oauth_config() -> GAMOAuthConfig:
    """Get GAM OAuth configuration."""
    return get_config().gam_oauth


def is_production() -> bool:
    """Check if running in production environment.

    Returns:
        bool: True if ENVIRONMENT=production, False otherwise
    """
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def get_pydantic_extra_mode() -> Literal["ignore", "forbid"]:
    """Get Pydantic extra field handling mode based on environment.

    Production: "ignore" - Accept extra fields for forward compatibility
    Non-production: "forbid" - Reject extra fields to catch bugs early
    """
    return "ignore" if is_production() else "forbid"
