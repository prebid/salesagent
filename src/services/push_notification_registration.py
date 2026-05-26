"""Registration helpers for protocol push-notification webhooks."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import requests
from adcp.webhooks import (
    WebhookChallengeError,
    WebhookDestinationPolicy,
    WebhookDestinationValidationError,
    create_webhook_challenge_payload,
    validate_webhook_challenge_response,
    validate_webhook_destination_url,
)

from src.core.database.repositories.push_notification import (
    PushNotificationConfigRepository,
    PushNotificationConfigWebhookFields,
)
from src.core.database.repositories.uow import PushNotificationUoW
from src.core.exceptions import AdCPValidationError
from src.services.protocol_webhook_service import _normalize_localhost_for_docker
from src.services.webhook_signing import (
    SIGNING_MODE_BOTH,
    SIGNING_MODE_RFC9421,
    SigningConfigurationError,
    load_active_signing_credential,
)

ACCOUNT_NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "creative.status_changed",
        "creative.purged",
        "product.created",
        "product.updated",
        "product.priced",
        "product.removed",
        "signal.created",
        "signal.updated",
        "signal.priced",
        "signal.removed",
        "wholesale_feed.bulk_change",
    }
)


@dataclass(frozen=True, kw_only=True)
class PushNotificationRegistration(PushNotificationConfigWebhookFields):
    """Normalized values stored for a protocol webhook registration."""

    config_id: str | None
    active: bool = True


def normalize_push_notification_config(
    config: Any,
    *,
    account_id: str | None = None,
    default_subscriber_id: str | None = None,
) -> PushNotificationRegistration | None:
    """Normalize AdCP PushNotificationConfig into DB-storable values."""
    config_dict = _config_to_dict(config)
    if not config_dict:
        return None

    url = config_dict.get("url")
    if url is None:
        return None

    authentication = config_dict.get("authentication") or {}
    schemes = authentication.get("schemes") or []
    auth_type = str(schemes[0]) if schemes else None
    credentials = authentication.get("credentials")
    config_id = str(config_dict["id"]) if config_dict.get("id") is not None else None
    subscriber_id = (
        str(config_dict["subscriber_id"]) if config_dict.get("subscriber_id") is not None else default_subscriber_id
    )
    event_types = _normalize_event_types(config_dict.get("event_types"))
    signing_mode = "hmac" if auth_type is not None else "rfc9421"
    return PushNotificationRegistration(
        config_id=config_id,
        url=str(url),
        operation_id=str(config_dict["operation_id"]) if config_dict.get("operation_id") is not None else None,
        account_id=account_id,
        subscriber_id=subscriber_id,
        event_types=event_types,
        authentication_type=auth_type,
        authentication_token=str(credentials) if credentials is not None else None,
        validation_token=str(config_dict["token"]) if config_dict.get("token") is not None else None,
        signing_mode=signing_mode,
        active=bool(config_dict.get("active", True)),
    )


def register_push_notification_config(
    tenant_id: str,
    principal_id: str,
    config: Any,
    *,
    session_id: str | None = None,
) -> PushNotificationRegistration | None:
    """Persist a protocol webhook registration in its own UoW."""
    registration = normalize_push_notification_config(config)
    if registration is None:
        return None

    with PushNotificationUoW(tenant_id) as uow:
        assert uow.push_notifications is not None
        register_push_notification_config_in_repo(
            uow.push_notifications,
            principal_id=principal_id,
            registration=registration,
            session_id=session_id,
        )
    return registration


def register_push_notification_config_in_repo(
    repo: PushNotificationConfigRepository,
    *,
    principal_id: str,
    registration: PushNotificationRegistration,
    session_id: str | None = None,
) -> None:
    """Persist an already-normalized registration using the caller's repository."""
    _validate_registration_url(registration.url)
    _validate_signing_readiness(repo.tenant_id, registration.signing_mode)
    config_id = registration.config_id or _stable_config_id(
        tenant_id=repo.tenant_id,
        principal_id=principal_id,
        account_id=registration.account_id,
        subscriber_id=registration.subscriber_id,
    )
    repo.upsert(
        config_id=config_id,
        principal_id=principal_id,
        url=registration.url,
        operation_id=registration.operation_id,
        account_id=registration.account_id,
        subscriber_id=registration.subscriber_id,
        event_types=registration.event_types,
        authentication_type=registration.authentication_type,
        authentication_token=registration.authentication_token,
        validation_token=registration.validation_token,
        session_id=session_id,
        purpose="catalog_changes",
        signing_mode=registration.signing_mode,
        is_active=registration.active,
    )


def register_account_notification_configs_in_repo(
    repo: PushNotificationConfigRepository,
    *,
    principal_id: str,
    account_id: str,
    configs: list[Any],
) -> list[PushNotificationRegistration]:
    """Persist an account's declarative AdCP notification_configs subscription set."""
    registrations: list[PushNotificationRegistration] = []
    seen_subscribers: set[str] = set()
    for config in configs:
        registration = normalize_push_notification_config(
            config,
            account_id=account_id,
        )
        if registration is None:
            continue
        if registration.subscriber_id is None:
            raise AdCPValidationError(
                "accounts[].notification_configs[].subscriber_id is required",
                details={"account_id": account_id},
            )
        _validate_account_notification_event_types(
            account_id, subscriber_id=registration.subscriber_id, event_types=registration.event_types
        )
        subscriber_id = registration.subscriber_id
        if subscriber_id in seen_subscribers:
            raise AdCPValidationError(
                "Duplicate notification_configs subscriber_id for account",
                details={"account_id": account_id, "subscriber_id": subscriber_id},
            )
        seen_subscribers.add(subscriber_id)
        _validate_registration_url(registration.url)
        _validate_signing_readiness(repo.tenant_id, registration.signing_mode)
        if registration.active:
            _verify_webhook_control(registration)
        registrations.append(registration)

    repo.deactivate_active_for_principal_purpose(
        principal_id=principal_id,
        purpose="catalog_changes",
        account_id=account_id,
        except_config_id=None,
    )
    for registration in registrations:
        register_push_notification_config_in_repo(
            repo,
            principal_id=principal_id,
            registration=registration,
        )
    return registrations


def _config_to_dict(config: Any) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        return config.model_dump(mode="json", exclude_none=True)
    return dict(config)


def _normalize_event_types(event_types: Any) -> list[str] | None:
    if event_types is None:
        return None
    normalized = []
    for event_type in event_types:
        normalized.append(event_type.value if hasattr(event_type, "value") else str(event_type))
    return normalized


def _stable_config_id(
    *,
    tenant_id: str,
    principal_id: str,
    account_id: str | None = None,
    subscriber_id: str | None = None,
) -> str:
    """Derive a stable id-less catalog registration id."""
    if account_id is None and subscriber_id is None:
        digest = hashlib.sha256(f"{tenant_id}\0{principal_id}\0catalog_changes".encode()).hexdigest()
        return f"pnc_{digest[:16]}"
    parts = [tenant_id, principal_id, account_id or "", subscriber_id or "", "catalog_changes"]
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return f"pnc_{digest[:16]}"


def _validate_registration_url(url: str) -> None:
    policy = (
        WebhookDestinationPolicy.local_development()
        if _allow_private_webhook_destinations()
        else WebhookDestinationPolicy.production()
    )
    try:
        validate_webhook_destination_url(url, policy=policy, field="push_notification_config.url")
    except WebhookDestinationValidationError as exc:
        raise AdCPValidationError(
            "Invalid push_notification_config.url",
            details={"field": exc.field or "push_notification_config.url", "reason": exc.reason},
        ) from exc


def _validate_signing_readiness(tenant_id: str, signing_mode: str) -> None:
    if signing_mode not in (SIGNING_MODE_RFC9421, SIGNING_MODE_BOTH):
        return
    try:
        load_active_signing_credential(tenant_id=tenant_id, signing_mode=signing_mode)
    except SigningConfigurationError as exc:
        raise AdCPValidationError(
            "push_notification_config requires an active tenant webhook signing credential",
            details={"signing_mode": signing_mode},
        ) from exc


def _validate_account_notification_event_types(
    account_id: str,
    *,
    subscriber_id: str | None,
    event_types: list[str] | None,
) -> None:
    if not event_types:
        raise AdCPValidationError(
            "accounts[].notification_configs[].event_types is required",
            details={"account_id": account_id, "subscriber_id": subscriber_id},
        )
    invalid = sorted(set(event_types) - ACCOUNT_NOTIFICATION_EVENT_TYPES)
    if invalid:
        raise AdCPValidationError(
            "accounts[].notification_configs[].event_types contains non-account-scoped events",
            details={"account_id": account_id, "subscriber_id": subscriber_id, "invalid_event_types": invalid},
        )


def _verify_webhook_control(registration: PushNotificationRegistration) -> None:
    try:
        payload = create_webhook_challenge_payload(
            account_id=registration.account_id or "",
            subscriber_id=registration.subscriber_id or "",
        )
    except ValueError as exc:
        raise AdCPValidationError(
            "accounts[].notification_configs[] webhook proof-of-control challenge is invalid",
            details={"account_id": registration.account_id, "subscriber_id": registration.subscriber_id},
        ) from exc

    try:
        response = requests.post(
            _normalize_localhost_for_docker(registration.url),
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "AdCP-Sales-Agent/1.0"},
            timeout=5,
        )
        response.raise_for_status()
        response_payload = response.json()
    except Exception as exc:
        raise AdCPValidationError(
            "accounts[].notification_configs[] webhook proof-of-control challenge failed",
            details={"account_id": registration.account_id, "subscriber_id": registration.subscriber_id},
        ) from exc
    try:
        validate_webhook_challenge_response(
            response_payload,
            challenge=payload["challenge"],
            field="accounts[].notification_configs[].url",
            url=registration.url,
        )
    except WebhookChallengeError as exc:
        raise AdCPValidationError(
            "accounts[].notification_configs[] webhook proof-of-control challenge was not echoed",
            details={
                "account_id": registration.account_id,
                "subscriber_id": registration.subscriber_id,
                "reason": exc.reason,
            },
        ) from exc


def _allow_private_webhook_destinations() -> bool:
    if os.getenv("WEBHOOK_ALLOW_PRIVATE_IPS", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("ADCP_AUTH_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return True
    return False
