"""Tests for FreeWheel adapter schemas — encryption round-trip + validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS, FreeWheelConnectionConfig, FreeWheelProductConfig
from src.core.utils.encryption import generate_encryption_key, is_encrypted


@pytest.fixture
def encryption_key():
    key = generate_encryption_key()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
        yield key


class TestPasswordGrantConfig:
    """Canonical auth path — username + password — mints/refreshes tokens."""

    def test_accepts_username_and_password(self):
        cfg = FreeWheelConnectionConfig(username="publisher@example.com", password="hunter2")
        assert cfg.username == "publisher@example.com"
        assert cfg.password == "hunter2"
        assert cfg.api_token is None
        assert cfg.environment == "production"
        assert cfg.base_url == "https://api.freewheel.tv"

    def test_password_serializes_to_ciphertext(self, encryption_key):
        cfg = FreeWheelConnectionConfig(username="u", password="super-secret-password")
        dumped = cfg.model_dump()
        assert dumped["password"] != "super-secret-password"
        assert is_encrypted(dumped["password"])

    def test_password_round_trips_through_dump_and_validate(self, encryption_key):
        original = FreeWheelConnectionConfig(username="u", password="super-secret-password")
        persisted = original.model_dump()
        rehydrated = FreeWheelConnectionConfig.model_validate(persisted)
        assert rehydrated.password == "super-secret-password"

    def test_already_encrypted_password_not_double_encrypted(self, encryption_key):
        cfg = FreeWheelConnectionConfig(username="u", password="super-secret-password")
        ciphertext = cfg.model_dump()["password"]
        rehydrated = FreeWheelConnectionConfig.model_validate({"username": "u", "password": ciphertext})
        assert rehydrated.password == "super-secret-password"


class TestPreMintedTokenConfig:
    """Escape-hatch auth path — pre-minted bearer, no auto-refresh."""

    def test_accepts_api_token_alone(self):
        cfg = FreeWheelConnectionConfig(api_token="bearer-xyz")
        assert cfg.api_token == "bearer-xyz"
        assert cfg.username is None
        assert cfg.password is None

    def test_api_token_serializes_to_ciphertext(self, encryption_key):
        cfg = FreeWheelConnectionConfig(api_token="super-secret-token")
        dumped = cfg.model_dump()
        assert dumped["api_token"] != "super-secret-token"
        assert is_encrypted(dumped["api_token"])

    def test_api_token_round_trips(self, encryption_key):
        original = FreeWheelConnectionConfig(api_token="super-secret-token")
        persisted = original.model_dump()
        rehydrated = FreeWheelConnectionConfig.model_validate(persisted)
        assert rehydrated.api_token == "super-secret-token"


class TestCredentialRequirement:
    """Exactly one auth path must be present."""

    def test_no_credentials_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="username \\+ password|api_token"):
            FreeWheelConnectionConfig()

    def test_username_without_password_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FreeWheelConnectionConfig(username="u")

    def test_password_without_username_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FreeWheelConnectionConfig(password="p")

    def test_both_paths_set_is_allowed(self, encryption_key):
        """Token takes precedence when both are set; this is intentional —
        partners may temporarily inject a token while keeping creds on file."""
        cfg = FreeWheelConnectionConfig(username="u", password="p", api_token="t")
        assert cfg.username == "u"
        assert cfg.api_token == "t"


class TestClientCredentialsConfig:
    """API-Access machine auth — client_id + client_secret, sandbox host."""

    def test_accepts_client_id_and_secret(self):
        cfg = FreeWheelConnectionConfig(client_id="cid", client_secret="csecret", environment="sandbox")
        assert cfg.client_id == "cid"
        assert cfg.client_secret == "csecret"
        assert cfg.environment == "sandbox"

    def test_sandbox_environment_resolves_to_sandbox_host(self):
        cfg = FreeWheelConnectionConfig(client_id="c", client_secret="s", environment="sandbox")
        assert cfg.base_url == "https://api.sandbox.freewheel.tv"

    def test_default_token_url_is_api_access_endpoint(self):
        cfg = FreeWheelConnectionConfig(client_id="c", client_secret="s")
        assert cfg.token_url == "https://token.apiaccess.freewheel.tv/oauth2/token"

    def test_token_url_must_be_https(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="https"):
            FreeWheelConnectionConfig(client_id="c", client_secret="s", token_url="http://evil.example/token")

    def test_client_secret_serializes_to_ciphertext(self, encryption_key):
        cfg = FreeWheelConnectionConfig(client_id="c", client_secret="super-secret-cc")
        dumped = cfg.model_dump()
        assert dumped["client_secret"] != "super-secret-cc"
        assert is_encrypted(dumped["client_secret"])

    def test_client_secret_round_trips(self, encryption_key):
        original = FreeWheelConnectionConfig(client_id="c", client_secret="super-secret-cc")
        rehydrated = FreeWheelConnectionConfig.model_validate(original.model_dump())
        assert rehydrated.client_secret == "super-secret-cc"

    def test_client_id_without_secret_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FreeWheelConnectionConfig(client_id="c")

    def test_client_secret_field_marked_secret_in_schema(self):
        schema = FreeWheelConnectionConfig.model_json_schema()
        assert schema["properties"]["client_secret"].get("secret") is True


class TestEnvironmentAndOptional:
    def test_staging_environment_resolves_to_staging_host(self):
        cfg = FreeWheelConnectionConfig(api_token="t", environment="staging")
        assert cfg.base_url == "https://api.stg.freewheel.tv"

    def test_invalid_environment_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FreeWheelConnectionConfig(api_token="t", environment="dev")

    def test_default_advertiser_id_optional(self):
        cfg = FreeWheelConnectionConfig(api_token="t")
        assert cfg.default_advertiser_id is None

    def test_default_advertiser_id_accepts_string(self):
        cfg = FreeWheelConnectionConfig(api_token="t", default_advertiser_id="1356511")
        assert cfg.default_advertiser_id == "1356511"

    def test_password_field_marked_secret_in_schema(self):
        schema = FreeWheelConnectionConfig.model_json_schema()
        assert schema["properties"]["password"].get("secret") is True

    def test_api_token_field_marked_secret_in_schema(self):
        schema = FreeWheelConnectionConfig.model_json_schema()
        assert schema["properties"]["api_token"].get("secret") is True

    def test_hosts_table_has_all_envs(self):
        assert "production" in FREEWHEEL_HOSTS
        assert "staging" in FREEWHEEL_HOSTS
        assert FREEWHEEL_HOSTS["sandbox"] == "https://api.sandbox.freewheel.tv"


class TestFreeWheelProductConfig:
    def test_defaults_are_empty(self):
        cfg = FreeWheelProductConfig()
        # Inventory
        assert cfg.site_ids == []
        assert cfg.site_section_ids == []
        assert cfg.video_group_ids == []
        assert cfg.series_ids == []
        assert cfg.ad_unit_package_id is None
        # Audience
        assert cfg.viewership_profile_ids == []
        assert cfg.audience_item_ids == []
        # Content classification
        assert cfg.genre_ids == []
        assert cfg.content_daypart_ids == []
        assert cfg.content_duration_ids == []
        assert cfg.content_territory_ids == []
        assert cfg.language_ids == []
        # Delivery context
        assert cfg.device_type_ids == []
        assert cfg.os_ids == []
        assert cfg.environment_ids == []
        assert cfg.stream_type_ids == []
        assert cfg.subscription_model_ids == []
        # Privacy
        assert cfg.addressability_ids == []
        assert cfg.privacy_signal_ids == []
        assert cfg.tv_rating_ids == []
        # Pricing
        assert cfg.priority is None
        assert cfg.price_model is None
        # Escape hatches
        assert cfg.targeting_profile_id is None
        assert cfg.custom_targeting == {}

    def test_accepts_full_inventory_targeting(self):
        cfg = FreeWheelProductConfig(
            site_ids=[973371, 767268],
            video_group_ids=[1843152716, 1843152488],
            series_ids=[1824258494],
            ad_unit_package_id=51949,
            tv_rating_ids=[11, 12],
            price_model="ACTUAL_ECPM",
            priority=10,
        )
        assert cfg.site_ids == [973371, 767268]
        assert cfg.video_group_ids == [1843152716, 1843152488]
        assert cfg.ad_unit_package_id == 51949
        assert cfg.tv_rating_ids == [11, 12]
        assert cfg.price_model == "ACTUAL_ECPM"
        assert cfg.priority == 10

    def test_audience_fields_accepted(self):
        cfg = FreeWheelProductConfig(
            viewership_profile_ids=[101, 102],
            audience_item_ids=[5001, 5002],
        )
        assert cfg.viewership_profile_ids == [101, 102]
        assert cfg.audience_item_ids == [5001, 5002]

    def test_full_targeting_envelope_round_trips(self):
        """Every targeting dimension persists through model_dump/validate."""
        cfg = FreeWheelProductConfig(
            site_ids=[1, 2],
            video_group_ids=[10],
            series_ids=[100],
            ad_unit_package_id=51949,
            viewership_profile_ids=[1001],
            audience_item_ids=[2001],
            genre_ids=[300],
            content_daypart_ids=[7],
            content_duration_ids=[8],
            content_territory_ids=[9],
            language_ids=[42],
            device_type_ids=[60],
            os_ids=[1],
            environment_ids=[2],
            stream_type_ids=[3],
            subscription_model_ids=[4],
            addressability_ids=[20],
            privacy_signal_ids=[1, 2, 3],
            tv_rating_ids=[11],
            price_model="FIXED_PRICE",
            priority=5,
        )
        dumped = cfg.model_dump()
        rehydrated = FreeWheelProductConfig.model_validate(dumped)
        assert rehydrated.model_dump() == dumped
        assert rehydrated.privacy_signal_ids == [1, 2, 3]
        assert rehydrated.subscription_model_ids == [4]

    def test_advanced_targeting_fields_kept(self):
        """Targeting profile + custom KV are escape hatches; still supported."""
        cfg = FreeWheelProductConfig(
            site_ids=[1],
            targeting_profile_id="tp_123",
            custom_targeting={"genre": ["sports", "news"]},
        )
        assert cfg.targeting_profile_id == "tp_123"
        assert cfg.custom_targeting == {"genre": ["sports", "news"]}
