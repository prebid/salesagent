"""Unit tests for src.core.log_safety redaction helpers (#1617)."""

from __future__ import annotations

from src.core.log_safety import redact_push_notification_config

_SECRET = "buyer-webhook-bearer-token-do-not-log"


class TestRedactPushNotificationConfig:
    def test_wire_dict_shape_masks_credentials(self) -> None:
        """A2A wire dict: the secret lives in authentication.credentials."""
        cfg = {
            "id": "pnc_1",
            "url": "https://buyer.example/webhook",
            "authentication": {"schemes": ["Bearer"], "credentials": _SECRET},
        }
        out = redact_push_notification_config(cfg)
        assert _SECRET not in str(out)
        assert out == {
            "id": "pnc_1",
            "url": "https://buyer.example/webhook",
            "authentication_type": "Bearer",
            "authentication": "***REDACTED***",
        }

    def test_db_model_shape_masks_token(self) -> None:
        """DBPushNotificationConfig-style model: the secret is authentication_token."""

        class _Cfg:
            id = "pnc_2"
            url = "https://buyer.example/wh"
            authentication_type = "bearer"
            authentication_token = _SECRET
            authentication = None

        out = redact_push_notification_config(_Cfg())
        assert _SECRET not in str(out)
        assert out["authentication"] == "***REDACTED***"
        assert out["authentication_type"] == "bearer"
        assert out["url"] == "https://buyer.example/wh"

    def test_no_credential_reports_none_not_redacted(self) -> None:
        """A config with no credential shows authentication=None, not a mask."""
        out = redact_push_notification_config({"id": "p", "url": "https://x/wh"})
        assert out["authentication"] is None

    def test_none_returns_empty(self) -> None:
        assert redact_push_notification_config(None) == {}

    def test_never_emits_the_secret_for_any_shape(self) -> None:
        """Belt-and-suspenders: the secret string must not survive redaction."""
        shapes = [
            {"authentication": {"credentials": _SECRET}},
            {"authentication_token": _SECRET},
            {"url": "https://x", "authentication": {"schemes": ["Bearer"], "credentials": _SECRET}},
        ]
        for cfg in shapes:
            assert _SECRET not in str(redact_push_notification_config(cfg)), cfg
