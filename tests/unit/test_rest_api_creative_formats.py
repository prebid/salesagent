"""Regression test: REST /api/v1/creative-formats must forward filter params to _impl.

Bug: salesagent-rppx — ListCreativeFormatsBody only had adcp_version, dropping
all filter parameters. The MCP and A2A transports correctly forward filters;
only REST was broken.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.app import app
from tests.factories import PrincipalFactory

_IDENTITY = PrincipalFactory.make_identity(protocol="rest")
_CLIENT = TestClient(app)


class TestRESTCreativeFormatsFilterForwarding:
    """POST /api/v1/creative-formats must forward filter params to _impl."""

    @pytest.mark.parametrize(
        "body,field,expected",
        [
            ({"name_search": "banner"}, "name_search", "banner"),
            ({"type": "display"}, "type", "display"),  # enum coerced
            ({"max_width": 728, "max_height": 90}, "max_width", 728),
            ({"is_responsive": True}, "is_responsive", True),
            ({"min_width": 300}, "min_width", 300),
        ],
        ids=["name_search", "type", "max_width", "is_responsive", "min_width"],
    )
    @patch("src.core.resolved_identity.resolve_identity")
    @patch("src.core.tools.creative_formats._list_creative_formats_impl")
    def test_filter_forwarded_to_impl(self, mock_impl, mock_resolve, body, field, expected):
        """Filter params in POST body must reach _impl via req."""
        from src.core.schemas import ListCreativeFormatsResponse

        mock_resolve.return_value = _IDENTITY
        mock_impl.return_value = ListCreativeFormatsResponse(formats=[])

        _CLIENT.post(
            "/api/v1/creative-formats",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )

        # Extract and verify the req argument passed to _impl
        (_, call_kwargs) = mock_impl.call_args
        req = call_kwargs.get("req") or mock_impl.call_args[0][0]
        assert req is not None, "req must not be None when filters are provided"
        actual = getattr(req, field)
        # Enum fields are coerced by Pydantic — compare .value for enums
        actual_val = actual.value if hasattr(actual, "value") else actual
        assert actual_val == expected
