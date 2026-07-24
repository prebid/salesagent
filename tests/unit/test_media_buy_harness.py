"""Regression test: MediaBuyCreateEnv harness exists and follows the contract.

This test verifies the harness can be instantiated, enters/exits cleanly,
and dispatches through all transports. It does NOT test business logic —
that's the BDD suite's job. This guards the harness infrastructure itself.
"""

from unittest.mock import Mock


class TestMediaBuyCreateEnvExists:
    """Verify the harness module can be imported and instantiated."""

    def test_import_harness(self):
        """MediaBuyCreateEnv should be importable."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert MediaBuyCreateEnv is not None

    def test_inherits_integration_env(self):
        """Must inherit from IntegrationEnv (real DB)."""
        from tests.harness._base import IntegrationEnv
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert issubclass(MediaBuyCreateEnv, IntegrationEnv)

    def test_has_external_patches(self):
        """Must declare EXTERNAL_PATCHES for adapter isolation."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert hasattr(MediaBuyCreateEnv, "EXTERNAL_PATCHES")
        assert len(MediaBuyCreateEnv.EXTERNAL_PATCHES) > 0

    def test_has_call_impl(self):
        """Must implement call_impl for direct _impl dispatch."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert hasattr(MediaBuyCreateEnv, "call_impl")
        assert callable(MediaBuyCreateEnv.call_impl)

    def test_has_call_a2a(self):
        """Must implement call_a2a for A2A dispatch."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert hasattr(MediaBuyCreateEnv, "call_a2a")
        assert callable(MediaBuyCreateEnv.call_a2a)

    def test_has_rest_endpoint(self):
        """Must define REST_ENDPOINT."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert hasattr(MediaBuyCreateEnv, "REST_ENDPOINT")
        assert "media-buys" in MediaBuyCreateEnv.REST_ENDPOINT

    def test_has_setup_helpers_it_calls_internally(self):
        """setup_media_buy_data + _configure_mocks reference these; missing = AttributeError on __enter__."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        assert callable(getattr(MediaBuyCreateEnv, "setup_product_chain", None))
        assert callable(getattr(MediaBuyCreateEnv, "_build_mock_context_manager", None))


class TestMediaBuyUpdateEnvExists:
    """Verify the update harness exists."""

    def test_import_harness(self):
        from tests.harness.media_buy_update import MediaBuyUpdateEnv

        assert MediaBuyUpdateEnv is not None


class TestMediaBuyListEnvExists:
    """Verify the list harness exists."""

    def test_import_harness(self):
        from tests.harness.media_buy_list import MediaBuyListEnv

        assert MediaBuyListEnv is not None

    def test_mcp_list_dispatch_delegates_to_run_mcp_client(self):
        """_call_list_mcp forwards to _run_mcp_client with the tool name, response
        type, and request kwargs. This pins the delegation wiring only; the real
        FastMCP client wire is covered by the BDD transport-parity dispatch.
        """
        from src.core.schemas._base import GetMediaBuysResponse
        from tests.harness.media_buy_list import MediaBuyListEnv

        env = object.__new__(MediaBuyListEnv)
        expected = object()
        run_mcp_client = Mock(return_value=expected)
        env._run_mcp_client = run_mcp_client

        result = env._call_list_mcp(media_buy_ids=["mb-001"])

        assert result is expected
        run_mcp_client.assert_called_once_with(
            "get_media_buys",
            GetMediaBuysResponse,
            media_buy_ids=["mb-001"],
        )
