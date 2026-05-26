"""Tests for list_creative_formats request filtering."""

from unittest.mock import MagicMock, patch

from src.core.creative_agent_registry import FormatFetchResult
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import FormatId, ListCreativeFormatsRequest
from src.core.tools.creative_formats import _list_creative_formats_impl
from tests.helpers.adcp_factories import create_test_format


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

    with (
        patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=registry),
        patch("src.core.database.repositories.uow.TenantConfigUoW", side_effect=RuntimeError("not needed")),
    ):
        response = _list_creative_formats_impl(
            ListCreativeFormatsRequest(
                format_ids=[
                    FormatId(
                        agent_url="https://creative.adcontextprotocol.org",
                        id="video_vast",
                        duration_ms=15000,
                    )
                ]
            ),
            identity,
        )

    assert [(f.format_id.id, f.format_id.duration_ms) for f in response.formats] == [("video_vast", 15000)]
