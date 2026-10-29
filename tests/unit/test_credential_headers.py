import pytest

from tests.helpers.credentials import credential_headers


@pytest.mark.parametrize("scheme", ["Bearer", "Basic"])
def test_credential_headers_supports_explicit_authentication_scheme(scheme):
    assert credential_headers(token="credential", scheme=scheme) == {"Authorization": f"{scheme} credential"}
