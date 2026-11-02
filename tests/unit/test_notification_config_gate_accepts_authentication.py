"""The notification_configs gate accepts an authenticated config (salesagent-3cs7o.12, item 1).

``sync_accounts`` hands a ``NotificationConfig``'s authentication block to the registration
gate unchanged, and the gate validates a ``PushNotificationConfig``. The pin spells that block
inline in both schemas, so codegen emits two classes with the same two fields, and pydantic
refuses an instance of one where the other is declared: every authenticated registration
through ``sync_accounts`` failed before any write. ``src/core/schemas/notification.py``
declares one local ``Authentication`` concept (the notification-config spelling, credentials
optional) with ``PushAuthentication`` as its narrowing subtype (the push-config spelling,
credentials required), and the push config adopts a base-typed block by rebuilding the
subtype from its two fields. The model case grades that adoption; the dict-input case pins
the shape the transports deliver.
"""

from src.core.schemas import NotificationConfig
from src.core.tools.accounts import _check_notification_configs
from src.core.webhooks.registration import accept_push_notification_config

#: The pinned schema's ``credentials`` ``minLength`` is 32.
_CREDENTIAL = "secret-token-value-of-at-least-32-characters"
_URL = "https://buyer.example/hook"
_AUTHENTICATION = {"schemes": ["Bearer"], "credentials": _CREDENTIAL}


def test_gate_accepts_an_authenticated_notification_config_model() -> None:
    cfg = NotificationConfig.model_validate(
        {
            "subscriber_id": "sub-1",
            "url": _URL,
            "event_types": ["creative.status_changed"],
            "authentication": _AUTHENTICATION,
        }
    )
    # None is the gate's "acceptable" answer; a list would carry the GateFailure.
    assert _check_notification_configs([cfg]) is None


def test_registration_accepts_a_dict_authentication_block() -> None:
    accepted = accept_push_notification_config(
        {"url": _URL, "authentication": _AUTHENTICATION},
        field_prefix="notification_configs[0]",
    )
    assert accepted.config.authentication is not None
    assert accepted.config.authentication.credentials == _CREDENTIAL
