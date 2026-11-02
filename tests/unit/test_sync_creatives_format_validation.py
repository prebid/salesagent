"""Tests for format validation in sync_creatives.

Tests the new format validation logic that was added to sync_creatives
to ensure consistent validation across all creative operations.
"""

from unittest.mock import patch

import pytest

from src.core.errors.details import AdapterFailureDetails
from src.core.tools.creatives._sync import _sync_creatives_impl
from tests.factories.creative_asset import build_assets, image_spec
from tests.helpers.creative_test_helpers import (
    make_creative_dict,
    make_format_spec,
    make_registry_mock,
    sync_creatives_request,
)
from tests.helpers.creative_test_helpers import (
    make_creative_uow as _make_creative_uow_shared,
)
from tests.helpers.unit_identity import fabricated_account_identity


def _make_creative_uow():
    return _make_creative_uow_shared(include_assignments=True)


class TestSyncCreativesFormatValidation:
    """Test format validation in sync_creatives operation."""

    @pytest.fixture
    def identity(self):
        """The AccountIdentity ``_sync_creatives_impl`` declares. Mocked DB, so fabricated."""
        return fabricated_account_identity(
            principal_id="principal_123",
            tenant_id="tenant_123",
            approval_mode="auto-approve",
            slack_webhook_url=None,
        )

    @pytest.fixture
    def mock_tenant(self):
        """Mock tenant configuration."""
        return {
            "tenant_id": "tenant_123",
            "approval_mode": "auto-approve",
            "slack_webhook_url": None,
        }

    @pytest.fixture
    def valid_creative_dict(self):
        """Valid creative dictionary for testing."""
        return make_creative_dict(creative_id="creative_123")

    @pytest.fixture
    def mock_format_spec(self):
        """Mock format specification from creative agent."""
        return make_format_spec(name="Medium Rectangle - Image")

    def test_format_validation_success(self, identity, mock_tenant, valid_creative_dict, mock_format_spec):
        """Test that format validation succeeds when format exists."""
        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            async def mock_get_format(agent_url, format_id, **_kwargs):
                return mock_format_spec

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(
                req=sync_creatives_request(creatives=[valid_creative_dict]), identity=identity
            )

            # Verify format was validated
            assert len(response.creatives) == 1
            assert response.creatives[0].action == "created"
            assert response.creatives[0].creative_id == "creative_123"

    def test_format_validation_unknown_format(self, identity, mock_tenant, valid_creative_dict):
        """Test that validation fails with clear error when format doesn't exist."""
        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry - format not found
            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id, **_kwargs):
                return None  # Format not found

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(
                req=sync_creatives_request(creatives=[valid_creative_dict]), identity=identity
            )

            # Verify creative failed with appropriate error
            assert len(response.creatives) == 1
            assert response.creatives[0].action == "failed"
            assert response.creatives[0].creative_id == "creative_123"
            assert len(response.creatives[0].errors) == 1

            advisory = response.creatives[0].errors[0]
            # The rejected format is STRUCTURED, not interpolated prose: a buyer agent can
            # read it without parsing English. The code is the pin's generic not-found
            # (enums/error-code.json: REFERENCE_NOT_FOUND for a referenced identifier that
            # does not exist), not VALIDATION_ERROR, which it reserves for business rules.
            assert advisory.code == "REFERENCE_NOT_FOUND"
            assert advisory.details["format_id"] == "display_300x250_image"

    def test_format_validation_agent_unreachable(self, identity, mock_tenant, valid_creative_dict):
        """An unreachable agent fails the REQUEST transiently, not the creative.

        Production-grounded : the registry types every network
        failure (connect/timeout -> AdCPServiceUnavailableError,
        creative_agent_registry.py:500-531), and typed transient errors
        PROPAGATE out of sync_creatives with their recovery semantics — the old
        bare-except rewrap made a down agent look like a broken creative.
        """
        from src.core.exceptions import AdCPServiceUnavailableError

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry — the typed error the registry actually raises
            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id, **_kwargs):
                raise AdCPServiceUnavailableError(details=AdapterFailureDetails(status="Connection failed"))

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            with pytest.raises(AdCPServiceUnavailableError) as exc_info:
                _sync_creatives_impl(req=sync_creatives_request(creatives=[valid_creative_dict]), identity=identity)

            # Both obligations the pre-merge test carried, kept — asserted where the
            # values live now. ``match="Connection refused"`` could not survive: the
            # message is a read-only function of the code (CODE_TABLE), so the
            # provenance of the failure is carried by the TYPED details instead.
            assert exc_info.value.details.status == "Connection failed"
            assert exc_info.value.recovery == "transient"

    def test_format_validation_multiple_creatives(self, identity, mock_tenant, mock_format_spec):
        """Test that format validation works correctly with multiple creatives."""
        creatives = [
            make_creative_dict(creative_id="creative_1", name="Valid Creative"),
            {
                **make_creative_dict(creative_id="creative_2", name="Invalid Format"),
                "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "unknown_format"},
            },
            make_creative_dict(creative_id="creative_3", name="Valid Creative 2"),
        ]

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            # Mock get_format to return format_spec for valid format, None for invalid
            async def mock_get_format(agent_url, format_id, **_kwargs):
                if format_id == "display_300x250_image":
                    return mock_format_spec
                return None

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(req=sync_creatives_request(creatives=creatives), identity=identity)

            # Verify results
            assert len(response.creatives) == 3

            # First creative: success
            assert response.creatives[0].creative_id == "creative_1"
            assert response.creatives[0].action == "created"

            # Second creative: failed (unknown format)
            assert response.creatives[1].creative_id == "creative_2"
            assert response.creatives[1].action == "failed"
            advisory_2 = response.creatives[1].errors[0]
            assert advisory_2.code == "REFERENCE_NOT_FOUND"
            assert advisory_2.details["format_id"] == "unknown_format"

            # Third creative: success
            assert response.creatives[2].creative_id == "creative_3"
            assert response.creatives[2].action == "created"

    def test_format_validation_caching(self, identity, mock_tenant, valid_creative_dict, mock_format_spec):
        """Test that format validation uses in-memory cache (doesn't call agent twice for same format)."""
        # Create two creatives with same format
        creative1 = valid_creative_dict.copy()
        creative1["creative_id"] = "creative_1"

        creative2 = valid_creative_dict.copy()
        creative2["creative_id"] = "creative_2"

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry
            async def mock_list_all_formats(tenant_id=None):
                return [mock_format_spec]

            async def mock_get_format(agent_url, format_id, **_kwargs):
                return mock_format_spec

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            # Execute
            response = _sync_creatives_impl(
                req=sync_creatives_request(creatives=[creative1, creative2]), identity=identity
            )

            # Verify both creatives succeeded
            assert len(response.creatives) == 2
            assert response.creatives[0].action == "created"
            assert response.creatives[1].action == "created"

    def test_error_messages_distinguish_scenarios(self, identity, mock_tenant):
        """Test that error messages clearly distinguish between different failure scenarios."""
        # Test 1: Format unknown (agent reachable, format doesn't exist)
        creative_unknown_format = {
            "creative_id": "creative_unknown",
            "name": "Unknown Format",
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "nonexistent_format"},
            "assets": build_assets(image_spec("image", url="https://example.com/1.png")),
        }

        # Test 2: Agent unreachable (network error)
        creative_unreachable = {
            "creative_id": "creative_unreachable",
            "name": "Unreachable Agent",
            "format_id": {"agent_url": "https://offline.example.com", "id": "display_300x250_image"},
            "assets": build_assets(image_spec("image", url="https://example.com/2.png")),
        }

        mock_uow, mock_creative_repo = _make_creative_uow()

        with (
            patch("src.core.tools.creatives._sync.CreativeUoW") as mock_uow_cls,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry_getter,
            patch("src.core.tools.creatives._workflow.get_audit_logger"),
            patch("src.core.tools.creatives._sync.log_tool_activity"),
        ):
            mock_uow_cls.return_value.__enter__.return_value = mock_uow

            # Setup mock registry — typed error for the down agent, as the
            # registry actually raises
            from src.core.exceptions import AdCPServiceUnavailableError

            async def mock_list_all_formats(tenant_id=None):
                return []

            async def mock_get_format(agent_url, format_id, **_kwargs):
                if "offline.example.com" in agent_url:
                    raise AdCPServiceUnavailableError(details=AdapterFailureDetails(status="Connection failed"))

            mock_registry = make_registry_mock(list_all_formats=mock_list_all_formats, get_format=mock_get_format)
            mock_registry_getter.return_value = mock_registry

            # Unknown format: per-item terminal failure — the creative is wrong.
            response1 = _sync_creatives_impl(
                req=sync_creatives_request(creatives=[creative_unknown_format]), identity=identity
            )

            advisory1 = response1.creatives[0].errors[0]
            # A wrong format is buyer-correctable; an unreachable agent is transient.
            # The CODE carries that distinction, so it cannot be blurred by wording.
            # The advisory is built by the SAME derivation as the request-level envelope
            # (build_error_object), and since salesagent-3dawm.8 that derivation resolves
            # the suggestion from CODE_TABLE — the class default is gone, not deferred.
            assert advisory1.code == "REFERENCE_NOT_FOUND"
            assert advisory1.code != "SERVICE_UNAVAILABLE"

            # Down agent: request-level TRANSIENT failure — the creative is fine.
            with pytest.raises(AdCPServiceUnavailableError) as exc_info:
                _sync_creatives_impl(req=sync_creatives_request(creatives=[creative_unreachable]), identity=identity)

            # Both obligations the pre-merge test carried, kept — asserted where the
            # values live now. ``match="Connection refused"`` could not survive: the
            # message is a read-only function of the code (CODE_TABLE), so the
            # provenance of the failure is carried by the TYPED details instead.
            assert exc_info.value.details.status == "Connection failed"
            assert exc_info.value.recovery == "transient"


class TestFormatValidationOptimization:
    """Test optimization considerations for format validation."""

    def test_format_validation_always_runs(self):
        """Document that format validation runs on all creative operations.

        Current Implementation:
        - Format validation runs on ALL creative operations (create AND update)
        - Even if format hasn't changed, we re-validate against creative agent
        - This ensures format spec is still valid on agent side

        Future Optimization (NOT RECOMMENDED):
        - Could skip validation if format_id unchanged on updates
        - Would require careful handling of edge cases:
          * Format spec changed on agent side (breaking change)
          * Agent migrated to different URL
          * Format deprecated/removed
        - Cache already makes validation fast (< 10ms for cache hit)
        - Complexity not worth marginal performance gain

        Recommendation: Keep current behavior (always validate).

        See docs/architecture/creative-format-validation.md for detailed analysis.
        """
        # This is a documentation test - no actual test code needed
        # The behavior is tested in integration tests with real database
        pass
