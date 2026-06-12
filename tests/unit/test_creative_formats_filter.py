"""Tests for list_creative_formats request filtering."""

from unittest.mock import MagicMock, patch

from adcp.validation.schema_validator import validate_response

from src.core.creative_agent_registry import FormatFetchResult
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import FormatId, ListCreativeFormatsRequest
from src.core.tools.creative_formats import _list_creative_formats_impl
from tests.helpers.adcp_factories import create_test_format


def _run_list_creative_formats(formats, req: ListCreativeFormatsRequest):
    identity = ResolvedIdentity(
        principal_id=None,
        tenant={"tenant_id": "test_tenant"},
        auth_type="anonymous",
        account=None,
    )

    async def list_all_formats_with_errors(**_kwargs):
        return FormatFetchResult(formats=formats, errors=[])

    registry = MagicMock()
    registry.list_all_formats_with_errors = list_all_formats_with_errors
    registry._get_tenant_agents.return_value = []

    with (
        patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=registry),
        patch("src.core.database.repositories.uow.TenantConfigUoW", side_effect=RuntimeError("not needed")),
        patch("src.core.tools.creative_formats.get_audit_logger"),
    ):
        return _list_creative_formats_impl(req, identity)


def test_list_creative_formats_filter_matches_duration_parameter():
    formats = [
        create_test_format(
            FormatId(
                agent_url="https://creative.adcontextprotocol.org",
                id="video_vast",
                duration_ms=15000,
            ),
            name="VAST 15s",
            type="video",
        ),
        create_test_format(
            FormatId(
                agent_url="https://creative.adcontextprotocol.org",
                id="video_vast",
                duration_ms=30000,
            ),
            name="VAST 30s",
            type="video",
        ),
    ]
    response = _run_list_creative_formats(
        formats,
        ListCreativeFormatsRequest(
            format_ids=[
                FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id="video_vast",
                    duration_ms=15000,
                )
            ]
        ),
    )

    assert [(f.format_id.id, f.format_id.duration_ms) for f in response.formats] == [("video_vast", 15000)]


def test_list_creative_formats_output_format_filter_respects_parameters():
    formats = [
        create_test_format(
            FormatId(
                agent_url="https://creative.adcontextprotocol.org",
                id="generative_display",
            ),
            name="Generative 728x90",
            output_format_ids=[
                FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id="display_image",
                    width=728,
                    height=90,
                )
            ],
        )
    ]

    response = _run_list_creative_formats(
        formats,
        ListCreativeFormatsRequest(
            output_format_ids=[
                FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id="display_image",
                    width=300,
                    height=250,
                )
            ]
        ),
    )

    assert response.formats == []


def test_list_creative_formats_preserves_supported_macros_for_sdk_schema_validation():
    supported_macros = ["MEDIA_BUY_ID", "CACHEBUSTER", "CUSTOM_PUBLISHER_MACRO"]
    formats = [
        create_test_format(
            "display_macro_test",
            supported_macros=supported_macros,
        )
    ]

    response = _run_list_creative_formats(formats, ListCreativeFormatsRequest())
    payload = response.model_dump(mode="json")
    outcome = validate_response("list_creative_formats", payload)

    assert response.formats[0].supported_macros == supported_macros
    assert payload["formats"][0]["supported_macros"] == supported_macros
    assert outcome.valid is True
    assert outcome.variant == "sync"
