"""Behavioral tests for create_media_buy transport boundary serialization.

Covers the push_notification_config serialization obligations introduced in
gh-#1377: both MCP and A2A wrappers must use model_dump(mode='json') so that
Pydantic v2 AnyUrl fields and enum instances are converted to plain Python
strings before reaching _impl and SQLAlchemy String columns.

Obligation IDs:
  UC-002-TRANSPORT-PNC-SERIALIZATION-01  (MCP wrapper)
  UC-002-TRANSPORT-PNC-SERIALIZATION-02  (A2A wrapper)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.schemas import CreateMediaBuyResult
from tests.helpers.adcp_factories import create_test_media_buy_request_dict


def _mock_create_result() -> CreateMediaBuyResult:
    result = MagicMock(spec=CreateMediaBuyResult)
    result.__str__ = lambda self: "mock_result"
    return result


class TestMCPWrapperPncJsonSerialization:
    """MCP wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: gh-#1377 — plain model_dump() preserves AnyUrl objects that
    SQLAlchemy String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01

        When the MCP wrapper serializes PushNotificationConfig to a dict,
        the url field must be a plain str (not a Pydantic AnyUrl object) so
        that SQLAlchemy String columns can persist it without StatementError.
        """
        from adcp import PushNotificationConfig

        from src.core.tools.media_buy_create import create_media_buy

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        mock_result = _mock_create_result()
        req_dict = create_test_media_buy_request_dict()

        mock_ctx = AsyncMock()
        mock_ctx.http = MagicMock()
        mock_ctx.http.headers = {}

        async def _get_state(key: str) -> Any:
            if key == "identity":
                return MagicMock()
            if key == "context_id":
                return "test-ctx-id"
            return None

        mock_ctx.get_state = _get_state

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            try:
                await create_media_buy(
                    brand=req_dict["brand"],
                    packages=req_dict["packages"],
                    start_time=req_dict["start_time"],
                    end_time=req_dict["end_time"],
                    idempotency_key=req_dict["idempotency_key"],
                    push_notification_config=pnc,
                    ctx=mock_ctx,
                )
            except Exception:
                pass  # ToolResult serialization with mock may raise; we only care about _impl args

            mock_impl.assert_called_once()
            forwarded: dict | None = mock_impl.call_args.kwargs.get("push_notification_config")

            assert forwarded is not None, "MCP wrapper did not forward push_notification_config to _impl"
            assert isinstance(forwarded, dict), (
                f"push_notification_config must be a dict, got {type(forwarded).__name__}"
            )

            url = forwarded.get("url")
            assert isinstance(url, str), (
                f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
                "This indicates model_dump() was used instead of model_dump(mode='json'), "
                "which preserves AnyUrl objects and causes SQLAlchemy StatementError (gh-#1377)."
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

        from src.core.tools.media_buy_create import create_media_buy

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        mock_result = _mock_create_result()
        req_dict = create_test_media_buy_request_dict()

        mock_ctx = AsyncMock()
        mock_ctx.http = MagicMock()
        mock_ctx.http.headers = {}

        async def _get_state(key: str) -> Any:
            if key == "identity":
                return MagicMock()
            if key == "context_id":
                return "test-ctx-id"
            return None

        mock_ctx.get_state = _get_state

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            try:
                await create_media_buy(
                    brand=req_dict["brand"],
                    packages=req_dict["packages"],
                    start_time=req_dict["start_time"],
                    end_time=req_dict["end_time"],
                    idempotency_key=req_dict["idempotency_key"],
                    push_notification_config=pnc,
                    ctx=mock_ctx,
                )
            except Exception:
                pass

            mock_impl.assert_called_once()
            forwarded: dict | None = mock_impl.call_args.kwargs.get("push_notification_config")
            assert forwarded is not None

            auth = forwarded.get("authentication", {})
            schemes = auth.get("schemes", [])
            for scheme in schemes:
                assert isinstance(scheme, str), (
                    f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                    f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors (gh-#1377)."
                )


class TestA2AWrapperPncJsonSerialization:
    """A2A wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: gh-#1377 — plain model_dump() preserves AnyUrl objects that
    SQLAlchemy String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_a2a_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper (create_media_buy_raw) receives a PushNotificationConfig
        model instance and serializes it to a dict, the url field must be a plain str
        (not a Pydantic AnyUrl object) so SQLAlchemy String columns can persist it.
        """
        from adcp import PushNotificationConfig

        from src.core.tools.media_buy_create import create_media_buy_raw

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        mock_result = _mock_create_result()
        mock_identity = MagicMock()
        req_dict = create_test_media_buy_request_dict()

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            await create_media_buy_raw(
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                push_notification_config=pnc,
                identity=mock_identity,
            )

            mock_impl.assert_called_once()
            forwarded: dict | None = mock_impl.call_args.kwargs.get("push_notification_config")

            assert forwarded is not None, "A2A wrapper did not forward push_notification_config to _impl"
            assert isinstance(forwarded, dict), (
                f"push_notification_config must be a dict, got {type(forwarded).__name__}"
            )

            url = forwarded.get("url")
            assert isinstance(url, str), (
                f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
                "This indicates model_dump() was used instead of model_dump(mode='json'), "
                "which preserves AnyUrl objects and causes SQLAlchemy StatementError (gh-#1377)."
            )
            assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_a2a_wrapper_passthrough_dict_unchanged(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper receives push_notification_config already as a plain
        dict (the normal A2A JSON path), it must pass it through unchanged without
        re-serializing it.
        """
        from src.core.tools.media_buy_create import create_media_buy_raw

        pnc_dict = {
            "url": "https://buyer.example.com/webhook",
            "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
        }
        mock_result = _mock_create_result()
        mock_identity = MagicMock()
        req_dict = create_test_media_buy_request_dict()

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            await create_media_buy_raw(
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                push_notification_config=pnc_dict,
                identity=mock_identity,
            )

            mock_impl.assert_called_once()
            forwarded: dict | None = mock_impl.call_args.kwargs.get("push_notification_config")

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

        from src.core.tools.media_buy_create import create_media_buy_raw

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        mock_result = _mock_create_result()
        mock_identity = MagicMock()
        req_dict = create_test_media_buy_request_dict()

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            await create_media_buy_raw(
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                push_notification_config=pnc,
                identity=mock_identity,
            )

            mock_impl.assert_called_once()
            forwarded: dict | None = mock_impl.call_args.kwargs.get("push_notification_config")
            assert forwarded is not None

            auth = forwarded.get("authentication", {})
            schemes = auth.get("schemes", [])
            for scheme in schemes:
                assert isinstance(scheme, str), (
                    f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                    f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors (gh-#1377)."
                )
