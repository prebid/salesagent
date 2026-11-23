"""A stored publisher domain and an authorized sign-in domain are host names, refused otherwise.

Both were stored as typed and rendered on admin pages, so markup or a quote in one reached
the DOM. The write boundary now admits only a hostname (a publisher's with an optional
port), through the helpers in ``src.core.http_utils`` and the model validators that call them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import AuthSettings
from src.core.database.models import Publisher, Tenant
from src.core.http_utils import authorized_domain, is_publisher_domain
from src.core.json_validators import checked_authorized_domain
from src.core.operator_errors import InvalidDomainNameError, OperatorRefusal

_NOT_HOSTS = [
    "<img src=x onerror=alert(1)>",
    "a',alert(document.domain),'.example",
    'a"b.example.com',
    "two words.example.com",
    "-leading.example.com",
    "under_score.example.com",
    "kKelvin.example.com",  # the Kelvin sign folds to "k" under str.lower
    "a" * 64 + ".example.com",
    "",
]


@pytest.mark.parametrize(
    "domain", ["news.example.com", "localhost", "127.0.0.1", "news.psatest.com:8443", "news.psatest.com:65535"]
)
def test_a_publisher_domain_is_a_hostname_with_an_optional_port(domain):
    assert is_publisher_domain(domain)


@pytest.mark.parametrize(
    "domain",
    [
        *_NOT_HOSTS,
        "news.example.com:",
        "news.example.com:port",
        "a:1:2",
        "a.test:0",
        "a.test:65536",
        "a.test:99999",
        "a.test:080",
    ],
)
def test_anything_else_is_not_a_publisher_domain(domain):
    assert not is_publisher_domain(domain)


def test_the_publisher_row_refuses_a_domain_that_is_not_a_host():
    assert Publisher(tenant_id="t", domain="news.example.com").domain == "news.example.com"
    with pytest.raises(InvalidDomainNameError):
        Publisher(tenant_id="t", domain="<b>x</b>")


@pytest.mark.parametrize(
    ("value", "stored"), [("Example.COM", "example.com"), (" eng.example.com. ", "eng.example.com")]
)
def test_an_authorized_domain_is_stored_folded(value, stored):
    assert authorized_domain(value) == stored


@pytest.mark.parametrize("value", [*_NOT_HOSTS, "localhost", "example.com:443"])
def test_an_authorized_domain_must_be_a_domain_name(value):
    assert authorized_domain(value) is None
    with pytest.raises(InvalidDomainNameError) as raised:
        checked_authorized_domain(value)
    # An operator refusal: the admin error handler answers 400 with the class's one text.
    assert isinstance(raised.value, OperatorRefusal) and InvalidDomainNameError.STATUS == 400


@pytest.mark.parametrize("value", ["example.com", ["example.com", 42], ["example.com", "b\u00fccher.example"]])
def test_the_column_refuses_a_list_that_is_not_domain_names(value):
    with pytest.raises(InvalidDomainNameError):
        Tenant(authorized_domains=value)


def test_the_column_stores_null_as_an_empty_list():
    assert Tenant(authorized_domains=None).authorized_domains == []


@pytest.mark.parametrize("field", ["super_admin_domains", "tenant_management_domains"])
def test_start_up_refuses_a_domain_setting_that_is_not_domain_names(field):
    assert AuthSettings(**{field: "example.com, eng.example.com"})
    with pytest.raises(ValidationError, match=field.upper()):
        AuthSettings(**{field: "example.com, a',alert(1),'.example"})


def test_a_tenant_refuses_an_authorized_domain_list_holding_a_non_domain():
    tenant = Tenant(authorized_domains=["Example.com"])
    assert tenant.authorized_domains == ["example.com"]
    with pytest.raises(InvalidDomainNameError):
        tenant.authorized_domains = ["example.com", "a',alert(1),'.example"]
