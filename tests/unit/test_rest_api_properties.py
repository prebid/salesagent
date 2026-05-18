"""Regression test: REST /api/v1/authorized-properties must forward filter params.

Bug: salesagent-9763 — Same pattern as salesagent-rppx. ListAuthorizedPropertiesBody
only had adcp_version, dropping property_tags and publisher_domains filters.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.app import app
from tests.factories import PrincipalFactory

_IDENTITY = PrincipalFactory.make_identity(protocol="rest")
_CLIENT = TestClient(app)


class TestRESTPropertiesFilterForwarding:
    """POST /api/v1/authorized-properties must forward filter params to _impl."""

    @pytest.mark.parametrize(
        "body,field,expected",
        [
            ({"property_tags": ["premium"]}, "property_tags", ["premium"]),
            ({"publisher_domains": ["example.com"]}, "publisher_domains", ["example.com"]),
        ],
        ids=["property_tags", "publisher_domains"],
    )
    @patch("src.core.resolved_identity.resolve_identity")
    @patch("src.core.tools.properties._list_authorized_properties_impl")
    def test_filter_forwarded_to_impl(self, mock_impl, mock_resolve, body, field, expected):
        """Filter params in POST body must reach _impl via req."""
        from src.core.schemas import ListAuthorizedPropertiesResponse

        mock_resolve.return_value = _IDENTITY
        mock_impl.return_value = ListAuthorizedPropertiesResponse(publisher_domains=[])

        _CLIENT.post(
            "/api/v1/authorized-properties",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )

        (_, call_kwargs) = mock_impl.call_args
        req = call_kwargs.get("req") or mock_impl.call_args[0][0]
        assert req is not None, "req must not be None when filters are provided"
        assert getattr(req, field) == expected
