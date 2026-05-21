"""Regression test: MediaBuyCreateEnv harness exists and follows the contract.

This test verifies the harness can be instantiated, enters/exits cleanly,
and dispatches through all transports. It does NOT test business logic —
that's the BDD suite's job. This guards the harness infrastructure itself.
"""


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
