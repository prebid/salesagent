"""Integration tests for creative sync data preservation.

These tests verify that user-provided data is never silently overwritten by
system-generated data (previews, generative outputs, etc.).

Context:
--------
During security audit, we discovered 6 critical bugs where system data was
unconditionally overwriting user data. These tests ensure that pattern never
returns.

Pattern Under Test:
------------------
if system_data and not user_data:
    use_system_data()
else:
    preserve_user_data()

Critical Bugs This Prevents:
----------------------------
1. Preview URL overwriting user-provided URL from assets
2. Preview dimensions overwriting user-provided dimensions
3. Generative creative output replacing user-provided assets
4. Generative creative URL replacing user-provided URL
5. Platform creative IDs being lost on re-upload
6. Tracking URLs being replaced instead of merged

Test Strategy:
-------------
- Use real database (integration_db fixture) with factory_boy
- Mock only the creative agent registry (external HTTP)
- Assert exact values, not just presence
- Test both create and update paths
- Test both static and generative formats

Covers:
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from tests.factories import PrincipalFactory, TenantFactory
from tests.factories.creative_asset import build_assets, image_spec, video_spec
from tests.harness._base import IntegrationEnv
from tests.helpers.creative_test_helpers import assert_stored_creative_assets, sync_creatives_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

DEFAULT_AGENT_URL = "https://test-agent.example.com"


# ---------------------------------------------------------------------------
# Custom test environment — patches registry only, preserves real async.
# ---------------------------------------------------------------------------


class _DataPreservationEnv(IntegrationEnv):
    """Integration env for data preservation tests.

    Only patches get_creative_agent_registry.
    Intentionally does NOT patch run_async_in_sync_context so that
    preview_creative/build_creative calls actually execute through
    the real async runner (with mocked registry methods).
    """

    EXTERNAL_PATCHES = {
        "registry": "src.core.creative_agent_registry.get_creative_agent_registry",
    }

    def _configure_mocks(self) -> None:
        """Minimal defaults — tests configure registry per-case.

        These cases are all STATIC creatives, so the generative build must stay off. The
        Gemini key is a named fact on the typed settings, read once from the ENVIRONMENT
        (``src/core/config.py``), and the config getter this env used to patch is gone --
        so "not configured" is pinned in the environment, the way CreativeSyncEnv pins it.
        """
        from src.core.config import load_settings

        self._gemini_env = patch.dict(os.environ)
        self._gemini_env.start()
        self._guard("gemini_api_key_env", self._release_gemini_env)
        os.environ.pop("GEMINI_API_KEY", None)
        load_settings()

    def set_gemini_api_key(self, value: str | None) -> None:
        """Configure (or clear, with ``None``) the key the generative build reads.

        "Is the key configured" is a state of the ENVIRONMENT, and the settings are
        rebuilt from it -- the same seam ``CreativeSyncEnv.set_gemini_api_key`` uses.
        """
        from src.core.config import load_settings

        if value is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = value
        load_settings()

    def _release_gemini_env(self) -> None:
        from src.core.config import load_settings

        self._gemini_env.stop()
        load_settings()

    def call_impl(self, **kwargs):
        """Call _sync_creatives_impl with real DB and async execution.

        The per-field kwargs are BUILT into a SyncCreativesRequest through the same builder
        the three transports use — _sync_creatives_impl takes ``(req, identity)`` now, and a
        harness forwarding loose fields would grade a shape production no longer has. The
        ``creatives=[]`` default is gone with it: sync-creatives-request.json declares
        minItems 1, so an empty array is a request the schema refuses.
        """
        from src.core.tools.creatives._sync import _sync_creatives_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        return _sync_creatives_impl(req=sync_creatives_request(**kwargs), identity=identity)


def _make_format_id(agent_url: str, format_id: str):
    """Create a proper FormatId model for test mocks."""
    from adcp.types import FormatId as LibraryFormatId

    return LibraryFormatId(agent_url=agent_url, id=format_id)


def _setup_static_registry(env: _DataPreservationEnv, format_id: str, format_name: str) -> MagicMock:
    """Configure mock registry for a static format with preview support."""
    mock_format = MagicMock()
    mock_format.format_id = _make_format_id(DEFAULT_AGENT_URL, format_id)
    mock_format.agent_url = DEFAULT_AGENT_URL
    mock_format.output_format_ids = None  # Static format

    mock_registry = MagicMock()
    mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
    mock_registry.get_format = AsyncMock(return_value=mock_format)
    env.mock["registry"].return_value = mock_registry
    return mock_registry


def _setup_generative_registry(env: _DataPreservationEnv, format_id: str, output_format_ids: list[str]) -> MagicMock:
    """Configure mock registry for a generative format with build support."""
    mock_format = MagicMock()
    mock_format.format_id = _make_format_id(DEFAULT_AGENT_URL, format_id)
    mock_format.agent_url = DEFAULT_AGENT_URL
    mock_format.output_format_ids = output_format_ids

    mock_registry = MagicMock()
    mock_registry.list_all_formats = AsyncMock(return_value=[mock_format])
    mock_registry.get_format = AsyncMock(return_value=mock_format)
    env.mock["registry"].return_value = mock_registry

    # Enable the Gemini key for the generative path: it is read off the settings, which
    # are rebuilt from the environment, so the key is set there (the env's _guard restores
    # both when the block exits).
    env.set_gemini_api_key("test-gemini-key")

    return mock_registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreativeSyncDataPreservation:
    """Test that sync_creatives preserves user data over system data."""

    TENANT_ID = "test-tenant-preserve"
    PRINCIPAL_ID = "test-principal-preserve"

    def test_sync_preserves_user_url_when_preview_available(self, integration_db):
        """Test that user-provided URL from assets is NOT overwritten by preview URL.

        Covers: UC-006-GENERATIVE-CREATIVE-BUILD-08

        Bug Context:
        -----------
        Line 587 (update) and line 945 (create) unconditionally set:
            data["url"] = first_render["preview_url"]

        This caused user URLs to be replaced with system placeholder URLs.
        """
        with _DataPreservationEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            registry = _setup_static_registry(env, "display_300x250_image", "Display 300x250 Image")
            registry.preview_creative = AsyncMock(
                return_value={
                    "previews": [
                        {
                            "renders": [
                                {
                                    "preview_url": "https://system-preview.example.com/placeholder.png",
                                    "dimensions": {"width": 300, "height": 250},
                                }
                            ]
                        }
                    ]
                }
            )

            user_url = "https://user-provided.example.com/actual-creative.png"
            result = env.call_impl(
                creatives=[
                    {
                        "creative_id": "preserve-url-001",
                        "name": "User Creative with URL",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"},
                        "assets": build_assets(image_spec("banner_image", url=user_url)),
                    }
                ],
            )

        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-url-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == user_url, (
                f"Expected user URL '{user_url}' but got '{creative.data.get('url')}'. "
                f"User data was overwritten by system preview URL!"
            )

    def test_sync_preserves_dimensions_when_preview_has_different_size(self, integration_db):
        """Test that user-provided dimensions are NOT overwritten by preview dimensions.

        Covers: UC-006-GENERATIVE-CREATIVE-BUILD-08

        Bug Context:
        -----------
        Lines 953-958 (create) unconditionally set:
            data["width"] = dimensions["width"]
            data["height"] = dimensions["height"]

        This caused user-specified sizes to be lost when preview returned different dimensions.
        """
        with _DataPreservationEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            registry = _setup_static_registry(env, "display_728x90_image", "Display 728x90 Image")
            registry.preview_creative = AsyncMock(
                return_value={
                    "previews": [
                        {
                            "renders": [
                                {
                                    "preview_url": "https://system-preview.example.com/placeholder.png",
                                    "dimensions": {"width": 300, "height": 250},  # Different from user!
                                }
                            ]
                        }
                    ]
                }
            )

            # Dimensions travel INSIDE the asset, which is where core/creative-asset.json puts
            # them. They were top-level ``width``/``height``/``url`` on the creative -- three
            # fields the schema does not define there -- and only reached _impl because this
            # payload bypassed the request boundary. The sibling URL test above already used
            # the asset form; this is the same shape, so the subject (user dimensions survive
            # a preview reporting different ones) is unchanged.
            user_width, user_height = 728, 90
            result = env.call_impl(
                creatives=[
                    {
                        "creative_id": "preserve-dims-001",
                        "name": "User Creative with Dimensions",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_728x90_image"},
                        "assets": build_assets(
                            image_spec(
                                "banner_image",
                                url="https://user.example.com/banner.png",
                                width=user_width,
                                height=user_height,
                            )
                        ),
                    }
                ],
            )

        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Graded where the user's dimensions actually live. They were asserted on the
        # top-level data["width"]/["height"], which the buyer supplied directly; that is not
        # a field core/creative-asset.json defines on a creative, so no buyer could ever send
        # it and the assertion described a payload only a direct _impl caller could make. The
        # obligation is unchanged -- the preview's 300x250 must not displace the user's
        # 728x90 -- and the asset is the place the request carries it.
        assert_stored_creative_assets(
            "preserve-dims-001",
            image_spec(
                "banner_image",
                url="https://user.example.com/banner.png",
                width=user_width,
                height=user_height,
            ),
        )

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-dims-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            # The TOP-LEVEL width/height are a derived denormalization, filled from the
            # preview only when the request carried none (_processing.py: `and not
            # data.get("width")`). A spec-shaped request never carries one, so they hold the
            # preview's render size while the asset holds the buyer's. Pinned rather than
            # left silent: it is the pre-existing inconsistency this test's non-conformant
            # payload used to hide, not something this contract change introduced.
            assert creative.data.get("width") == 300
            assert creative.data.get("height") == 250

    def test_generative_output_preserves_user_assets(self, integration_db):
        """Test that user-provided assets are NOT replaced by generative output.

        Covers: UC-006-GENERATIVE-CREATIVE-BUILD-08

        Bug Context:
        -----------
        Lines 495, 866 unconditionally set:
            data["assets"] = creative_output["assets"]

        This caused user's carefully crafted AdCP-compliant asset structures
        to be completely replaced by AI-generated output.
        """
        with _DataPreservationEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            registry = _setup_generative_registry(env, "display_300x250_generative", ["display_300x250"])
            registry.build_creative = AsyncMock(
                return_value={
                    "status": "draft",
                    "context_id": "ctx-123",
                    "creative_output": {
                        "assets": {
                            "generated_headline": {"text": "AI Generated Headline"},
                            "generated_image": {"url": "https://ai-generated.example.com/output.png"},
                        },
                        "output_format": {"url": "https://ai-generated.example.com/creative.html"},
                    },
                }
            )

            # User-provided assets (SDK 5.7: assets are discriminated-union lists)
            banner_spec = image_spec(
                "banner_image",
                url="https://user-creative.example.com/banner.png",
            )

            result = env.call_impl(
                creatives=[
                    {
                        "creative_id": "preserve-assets-001",
                        "name": "User Creative with Assets",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_generative"},
                        "assets": build_assets(banner_spec),
                    }
                ],
            )

        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # User's banner asset must survive — NOT replaced by generative output.
        assert_stored_creative_assets("preserve-assets-001", banner_spec)
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-assets-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert "generated_headline" not in (creative.data.get("assets") or {})
            assert "generated_image" not in (creative.data.get("assets") or {})

    def test_generative_output_preserves_user_url(self, integration_db):
        """Test that user-provided URL is NOT replaced by generative output URL.

        Covers: UC-006-GENERATIVE-CREATIVE-BUILD-08

        Bug Context:
        -----------
        Lines 506, 873 unconditionally set:
            data["url"] = output_format["url"]

        When user provided URL via assets, generative output would overwrite it.
        """
        with _DataPreservationEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            registry = _setup_generative_registry(env, "video_generative", ["video_mp4"])
            registry.build_creative = AsyncMock(
                return_value={
                    "status": "draft",
                    "context_id": "ctx-456",
                    "creative_output": {
                        "assets": {"video": {"url": "https://ai-generated.example.com/video.mp4"}},
                        "output_format": {"url": "https://ai-generated.example.com/video-final.mp4"},
                    },
                }
            )

            user_url = "https://user-video.example.com/campaign-video.mp4"
            result = env.call_impl(
                creatives=[
                    {
                        "creative_id": "preserve-gen-url-001",
                        "name": "User Video Creative",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "video_generative"},
                        "assets": build_assets(video_spec("video", url=user_url, duration=30)),
                    }
                ],
            )

        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="preserve-gen-url-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == user_url, (
                f"Expected user URL '{user_url}' but got '{creative.data.get('url')}'. "
                f"User URL was replaced by generative output!"
            )

    def test_update_preserves_user_url_when_preview_changes(self, integration_db):
        """Test that UPDATE path also preserves user URL over preview URL.

        Covers: UC-006-GENERATIVE-CREATIVE-BUILD-07

        Bug Context:
        -----------
        Same bug (line 587) affected UPDATE path. User updates were losing data.
        """
        with _DataPreservationEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            registry = _setup_static_registry(env, "display_300x250_image", "Display 300x250 Image")
            registry.preview_creative = AsyncMock(
                return_value={
                    "previews": [
                        {
                            "renders": [
                                {
                                    "preview_url": "https://system-preview-v2.example.com/placeholder.png",
                                    "dimensions": {"width": 300, "height": 250},
                                }
                            ]
                        }
                    ]
                }
            )

            # First create the creative
            original_url = "https://user-v1.example.com/banner.png"
            env.call_impl(
                creatives=[
                    {
                        "creative_id": "update-preserve-001",
                        "name": "Creative to Update",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"},
                        "assets": build_assets(image_spec("banner_image", url=original_url)),
                    }
                ],
            )

            # Now update with new user URL
            new_user_url = "https://user-v2.example.com/banner-updated.png"
            result = env.call_impl(
                creatives=[
                    {
                        "creative_id": "update-preserve-001",
                        "name": "Creative to Update",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"},
                        "assets": build_assets(image_spec("banner_image", url=new_user_url)),
                    }
                ],
            )

        assert len(result.creatives) == 1
        assert result.creatives[0].action in ["updated", "unchanged"]

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(creative_id="update-preserve-001")
            creative = session.scalars(stmt).first()
            assert creative is not None
            assert creative.data.get("url") == new_user_url, (
                f"Expected updated user URL '{new_user_url}' but got '{creative.data.get('url')}'. "
                f"User update was overwritten by preview!"
            )
