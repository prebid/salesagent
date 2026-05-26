import pytest

from src.core.exceptions import AdCPValidationError
from src.services.push_notification_registration import (
    PushNotificationRegistration,
    _verify_webhook_control,
    normalize_push_notification_config,
    register_account_notification_configs_in_repo,
    register_push_notification_config_in_repo,
)


class RecordingPushNotificationRepo:
    tenant_id = "tenant_1"

    def __init__(self) -> None:
        self.calls = []
        self.deactivations = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)

    def deactivate_active_for_principal_purpose(self, **kwargs):
        self.deactivations.append(kwargs)
        return 0


def _account_registration() -> PushNotificationRegistration:
    return PushNotificationRegistration(
        config_id="pnc_1",
        url="https://example.com/catalog-webhooks",
        account_id="acc_1",
        subscriber_id="sub_1",
        event_types=["product.updated"],
    )


def test_normalizes_legacy_authenticated_push_notification_config() -> None:
    registration = normalize_push_notification_config(
        {
            "id": "pnc_sync_accounts",
            "url": "https://example.com/webhooks",
            "operation_id": "sync-op-1",
            "token": "client-validation-token",
            "authentication": {
                "schemes": ["HMAC-SHA256"],
                "credentials": "shared-secret",
            },
        }
    )

    assert registration is not None
    assert registration.config_id == "pnc_sync_accounts"
    assert registration.url == "https://example.com/webhooks"
    assert registration.operation_id == "sync-op-1"
    assert registration.authentication_type == "HMAC-SHA256"
    assert registration.authentication_token == "shared-secret"
    assert registration.validation_token == "client-validation-token"
    assert registration.signing_mode == "hmac"


def test_normalizes_default_push_notification_config_to_rfc9421() -> None:
    registration = normalize_push_notification_config(
        {
            "url": "https://example.com/webhooks",
            "operation_id": "sync-op-1",
            "token": "client-validation-token",
        }
    )

    assert registration is not None
    assert registration.authentication_type is None
    assert registration.signing_mode == "rfc9421"


def test_register_push_notification_config_in_repo_upserts_normalized_values() -> None:
    repo = RecordingPushNotificationRepo()
    registration = normalize_push_notification_config(
        {
            "id": "pnc_sync_accounts",
            "url": "https://example.com/webhooks",
            "operation_id": "sync-op-2",
            "authentication": {"schemes": ["Bearer"], "credentials": "bearer-token"},
        }
    )

    assert registration is not None
    register_push_notification_config_in_repo(
        repo,
        principal_id="agent_1",
        registration=registration,
        session_id="session_1",
    )

    assert repo.calls == [
        {
            "config_id": "pnc_sync_accounts",
            "principal_id": "agent_1",
            "url": "https://example.com/webhooks",
            "operation_id": "sync-op-2",
            "account_id": None,
            "subscriber_id": None,
            "event_types": None,
            "authentication_type": "Bearer",
            "authentication_token": "bearer-token",
            "validation_token": None,
            "session_id": "session_1",
            "purpose": "catalog_changes",
            "signing_mode": "hmac",
            "is_active": True,
        }
    ]
    assert repo.deactivations == []


def test_sdk_push_notification_config_gets_stable_generated_id() -> None:
    config = {
        "url": "https://example.com/webhooks",
        "operation_id": "catalog-refresh-1",
        "token": "client-validation-token",
    }

    first = normalize_push_notification_config(config)
    second = normalize_push_notification_config(config)

    assert first is not None
    assert second is not None
    assert first.config_id is None
    assert second.config_id is None


def test_generated_id_is_stable_per_principal_not_url_or_operation() -> None:
    repo = RecordingPushNotificationRepo()
    first = normalize_push_notification_config(
        {
            "url": "https://example.com/webhooks",
            "operation_id": "catalog-refresh-1",
            "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
        }
    )
    second = normalize_push_notification_config(
        {
            "url": "https://www.example.com/webhooks",
            "operation_id": "catalog-refresh-2",
            "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
        }
    )

    assert first is not None
    assert second is not None
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=first)
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=second)

    assert repo.calls[0]["config_id"] == repo.calls[1]["config_id"]
    assert repo.calls[0]["config_id"].startswith("pnc_")
    assert repo.calls[1]["url"] == "https://www.example.com/webhooks"
    assert repo.calls[1]["operation_id"] == "catalog-refresh-2"
    assert repo.deactivations == []


def test_generated_id_is_scoped_by_principal() -> None:
    repo = RecordingPushNotificationRepo()
    registration = normalize_push_notification_config(
        {
            "url": "https://example.com/webhooks",
            "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
        }
    )

    assert registration is not None
    register_push_notification_config_in_repo(repo, principal_id="agent_1", registration=registration)
    register_push_notification_config_in_repo(repo, principal_id="agent_2", registration=registration)

    assert repo.calls[0]["config_id"] != repo.calls[1]["config_id"]


def test_account_notification_configs_are_account_scoped_and_replace_active_set(monkeypatch) -> None:
    repo = RecordingPushNotificationRepo()
    monkeypatch.setattr(
        "src.services.push_notification_registration._verify_webhook_control", lambda registration: None
    )

    register_account_notification_configs_in_repo(
        repo,
        principal_id="agent_1",
        account_id="acc_1",
        configs=[
            {
                "subscriber_id": "sub_1",
                "url": "https://example.com/catalog-webhooks",
                "event_types": ["product.updated", "signal.updated"],
                "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
            }
        ],
    )

    assert repo.calls[0]["account_id"] == "acc_1"
    assert repo.calls[0]["subscriber_id"] == "sub_1"
    assert repo.calls[0]["event_types"] == ["product.updated", "signal.updated"]
    assert repo.calls[0]["config_id"].startswith("pnc_")
    assert repo.deactivations == [
        {
            "principal_id": "agent_1",
            "purpose": "catalog_changes",
            "account_id": "acc_1",
            "except_config_id": None,
        }
    ]


def test_verify_webhook_control_posts_sdk_challenge_and_accepts_echo(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"challenge": captured["payload"]["challenge"]}

    def post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("src.services.push_notification_registration.requests.post", post)

    _verify_webhook_control(_account_registration())

    assert captured["url"] == "https://example.com/catalog-webhooks"
    assert captured["payload"]["type"] == "webhook.challenge"
    assert captured["payload"]["account_id"] == "acc_1"
    assert captured["payload"]["subscriber_id"] == "sub_1"
    assert captured["payload"]["challenge"].startswith("wch_")
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["timeout"] == 5


def test_verify_webhook_control_rejects_missing_echo(monkeypatch) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {}

    monkeypatch.setattr("src.services.push_notification_registration.requests.post", lambda *args, **kwargs: Response())

    with pytest.raises(AdCPValidationError, match="was not echoed") as exc:
        _verify_webhook_control(_account_registration())

    assert exc.value.details["reason"] == "missing_echo"


def test_verify_webhook_control_rejects_invalid_challenge_inputs() -> None:
    registration = PushNotificationRegistration(
        config_id="pnc_1",
        url="https://example.com/catalog-webhooks",
        account_id=None,
        subscriber_id="sub_1",
        event_types=["product.updated"],
    )

    with pytest.raises(AdCPValidationError, match="challenge is invalid"):
        _verify_webhook_control(registration)


def test_account_notification_configs_reject_missing_event_types() -> None:
    repo = RecordingPushNotificationRepo()

    with pytest.raises(AdCPValidationError, match="event_types is required"):
        register_account_notification_configs_in_repo(
            repo,
            principal_id="agent_1",
            account_id="acc_1",
            configs=[
                {
                    "subscriber_id": "sub_1",
                    "url": "https://example.com/catalog-webhooks",
                    "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
                }
            ],
        )


def test_account_notification_configs_reject_media_buy_event_types() -> None:
    repo = RecordingPushNotificationRepo()

    with pytest.raises(AdCPValidationError, match="non-account-scoped"):
        register_account_notification_configs_in_repo(
            repo,
            principal_id="agent_1",
            account_id="acc_1",
            configs=[
                {
                    "subscriber_id": "sub_1",
                    "url": "https://example.com/catalog-webhooks",
                    "event_types": ["scheduled"],
                    "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "shared-secret"},
                }
            ],
        )


def test_rfc9421_registration_requires_signing_credential(monkeypatch) -> None:
    def fail_load(*, tenant_id, signing_mode):
        from src.services.webhook_signing import SigningConfigurationError

        raise SigningConfigurationError("missing key")

    monkeypatch.setattr("src.services.push_notification_registration.load_active_signing_credential", fail_load)
    registration = normalize_push_notification_config({"url": "https://example.com/webhooks"})
    assert registration is not None

    with pytest.raises(AdCPValidationError, match="signing credential"):
        register_push_notification_config_in_repo(
            RecordingPushNotificationRepo(),
            principal_id="agent_1",
            registration=registration,
        )


def test_register_rejects_private_webhook_url(monkeypatch) -> None:
    monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)

    with pytest.raises(AdCPValidationError, match="push_notification_config.url"):
        registration = normalize_push_notification_config({"url": "http://169.254.169.254/latest/meta-data"})
        assert registration is not None
        register_push_notification_config_in_repo(
            RecordingPushNotificationRepo(),
            principal_id="agent_1",
            registration=registration,
        )


def test_register_rejects_http_webhook_url_in_production(monkeypatch) -> None:
    monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)

    with pytest.raises(AdCPValidationError, match="push_notification_config.url") as exc:
        registration = normalize_push_notification_config({"url": "http://example.com/webhooks"})
        assert registration is not None
        register_push_notification_config_in_repo(
            RecordingPushNotificationRepo(),
            principal_id="agent_1",
            registration=registration,
        )

    assert exc.value.details["reason"] == "https_required"
