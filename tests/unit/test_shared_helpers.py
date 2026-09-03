"""Unit tests for shared helper functions extracted during DRY refactoring.

Covers:
- resolve_adapter_id() in src/adapters/constants.py
- _build_package_responses() and _build_create_success() in src/adapters/base.py

"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from src.adapters.constants import _OLD_FIELD_MAP, ADAPTER_PLATFORM_MAP, resolve_adapter_id

# ---------------------------------------------------------------------------
# resolve_adapter_id
# ---------------------------------------------------------------------------


class TestResolveAdapterId:
    """Tests for resolve_adapter_id() — maps adapter name + platform_mappings to ID."""

    def test_gam_nested_advertiser_id(self):
        """GAM adapter resolves advertiser_id from nested google_ad_manager dict."""
        mappings = {"google_ad_manager": {"advertiser_id": "12345"}}
        assert resolve_adapter_id(mappings, "gam") == "12345"

    def test_gam_alias_google_ad_manager(self):
        """'google_ad_manager' adapter name also resolves correctly."""
        mappings = {"google_ad_manager": {"advertiser_id": "99"}}
        assert resolve_adapter_id(mappings, "google_ad_manager") == "99"

    def test_kevel_nested_advertiser_id(self):
        """Kevel adapter resolves advertiser_id from nested kevel dict."""
        mappings = {"kevel": {"advertiser_id": "kv-001"}}
        assert resolve_adapter_id(mappings, "kevel") == "kv-001"

    def test_triton_nested_advertiser_id(self):
        """Triton adapter resolves advertiser_id from nested triton dict."""
        mappings = {"triton": {"advertiser_id": "tri-100"}}
        assert resolve_adapter_id(mappings, "triton") == "tri-100"

    def test_broadstreet_nested_advertiser_id(self):
        """Broadstreet adapter resolves advertiser_id from nested broadstreet dict."""
        mappings = {"broadstreet": {"advertiser_id": "bs-42"}}
        assert resolve_adapter_id(mappings, "broadstreet") == "bs-42"

    def test_mock_nested_advertiser_id(self):
        """Mock adapter resolves advertiser_id from nested mock dict."""
        mappings = {"mock": {"advertiser_id": "mock-1"}}
        assert resolve_adapter_id(mappings, "mock") == "mock-1"

    def test_nested_id_field_fallback(self):
        """When advertiser_id is absent, falls back to 'id' field."""
        mappings = {"kevel": {"id": "fallback-id"}}
        assert resolve_adapter_id(mappings, "kevel") == "fallback-id"

    def test_nested_company_id_field_fallback(self):
        """When advertiser_id and id are absent, falls back to 'company_id'."""
        mappings = {"kevel": {"company_id": "comp-77"}}
        assert resolve_adapter_id(mappings, "kevel") == "comp-77"

    def test_nested_field_priority_order(self):
        """advertiser_id takes priority over id and company_id."""
        mappings = {"mock": {"advertiser_id": "first", "id": "second", "company_id": "third"}}
        assert resolve_adapter_id(mappings, "mock") == "first"

    def test_id_takes_priority_over_company_id(self):
        """id takes priority over company_id when advertiser_id is absent."""
        mappings = {"mock": {"id": "second", "company_id": "third"}}
        assert resolve_adapter_id(mappings, "mock") == "second"

    def test_old_field_name_fallback_gam(self):
        """Falls back to legacy gam_advertiser_id field."""
        mappings = {"gam_advertiser_id": "legacy-gam-99"}
        assert resolve_adapter_id(mappings, "gam") == "legacy-gam-99"

    def test_old_field_name_fallback_kevel(self):
        """Falls back to legacy kevel_advertiser_id field."""
        mappings = {"kevel_advertiser_id": "legacy-kevel-1"}
        assert resolve_adapter_id(mappings, "kevel") == "legacy-kevel-1"

    def test_old_field_name_fallback_triton(self):
        """Falls back to legacy triton_advertiser_id field."""
        mappings = {"triton_advertiser_id": "legacy-triton-5"}
        assert resolve_adapter_id(mappings, "triton") == "legacy-triton-5"

    def test_old_field_name_fallback_broadstreet(self):
        """Falls back to legacy broadstreet_advertiser_id field."""
        mappings = {"broadstreet_advertiser_id": "legacy-bs-3"}
        assert resolve_adapter_id(mappings, "broadstreet") == "legacy-bs-3"

    def test_old_field_name_fallback_mock(self):
        """Falls back to legacy mock_advertiser_id field."""
        mappings = {"mock_advertiser_id": "legacy-mock-7"}
        assert resolve_adapter_id(mappings, "mock") == "legacy-mock-7"

    def test_nested_takes_priority_over_old_field(self):
        """Nested format is preferred over legacy flat field."""
        mappings = {"google_ad_manager": {"advertiser_id": "nested-1"}, "gam_advertiser_id": "legacy-2"}
        assert resolve_adapter_id(mappings, "gam") == "nested-1"

    def test_unknown_adapter_returns_none(self):
        """Unknown adapter name returns None immediately."""
        mappings = {"google_ad_manager": {"advertiser_id": "123"}}
        assert resolve_adapter_id(mappings, "unknown_adapter") is None

    def test_empty_platform_mappings(self):
        """Empty platform_mappings dict returns None."""
        assert resolve_adapter_id({}, "gam") is None

    def test_platform_key_present_but_empty_dict(self):
        """Platform key exists but contains an empty dict; returns None."""
        mappings = {"google_ad_manager": {}}
        assert resolve_adapter_id(mappings, "gam") is None

    def test_none_advertiser_id_returns_none(self):
        """If advertiser_id is explicitly None, returns None."""
        mappings = {"kevel": {"advertiser_id": None}}
        assert resolve_adapter_id(mappings, "kevel") is None

    def test_integer_advertiser_id_coerced_to_string(self):
        """Integer values are coerced to string."""
        mappings = {"mock": {"advertiser_id": 42}}
        assert resolve_adapter_id(mappings, "mock") == "42"

    def test_adapter_platform_map_covers_all_old_field_map_keys(self):
        """Every adapter in _OLD_FIELD_MAP must also be in ADAPTER_PLATFORM_MAP."""
        for adapter_name in _OLD_FIELD_MAP:
            assert adapter_name in ADAPTER_PLATFORM_MAP, (
                f"Adapter '{adapter_name}' in _OLD_FIELD_MAP but missing from ADAPTER_PLATFORM_MAP"
            )


# ---------------------------------------------------------------------------
# _build_package_responses and _build_create_success
#
# build_agent_config (and its _HasAgentFields Protocol) was deleted by
# GH #1802: both registries that used it now dial through the guarded
# MCP seam (call_mcp_tool, via src.core.utils.operator_mcp.call_operator_mcp_tool)
# instead of constructing an adcp AgentConfig for ADCPMultiAgentClient. See
# tests/unit/test_creative_agent_connection_alias.py and
# tests/integration/test_auth_header_propagation.py for the replacement
# coverage (agent_url/auth/auth_header/timeout forwarded to call_mcp_tool).
# ---------------------------------------------------------------------------


def _make_media_package(
    package_id: str = "pkg-1",
) -> MagicMock:
    """Create a minimal MediaPackage-like object for testing response builders."""
    pkg = MagicMock()
    pkg.package_id = package_id
    return pkg


def _make_create_request() -> MagicMock:
    """Create a minimal CreateMediaBuyRequest-like object."""
    return MagicMock()


def _make_adapter_instance() -> Any:
    """Instantiate a concrete AdServerAdapter subclass for testing base methods.

    Uses MockAdServer since it's the simplest concrete subclass.
    We only need the base class methods, so we mock the Principal.
    """
    from src.adapters.mock_ad_server import MockAdServer

    principal = MagicMock()
    principal.principal_id = "test-principal"
    principal.get_adapter_id.return_value = "mock-adv-1"
    return MockAdServer(
        config={"enabled": True},
        principal=principal,
        dry_run=False,
        tenant_id="test-tenant",
    )


class TestBuildPackageResponses:
    """Tests for AdServerAdapter._build_package_responses()."""

    def test_single_package(self):
        """Single package produces a single ResponsePackage with correct fields."""
        adapter = _make_adapter_instance()
        packages = [_make_media_package(package_id="p1")]
        result = adapter._build_package_responses(packages)

        assert len(result) == 1
        assert result[0].package_id == "p1"
        assert result[0].paused is False

    def test_multiple_packages(self):
        """Multiple packages produce corresponding ResponsePackage list."""
        adapter = _make_adapter_instance()
        packages = [
            _make_media_package(package_id="p1"),
            _make_media_package(package_id="p2"),
            _make_media_package(package_id="p3"),
        ]
        result = adapter._build_package_responses(packages)

        assert len(result) == 3
        assert [r.package_id for r in result] == ["p1", "p2", "p3"]

    def test_paused_flag_propagated(self):
        """When paused=True, all ResponsePackages have paused=True."""
        adapter = _make_adapter_instance()
        packages = [_make_media_package()]
        result = adapter._build_package_responses(packages, paused=True)

        assert result[0].paused is True

    def test_empty_packages_list(self):
        """Empty packages list produces empty result."""
        adapter = _make_adapter_instance()
        result = adapter._build_package_responses([])
        assert result == []


class TestBuildCreateSuccess:
    """Tests for AdServerAdapter._build_create_success()."""

    def test_basic_success_response(self):
        """Creates success with media_buy_id, packages, and deadline."""
        adapter = _make_adapter_instance()
        request = _make_create_request()
        packages = [_make_media_package(package_id="p1")]

        result = adapter._build_create_success(
            request=request,
            media_buy_id="mb-123",
            packages=packages,
        )

        assert result.media_buy_id == "mb-123"
        assert len(result.packages) == 1
        assert result.packages[0].package_id == "p1"

    def test_creative_deadline_default_2_days(self):
        """Default creative_deadline is ~2 days from now."""
        adapter = _make_adapter_instance()
        before = datetime.now(UTC)
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],
        )
        after = datetime.now(UTC)

        expected_min = before + timedelta(days=2)
        expected_max = after + timedelta(days=2)
        assert expected_min <= result.creative_deadline <= expected_max

    def test_creative_deadline_custom_days(self):
        """Custom creative_deadline_days is respected."""
        adapter = _make_adapter_instance()
        before = datetime.now(UTC)
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],
            creative_deadline_days=5,
        )
        after = datetime.now(UTC)

        expected_min = before + timedelta(days=5)
        expected_max = after + timedelta(days=5)
        assert expected_min <= result.creative_deadline <= expected_max

    def test_paused_flag_passed_to_packages(self):
        """paused=True propagates to generated package responses."""
        adapter = _make_adapter_instance()
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],
            paused=True,
        )

        assert result.packages[0].paused is True

    def test_pre_built_package_responses_override(self):
        """When package_responses is provided, it is used instead of building from packages."""
        from adcp.types.aliases import Package as ResponsePackage

        adapter = _make_adapter_instance()
        pre_built = [ResponsePackage(package_id="custom-p1", paused=False)]
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],  # these should be ignored
            package_responses=pre_built,
        )

        assert len(result.packages) == 1
        assert result.packages[0].package_id == "custom-p1"

    def test_buyer_ref_not_serialized_on_success_response(self):
        """buyer_ref must never reach the wire on CreateMediaBuySuccess.

        adcp 6.6 (spec 3.1.1) removed buyer_ref from the create-media-buy response type —
        it belongs on the request, not the response. The adcp 6.6 parent therefore no
        longer declares it, so it is not a field on CreateMediaBuySuccess at all and can
        never be serialized. (The obsolete SDK-5.7 exclude=True redeclaration that used to
        neutralize a wrongly-inherited parent field is gone with the 6.6 bump.)
        """
        adapter = _make_adapter_instance()
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],
        )

        # The obligation is on the wire: buyer_ref never serializes. Under adcp 6.6 that
        # holds because it is not a field on the response type at all.
        assert "buyer_ref" not in type(result).model_fields
        assert "buyer_ref" not in result.model_dump()
        assert "buyer_ref" not in result.model_dump_json()

    def test_result_is_create_media_buy_success_type(self):
        """Return type is CreateMediaBuySuccess."""
        from src.core.schemas import CreateMediaBuySuccess

        adapter = _make_adapter_instance()
        result = adapter._build_create_success(
            request=_make_create_request(),
            media_buy_id="mb-1",
            packages=[_make_media_package()],
        )

        assert isinstance(result, CreateMediaBuySuccess)
