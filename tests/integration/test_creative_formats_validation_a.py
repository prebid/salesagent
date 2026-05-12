"""Integration tests for creative formats: adapter formats and validation errors.

Covers:
- UC-005-MAIN-REST-02: Adapter-specific formats merged via A2A
- UC-005-EXT-B-01: Invalid format category enum -> VALIDATION_ERROR
- UC-005-EXT-B-02: Malformed FormatId objects -> VALIDATION_ERROR
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
from src.core.schemas import FormatId, ListCreativeFormatsRequest, ListCreativeFormatsResponse
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestAdapterFormatsViaA2A:
    """UC-005-MAIN-REST-02: adapter-specific formats included via A2A.

    Covers: UC-005-MAIN-REST-02

    Given the tenant uses an adapter (e.g., Broadstreet) that provides
    additional format templates, when the Buyer sends list_creative_formats
    via A2A, adapter-specific formats are merged into the response alongside
    creative agent formats.

    Business Rule: BR-3 (adapter format merging)
    """

    def test_broadstreet_formats_merged_into_response(self, integration_db):
        """UC-005-MAIN-REST-02: Broadstreet adapter formats are merged into the A2A response.

        When a tenant has adapter_type='broadstreet' in AdapterConfig,
        the list_creative_formats response includes all 8 real Broadstreet
        template formats (with assets) alongside the standard creative agent formats.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            with get_db_session() as session:
                config = AdapterConfig(
                    tenant_id="test_tenant",
                    adapter_type="broadstreet",
                )
                session.add(config)
                session.commit()

            from src.core.schemas import Format

            standard_format = Format(
                format_id=FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id="display_300x250",
                ),
                name="Display 300x250",
                type="display",
                is_standard=True,
            )
            env.set_registry_formats([standard_format])

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)

        # Standard format should be present
        format_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250" in format_ids, "Standard format should be in response"

        # All 8 real Broadstreet formats should be present with assets
        broadstreet_formats = [f for f in response.formats if "broadstreet" in str(f.format_id.agent_url)]
        assert len(broadstreet_formats) == len(BROADSTREET_TEMPLATES), (
            f"Expected {len(BROADSTREET_TEMPLATES)} Broadstreet formats, got {len(broadstreet_formats)}"
        )

        # Each Broadstreet format must have assets (regression guard for _make_asset fix)
        for fmt in broadstreet_formats:
            tmpl_id = fmt.format_id.id.replace("broadstreet_", "")
            tmpl = BROADSTREET_TEMPLATES[tmpl_id]
            expected_assets = len(tmpl.get("required_assets", [])) + len(tmpl.get("optional_assets", []))
            assert fmt.assets is not None, f"Template {tmpl_id} must have assets"
            assert len(fmt.assets) == expected_assets, (
                f"Template {tmpl_id}: expected {expected_assets} assets, got {len(fmt.assets)}"
            )

    def test_broadstreet_formats_have_correct_structure(self, integration_db):
        """UC-005-MAIN-REST-02: Broadstreet adapter formats have valid Format structure.

        Each Broadstreet format should have a valid FormatId with agent_url,
        a name, type=display, is_standard=False, and non-empty assets list.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            with get_db_session() as session:
                config = AdapterConfig(
                    tenant_id="test_tenant",
                    adapter_type="broadstreet",
                )
                session.add(config)
                session.commit()

            env.set_registry_formats([])

            response = env.call_a2a()

        assert len(response.formats) == len(BROADSTREET_TEMPLATES)

        for fmt in response.formats:
            # FormatId structure
            assert fmt.format_id is not None
            assert fmt.format_id.id.startswith("broadstreet_")
            assert "broadstreet://" in str(fmt.format_id.agent_url)

            # Format metadata
            assert fmt.name is not None and len(fmt.name) > 0
            # is_standard is exclude=True (internal-only) — not visible through A2A serialization

            # Assets must be present (regression guard)
            assert fmt.assets is not None, f"Format {fmt.format_id.id} must have assets"
            assert len(fmt.assets) > 0, f"Format {fmt.format_id.id} must have non-empty assets"

    def test_non_broadstreet_adapter_no_extra_formats(self, integration_db):
        """UC-005-MAIN-REST-02 (negative): Non-broadstreet adapters don't add formats.

        When the adapter_type is not 'broadstreet', no adapter-specific
        formats should be merged into the response.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            with get_db_session() as session:
                config = AdapterConfig(
                    tenant_id="test_tenant",
                    adapter_type="mock",
                )
                session.add(config)
                session.commit()

            from src.core.schemas import Format

            standard_format = Format(
                format_id=FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id="display_300x250",
                ),
                name="Display 300x250",
                type="display",
                is_standard=True,
            )
            env.set_registry_formats([standard_format])

            response = env.call_a2a()

        # Only the standard format should be present
        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "display_300x250"


class TestInvalidFormatCategoryEnum:
    """UC-005-EXT-B-01: invalid format category enum -> VALIDATION_ERROR.

    Covers: UC-005-EXT-B-01

    When the Buyer calls list_creative_formats with type='invalid_category',
    the response is an error with code VALIDATION_ERROR. The error must
    identify the invalid type field, explain why it failed, and suggest
    valid FormatCategory enum values.
    """

    def test_invalid_type_raises_validation_error(self, integration_db):
        """UC-005-EXT-B-01: unknown fields raise ValidationError at request construction.

        Pydantic extra="forbid" rejects unknown fields at request construction
        time, producing a clear error before the request reaches _impl.
        """
        with pytest.raises(ValidationError):
            ListCreativeFormatsRequest(type="invalid_category")

    def test_unknown_field_rejected(self, integration_db):
        """UC-005-EXT-B-01: unknown fields are rejected by extra=forbid.

        The type field was removed in adcp 3.12. Passing it now triggers
        extra_forbidden validation error.
        """
        with pytest.raises(ValidationError):
            ListCreativeFormatsRequest(type="display")

    def test_valid_filters_via_mcp_works(self, integration_db):
        """UC-005-EXT-B-01: MCP wrapper correctly handles valid filter parameters.

        The type filter was removed in adcp 3.12. Verify the MCP wrapper
        handles remaining valid filters (e.g., name_search) without error.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            # Pass a valid filter — name_search for nonexistent name → empty result
            response = env.call_mcp(name_search="nonexistent_format_xyz")

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == 0

    def test_each_valid_category_accepted(self, integration_db):
        """UC-005-EXT-B-01 (positive counterpart): all valid format filter values are accepted.

        Ensures the validation correctly accepts valid ListCreativeFormatsRequest construction.
        """
        # type filter was removed from ListCreativeFormatsRequest in adcp 3.12
        req = ListCreativeFormatsRequest()
        assert req is not None


class TestMalformedFormatIdObjects:
    """UC-005-EXT-B-02: malformed FormatId objects -> VALIDATION_ERROR.

    Covers: UC-005-EXT-B-02

    When the Buyer calls list_creative_formats with malformed format_ids
    (e.g., missing agent_url), the response is a VALIDATION_ERROR that
    identifies the malformed FormatId field.
    """

    def test_missing_agent_url_raises_validation_error(self, integration_db):
        """UC-005-EXT-B-02: FormatId without agent_url raises ValidationError.

        The FormatId schema requires both 'agent_url' and 'id' fields.
        Missing agent_url triggers a clear validation error.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(format_ids=[{"id": "test_format"}])

        errors = exc_info.value.errors()
        # Should have at least one error about agent_url
        agent_url_errors = [e for e in errors if any("agent_url" in str(loc) for loc in e["loc"])]
        assert len(agent_url_errors) > 0, f"Should have error about missing agent_url. Errors: {errors}"

    def test_missing_id_raises_validation_error(self, integration_db):
        """UC-005-EXT-B-02: FormatId without id field raises ValidationError.

        The FormatId schema requires the 'id' field.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(format_ids=[{"agent_url": "https://example.com/agent"}])

        errors = exc_info.value.errors()
        id_errors = [e for e in errors if any("id" in str(loc) for loc in e["loc"])]
        assert len(id_errors) > 0, f"Should have error about missing id. Errors: {errors}"

    def test_empty_format_ids_dict_raises_validation_error(self, integration_db):
        """UC-005-EXT-B-02: Empty dict as FormatId raises ValidationError.

        An empty dict is missing both required fields (agent_url, id).
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(format_ids=[{}])

        errors = exc_info.value.errors()
        assert len(errors) >= 1, "Should have at least one validation error"

    def test_invalid_agent_url_format_raises_validation_error(self, integration_db):
        """UC-005-EXT-B-02: FormatId with non-URL agent_url raises ValidationError.

        The agent_url field must be a valid URL.
        """
        with pytest.raises(ValidationError):
            ListCreativeFormatsRequest(format_ids=[{"agent_url": "not_a_url", "id": "test_format"}])

    def test_malformed_format_ids_via_mcp_raises_adcp_error(self, integration_db):
        """UC-005-EXT-B-02: MCP rejects malformed FormatId (missing agent_url).

        The request is rejected regardless of which layer catches it:
        TypeAdapter (dev) or our validation code (production after fallback).
        """
        from tests.harness.assertions import assert_rejected
        from tests.harness.transport import Transport

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(Transport.MCP, format_ids=[{"id": "no_agent_url"}])
            assert_rejected(result, field="agent_url", reason="Field required")

    def test_valid_format_ids_accepted(self, integration_db):
        """UC-005-EXT-B-02 (positive counterpart): well-formed FormatId objects are accepted.

        Ensures format_ids with both agent_url and id are valid.
        """
        req = ListCreativeFormatsRequest(
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_16x9"),
            ]
        )
        assert len(req.format_ids) == 2
        assert req.format_ids[0].id == "display_300x250"
        assert req.format_ids[1].id == "video_16x9"
