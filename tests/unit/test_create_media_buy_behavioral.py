"""Behavioral tests for create_media_buy transport boundary serialization.

Covers the push_notification_config serialization obligations: both MCP and A2A
wrappers must use model_dump(mode='json') so that Pydantic v2 AnyUrl fields and
enum instances are converted to plain Python strings before reaching _impl and
SQLAlchemy String columns.

Obligation IDs:
  UC-002-TRANSPORT-PNC-SERIALIZATION-01  (MCP wrapper)
  UC-002-TRANSPORT-PNC-SERIALIZATION-02  (A2A wrapper)
"""

from __future__ import annotations

import pytest

from tests.helpers.create_media_buy_capture import capture_a2a_forwarded_pnc, capture_mcp_forwarded_pnc


class TestMCPWrapperPncJsonSerialization:
    """MCP wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: plain model_dump() preserves AnyUrl objects that SQLAlchemy
    String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01

        When the MCP wrapper serializes PushNotificationConfig to a dict,
        the url field must be a plain str (not a Pydantic AnyUrl object) so
        that SQLAlchemy String columns can persist it without StatementError.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)

        assert forwarded is not None, "MCP wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, dict), f"push_notification_config must be a dict, got {type(forwarded).__name__}"

        url = forwarded.get("url")
        assert isinstance(url, str), (
            f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
            "This indicates model_dump() was used instead of model_dump(mode='json'), "
            "which preserves AnyUrl objects and causes SQLAlchemy StatementError."
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_mcp_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01

        When the MCP wrapper serializes PushNotificationConfig, enum fields
        such as authentication.schemes must be plain strings, not enum instances,
        so SQLAlchemy can persist them without coercion errors.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)
        assert forwarded is not None

        auth = forwarded.get("authentication", {})
        schemes = auth.get("schemes", [])
        for scheme in schemes:
            assert isinstance(scheme, str), (
                f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors."
            )


class TestA2AWrapperPncJsonSerialization:
    """A2A wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: plain model_dump() preserves AnyUrl objects that SQLAlchemy
    String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_a2a_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper (create_media_buy_raw) receives a PushNotificationConfig
        model instance and serializes it to a dict, the url field must be a plain str
        (not a Pydantic AnyUrl object) so SQLAlchemy String columns can persist it.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)

        assert forwarded is not None, "A2A wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, dict), f"push_notification_config must be a dict, got {type(forwarded).__name__}"

        url = forwarded.get("url")
        assert isinstance(url, str), (
            f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
            "This indicates model_dump() was used instead of model_dump(mode='json'), "
            "which preserves AnyUrl objects and causes SQLAlchemy StatementError."
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_a2a_wrapper_passthrough_dict_unchanged(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper receives push_notification_config already as a plain
        dict (the normal A2A JSON path), it must pass it through unchanged without
        re-serializing it.
        """
        pnc_dict = {
            "url": "https://buyer.example.com/webhook",
            "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
        }
        forwarded = await capture_a2a_forwarded_pnc(pnc_dict)

        assert forwarded is not None
        assert isinstance(forwarded, dict)
        assert forwarded["url"] == "https://buyer.example.com/webhook"
        assert forwarded["authentication"]["schemes"] == ["Bearer"]

    @pytest.mark.asyncio
    async def test_a2a_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper serializes a PushNotificationConfig model, enum
        fields such as authentication.schemes must be plain strings.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)
        assert forwarded is not None

        auth = forwarded.get("authentication", {})
        schemes = auth.get("schemes", [])
        for scheme in schemes:
            assert isinstance(scheme, str), (
                f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors."
            )
