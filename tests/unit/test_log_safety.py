"""Unit tests for src.core.log_safety redaction helpers (#1617)."""

from __future__ import annotations

from adcp import PushNotificationConfig

from src.core.log_safety import REDACTED, redact_push_notification_config
from tests.factories import PushNotificationConfigFactory

# adcp's PushNotificationConfig enforces a >=32-char credential, so use one that
# clears the bar (the helper never inspects the value, only its presence).
_SECRET = "buyer-webhook-bearer-token-do-not-log"


def _wire_cfg(**overrides):
    """Real SDK ``PushNotificationConfig`` with a credential-bearing typed auth.

    Built via ``model_validate`` exactly as media_buy_create's call site does.
    The double IS the production model, so it cannot drift from the shape the
    helper sees in production — the divergence a hand-rolled stub would hide.
    """
    data: dict = {
        "url": "https://buyer.example/webhook",
        "authentication": {"schemes": ["Bearer"], "credentials": _SECRET},
    }
    data.update(overrides)
    return PushNotificationConfig.model_validate(data)


def _db_cfg(**overrides):
    """Real (unsaved) DB ``PushNotificationConfig`` via the shared factory.

    ``.build()`` touches no session, so this stays a unit test. Same rationale
    as ``_wire_cfg``: a field rename on the DB model breaks these tests
    immediately instead of leaving a five-attribute stub silently stale.
    """
    fields: dict = {
        "id": "pnc_db",
        "url": "https://buyer.example/wh",
        "authentication_type": "bearer",
        "authentication_token": _SECRET,
    }
    fields.update(overrides)
    return PushNotificationConfigFactory.build(**fields)


class TestRedactPushNotificationConfig:
    def test_sdk_wire_model_masks_credentials(self) -> None:
        """SDK PushNotificationConfig: the secret lives in authentication.credentials.

        The helper takes a typed model (callers normalize the wire dict first).
        The SDK model has no ``id`` field, so the redacted view reports ``id=None``.
        """
        out = redact_push_notification_config(_wire_cfg())
        assert _SECRET not in str(out)
        assert out["id"] is None
        # SDK url is a pydantic ``AnyUrl``; it renders as the URL string under %s.
        assert str(out["url"]) == "https://buyer.example/webhook"
        assert out["authentication_type"] == "Bearer"
        assert out["authentication"] == REDACTED

    def test_db_model_shape_masks_token(self) -> None:
        """DB PushNotificationConfig: the secret is the flat authentication_token."""
        out = redact_push_notification_config(_db_cfg())
        assert _SECRET not in str(out)
        assert out["authentication"] == REDACTED
        assert out["authentication_type"] == "bearer"
        assert out["url"] == "https://buyer.example/wh"

    def test_typed_nested_authentication_object_is_read_by_attribute(self) -> None:
        """A typed (non-dict) authentication block still reports scheme + credential.

        Regression pin for the round-2 fix: the nested ``authentication`` block
        used to be read only when it was a ``dict``, so a typed auth object
        reported ``authentication=None`` — "no credential configured" for a
        config that has one. No leak either way (the allowlisted output can't
        carry the secret); this is the has-credential signal. The premise assert
        keeps the pin honest: the SDK model's ``authentication`` must actually
        be a typed object, not a dict, for this test to exercise the attribute
        read.
        """
        cfg = _wire_cfg()
        assert not isinstance(cfg.authentication, dict)
        out = redact_push_notification_config(cfg)
        assert out["authentication"] == REDACTED
        assert out["authentication_type"] == "Bearer"
        assert _SECRET not in str(out)

    def test_no_credential_reports_none_not_redacted(self) -> None:
        """A config with no credential shows authentication=None, not a mask."""
        out = redact_push_notification_config(_db_cfg(authentication_type=None, authentication_token=None))
        assert out["authentication"] is None

    def test_none_returns_empty(self) -> None:
        assert redact_push_notification_config(None) == {}

    def test_never_emits_the_secret_for_any_typed_shape(self) -> None:
        """Belt-and-suspenders: the secret string must not survive redaction.

        Covers both typed shapes the helper accepts — the SDK model's nested
        ``authentication`` object and the DB model's flat ``authentication_token``.
        """
        for cfg in (_wire_cfg(), _db_cfg()):
            assert _SECRET not in str(redact_push_notification_config(cfg)), cfg

    def test_sentinel_is_distinct_from_model_repr_mask(self) -> None:
        """The mask value itself is load-bearing — pinned here, and only here.

        Every other assertion imports ``REDACTED`` instead of hardcoding it, so
        without this test the sentinel could be changed to ``'***'`` with the whole
        suite still green — while silently defeating the per-site deletion oracles
        in tests/integration/test_push_notification_log_redaction.py. Those oracles
        separate "the log went through the redactor" from "the log rendered the DB
        model", and ``PushNotificationConfig.__repr__``
        (src/core/database/models.py) already masks its authentication_token with
        ``'***'``. Same token => indistinguishable => decorative tests.
        """
        assert REDACTED == "***REDACTED***"
        assert REDACTED != "***"
