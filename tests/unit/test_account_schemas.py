"""Unit tests for Account schema extensions.

Verifies that local account schemas correctly extend adcp library types
per pattern #1 (schema inheritance).

beads: salesagent-x79
"""


class TestAccountSchemaInheritance:
    """Account schemas extend library types, not duplicate them."""

    def test_list_accounts_request_extends_library(self):
        from adcp.types import ListAccountsRequest as LibraryType

        from src.core.schemas.account import ListAccountsRequest

        assert issubclass(ListAccountsRequest, LibraryType)

    def test_list_accounts_response_extends_library(self):
        from adcp.types import ListAccountsResponse as LibraryType

        from src.core.schemas.account import ListAccountsResponse

        assert issubclass(ListAccountsResponse, LibraryType)

    def test_sync_accounts_request_extends_library(self):
        from adcp.types import SyncAccountsRequest as LibraryType

        from src.core.schemas.account import SyncAccountsRequest

        assert issubclass(SyncAccountsRequest, LibraryType)


class TestListAccountsResponse:
    """ListAccountsResponse serialization."""

    def test_str_returns_human_readable_count(self):
        from src.core.schemas.account import ListAccountsResponse

        resp = ListAccountsResponse(accounts=[], pagination={"has_more": False})
        assert "0" in str(resp)

    def test_accounts_field_exists(self):
        from src.core.schemas.account import ListAccountsResponse

        assert "accounts" in ListAccountsResponse.model_fields


class TestAccountReExports:
    """Account schemas are re-exported from src.core.schemas."""

    def test_list_accounts_request_importable(self):
        from src.core.schemas import ListAccountsRequest  # noqa: F401

    def test_list_accounts_response_importable(self):
        from src.core.schemas import ListAccountsResponse  # noqa: F401

    def test_sync_accounts_request_importable(self):
        from src.core.schemas import SyncAccountsRequest  # noqa: F401
