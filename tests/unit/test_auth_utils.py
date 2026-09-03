from types import SimpleNamespace

from src.core.auth_utils import get_principal_from_token
from src.core.oauth_service import OAuthTokenValidationError


class _ScalarsResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _FakeSession:
    def scalars(self, _stmt):
        return _ScalarsResult(SimpleNamespace(principal_id="principal_123"))


def test_get_principal_from_token_falls_back_to_legacy_token_with_dots(monkeypatch):
    monkeypatch.setattr(
        "src.core.auth_utils.validate_mcp_access_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(OAuthTokenValidationError("invalid")),
    )
    monkeypatch.setattr("src.core.auth_utils.execute_with_retry", lambda operation: operation(_FakeSession()))

    principal_id, tenant = get_principal_from_token("legacy.token.with.dots", tenant_id="tenant_123")

    assert principal_id == "principal_123"
    assert tenant is None
