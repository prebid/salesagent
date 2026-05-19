"""Tests for SpringServe adapter schemas -- encryption round-trip + validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.adapters.springserve.schemas import (
    SPRINGSERVE_HOSTS,
    SpringServeConnectionConfig,
    SpringServeProductConfig,
)
from src.core.utils.encryption import generate_encryption_key, is_encrypted


@pytest.fixture
def encryption_key():
    key = generate_encryption_key()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
        yield key


class TestPasswordGrantConfig:
    """Canonical auth path -- email + password -- mints/refreshes tokens."""

    def test_accepts_email_and_password(self):
        cfg = SpringServeConnectionConfig(email="api@example.com", password="hunter2")
        assert cfg.email == "api@example.com"
        assert cfg.password == "hunter2"
        assert cfg.api_token is None
        assert cfg.environment == "production"
        assert cfg.base_url == "https://console.springserve.com/api/v0"

    def test_password_serializes_to_ciphertext(self, encryption_key):
        cfg = SpringServeConnectionConfig(email="e", password="super-secret-password")
        dumped = cfg.model_dump()
        assert dumped["password"] != "super-secret-password"
        assert is_encrypted(dumped["password"])

    def test_password_round_trips_through_dump_and_validate(self, encryption_key):
        original = SpringServeConnectionConfig(email="e", password="super-secret-password")
        persisted = original.model_dump()
        rehydrated = SpringServeConnectionConfig.model_validate(persisted)
        assert rehydrated.password == "super-secret-password"

    def test_already_encrypted_password_not_double_encrypted(self, encryption_key):
        cfg = SpringServeConnectionConfig(email="e", password="super-secret-password")
        ciphertext = cfg.model_dump()["password"]
        rehydrated = SpringServeConnectionConfig.model_validate({"email": "e", "password": ciphertext})
        assert rehydrated.password == "super-secret-password"


class TestPreMintedTokenConfig:
    """Escape-hatch auth path -- pre-minted token, no auto-refresh."""

    def test_accepts_api_token_alone(self):
        cfg = SpringServeConnectionConfig(api_token="tok-xyz")
        assert cfg.api_token == "tok-xyz"
        assert cfg.email is None
        assert cfg.password is None

    def test_api_token_serializes_to_ciphertext(self, encryption_key):
        cfg = SpringServeConnectionConfig(api_token="super-secret-token")
        dumped = cfg.model_dump()
        assert dumped["api_token"] != "super-secret-token"
        assert is_encrypted(dumped["api_token"])

    def test_api_token_round_trips(self, encryption_key):
        original = SpringServeConnectionConfig(api_token="super-secret-token")
        persisted = original.model_dump()
        rehydrated = SpringServeConnectionConfig.model_validate(persisted)
        assert rehydrated.api_token == "super-secret-token"


class TestCredentialRequirement:
    """Exactly one auth path must be present."""

    def test_no_credentials_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="email \\+ password|api_token"):
            SpringServeConnectionConfig()

    def test_email_without_password_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SpringServeConnectionConfig(email="e")

    def test_password_without_email_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SpringServeConnectionConfig(password="p")

    def test_both_paths_set_is_allowed(self, encryption_key):
        """Token takes precedence when both are set; partners may temporarily
        inject a token while keeping creds on file."""
        cfg = SpringServeConnectionConfig(email="e", password="p", api_token="t")
        assert cfg.email == "e"
        assert cfg.api_token == "t"


class TestEnvironmentAndOptional:
    def test_invalid_environment_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SpringServeConnectionConfig(api_token="t", environment="staging")

    def test_default_demand_partner_id_optional(self):
        cfg = SpringServeConnectionConfig(api_token="t")
        assert cfg.default_demand_partner_id is None

    def test_default_demand_partner_id_accepts_int(self):
        cfg = SpringServeConnectionConfig(api_token="t", default_demand_partner_id=42)
        assert cfg.default_demand_partner_id == 42

    def test_password_field_marked_secret_in_schema(self):
        schema = SpringServeConnectionConfig.model_json_schema()
        assert schema["properties"]["password"].get("secret") is True

    def test_api_token_field_marked_secret_in_schema(self):
        schema = SpringServeConnectionConfig.model_json_schema()
        assert schema["properties"]["api_token"].get("secret") is True

    def test_hosts_table_has_production(self):
        assert "production" in SPRINGSERVE_HOSTS


class TestProvisioningModelDefaults:
    """The per-tenant provisioning knobs that decide how AdCP buyers ship
    demand into this tenant's SpringServe account."""

    def test_demand_class_defaults_to_line_item(self):
        """Most AdCP integrations expect SpringServe to host the creative
        (POST /videos + bind via creative_id), so the default class matches
        that path."""
        cfg = SpringServeConnectionConfig(api_token="t")
        assert cfg.demand_class == "line_item"

    def test_demand_class_accepts_tag(self):
        """Passthrough integrations where the buyer's third-party VAST/audio
        URL is the creative."""
        cfg = SpringServeConnectionConfig(api_token="t", demand_class="tag")
        assert cfg.demand_class == "tag"

    def test_demand_class_rejects_unknown_value(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SpringServeConnectionConfig(api_token="t", demand_class="anything_else")

    def test_enable_key_value_targeting_defaults_off(self):
        """Audience / content / device are typically supply-side selectors
        in SpringServe -- KV targeting is opt-in."""
        cfg = SpringServeConnectionConfig(api_token="t")
        assert cfg.enable_key_value_targeting is False

    def test_enable_key_value_targeting_opt_in(self):
        cfg = SpringServeConnectionConfig(api_token="t", enable_key_value_targeting=True)
        assert cfg.enable_key_value_targeting is True


class TestSpringServeProductConfig:
    def test_defaults_are_empty(self):
        cfg = SpringServeProductConfig()
        assert cfg.supply_tag_ids == []
        assert cfg.supply_partner_ids == []
        assert cfg.player_sizes == []
        assert cfg.environments == []
        assert cfg.device_types == []
        assert cfg.content_genres == []
        assert cfg.priority is None
        assert cfg.extra_demand_tag_fields == {}

    def test_no_media_type_flag(self):
        """Audio vs video is determined by the AdCP Product's ``format_ids``
        (springserve_audio_* vs springserve_video_*), not by a denormalised
        adapter-config flag. Guard against regressions that re-introduce one."""
        fields = set(SpringServeProductConfig.model_fields.keys())
        assert "is_audio" not in fields
        assert "media_type" not in fields

    def test_accepts_full_inventory_selection(self):
        cfg = SpringServeProductConfig(
            supply_tag_ids=[1001, 1002],
            supply_partner_ids=[5],
            player_sizes=["large", "medium"],
            environments=["ctv", "app"],
            device_types=["ctv", "mobile"],
            priority=10,
        )
        assert cfg.supply_tag_ids == [1001, 1002]
        assert cfg.player_sizes == ["large", "medium"]
        assert cfg.priority == 10

    def test_audio_product_uses_audio_environment(self):
        """Audio products carry no flag -- the audio routing comes from the
        Product's ``format_ids`` (e.g. springserve_audio_15s_pre_roll). The
        product config only declares supply/environment selectors."""
        cfg = SpringServeProductConfig(environments=["streaming_audio"])
        assert cfg.environments == ["streaming_audio"]

    def test_round_trip_through_dump_and_validate(self):
        cfg = SpringServeProductConfig(
            supply_tag_ids=[1, 2],
            supply_partner_ids=[3],
            player_sizes=["large"],
            environments=["ctv"],
            device_types=["ctv"],
            priority=5,
            extra_demand_tag_fields={"raw_field": "value"},
        )
        dumped = cfg.model_dump()
        rehydrated = SpringServeProductConfig.model_validate(dumped)
        assert rehydrated.model_dump() == dumped
        assert rehydrated.extra_demand_tag_fields == {"raw_field": "value"}
