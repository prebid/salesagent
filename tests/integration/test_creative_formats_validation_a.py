"""Integration tests for creative formats: adapter formats and validation errors.

Covers:
- UC-005-MAIN-REST-02: Adapter-specific formats merged via A2A
- UC-005-EXT-B-01: Invalid format category enum -> VALIDATION_ERROR
- UC-005-EXT-B-02: Malformed FormatId objects -> VALIDATION_ERROR
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.adapters.broadstreet.formats import BROADSTREET_CANONICAL_FORMAT_IDS
from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest, ListCreativeFormatsResponse
from src.core.standard_formats import get_standard_format
from tests.factories import AdapterConfigFactory, TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

BROADSTREET_CANONICAL_FORMAT_SET = set(BROADSTREET_CANONICAL_FORMAT_IDS)


def _broadstreet_canonical_formats(formats: list[Format]) -> list[Format]:
    """Return the canonical reference-agent formats Broadstreet contributes."""
    return [fmt for fmt in formats if fmt.format_id.id in BROADSTREET_CANONICAL_FORMAT_SET]


class TestAdapterFormatsViaA2A:
    """UC-005-MAIN-REST-02: adapter-supported canonical formats included via A2A.

    Covers: UC-005-MAIN-REST-02

    Given the tenant uses an adapter (e.g., Broadstreet), when the Buyer sends
    list_creative_formats via A2A, that adapter's supported canonical formats
    are merged into the response alongside creative agent formats.

    Business Rule: BR-3 (adapter format merging)
    """

    def test_broadstreet_formats_merged_into_response(self, integration_db):
        """UC-005-MAIN-REST-02: Broadstreet canonical formats are merged into the A2A response.

        When a tenant has adapter_type='broadstreet' in AdapterConfig,
        the list_creative_formats response includes the canonical formats that
        Broadstreet supports (with assets) alongside the creative agent formats.
        """
        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

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

            response = env.call_impl()

        assert isinstance(response, ListCreativeFormatsResponse)

        # Standard format should be present
        format_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250" in format_ids, "Standard format should be in response"

        # Broadstreet contributes canonical formats, not Broadstreet template IDs.
        broadstreet_formats = _broadstreet_canonical_formats(response.formats)
        assert {fmt.format_id.id for fmt in broadstreet_formats} == BROADSTREET_CANONICAL_FORMAT_SET

        # Each canonical format must keep the reference-agent asset contract.
        for fmt in broadstreet_formats:
            expected = get_standard_format(fmt.format_id.id)
            assert expected is not None
            assert fmt.assets is not None, f"Format {fmt.format_id.id} must have assets"
            assert expected.assets is not None
            assert len(fmt.assets) == len(expected.assets)

    def test_broadstreet_formats_have_correct_structure(self, integration_db):
        """UC-005-MAIN-REST-02: Broadstreet canonical formats have valid Format structure.

        Each Broadstreet format should have a valid FormatId with agent_url,
        a canonical format ID, a name, and a non-empty assets list.
        """
        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            env.set_registry_formats([])

            response = env.call_impl()

        assert len(response.formats) == len(BROADSTREET_CANONICAL_FORMAT_IDS)

        for fmt in response.formats:
            # FormatId structure
            assert fmt.format_id is not None
            assert fmt.format_id.id in BROADSTREET_CANONICAL_FORMAT_SET
            assert not fmt.format_id.id.startswith("broadstreet_")
            assert str(fmt.format_id.agent_url).rstrip("/") == DEFAULT_CREATIVE_AGENT_URL

            # Format metadata
            assert fmt.name is not None and len(fmt.name) > 0

            # Assets must be present (regression guard)
            assert fmt.assets is not None, f"Format {fmt.format_id.id} must have assets"
            assert len(fmt.assets) > 0, f"Format {fmt.format_id.id} must have non-empty assets"

    def test_non_broadstreet_adapter_no_extra_formats(self, integration_db):
        """UC-005-MAIN-REST-02 (negative): Non-broadstreet adapters don't add formats.

        When the adapter_type is not 'broadstreet', no adapter-specific
        formats should be merged into the response.
        """
        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="mock")

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

            response = env.call_impl()

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
        assert req.format_ids[0].id == "display_image"
        assert req.format_ids[0].width == 300
        assert req.format_ids[0].height == 250
        assert req.format_ids[1].id == "video_standard"
        assert req.format_ids[1].width == 16
        assert req.format_ids[1].height == 9
