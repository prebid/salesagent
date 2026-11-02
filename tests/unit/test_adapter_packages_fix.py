"""Every adapter maps the ad server's response back onto the packages it was asked to place.

One obligation, three adapters: whatever Kevel, Triton or Xandr calls its unit of
delivery (a flight, a flight, a line item), ``create_media_buy`` must hand the tool back
one package per requested package, under the ids the request used — none dropped, none
duplicated, none invented.

This file used to be titled "proving ALL adapters now return packages with package_id,
fixing the 'Adapter did not return package_id' error", and each test asserted
``packages is not None``, ``isinstance(packages, list)`` and a per-package
``hasattr(pkg, "package_id")``. None of those can fail any more: the carrier split made
``AdapterCreateResult.packages`` a required ``list[ResponsePackage]`` whose
``package_id`` is a required ``str``, so pydantic refuses such a result at construction.
The bug became a type invariant, and the assertions that named it graded pydantic.

Two other tests are gone with them. Each adapter had a second test that built it without
its required config (Kevel without ``network_id``, Triton without ``auth_token``) to
reach a dry-run branch that answered without calling the vendor. No such branch exists —
both fields are required at construction — so those tests asserted the same package-id
obligation as their live siblings through a mode the design does not have. The refusal
they now run into is graded below instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from src.adapters.base import AdapterCreateRequest
from src.adapters.kevel import Kevel
from src.adapters.triton_digital import TritonDigital
from src.adapters.vendor_http import VendorHttpClient
from src.adapters.xandr import XandrAdapter
from src.core.exceptions import AdCPConfigurationError
from src.core.schemas import FormatId, MediaPackage


@pytest.fixture
def mock_principal():
    """Mock principal for testing."""
    principal = Mock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    return principal


@pytest.fixture
def sample_request():
    """What the create path hands an adapter.

    The carrier, not a ``CreateMediaBuyRequest``: an adapter takes the buy to place.
    ``total_budget`` is already summed by the caller (the tool sums the request's
    packages; the approval replay reads the row's column), so an adapter that divides
    by it — Xandr's daily-budget split — gets a number rather than a null.
    """
    return AdapterCreateRequest(brand={"domain": "testbrand.com"}, total_budget=Decimal("10000.00"))


@pytest.fixture
def sample_packages():
    """Sample packages list."""
    return [
        MediaPackage(
            package_id="pkg_001",
            name="Package 1",
            delivery_type="guaranteed",
            impressions=10000,
            cpm=5.0,
            format_ids=[FormatId(agent_url="https://test.com", id="display_300x250")],
        ),
        MediaPackage(
            package_id="pkg_002",
            name="Package 2",
            delivery_type="guaranteed",
            impressions=20000,
            cpm=7.5,
            format_ids=[FormatId(agent_url="https://test.com", id="display_728x90")],
        ),
    ]


def _vendor_reply(payload: dict) -> Mock:
    """One ``VendorHttpClient.call`` result — these paths read only ``.json()``."""
    reply = Mock()
    reply.json.return_value = payload
    return reply


@contextmanager
def _kevel(mock_principal) -> Iterator[Kevel]:
    """Kevel over a stubbed vendor client: a campaign, then one flight per package.

    The client is REPLACED rather than patched: it is a frozen slotted dataclass, so
    ``patch.object`` on the instance attribute is refused.
    """
    mock_principal.get_adapter_id = Mock(return_value="123")
    adapter = Kevel(
        config={"api_key": "test_key", "base_url": "https://api.kevel.com", "network_id": "456"},
        principal=mock_principal,
        tenant_id="tenant_123",
    )
    adapter._vendor = Mock(spec=VendorHttpClient)
    adapter._vendor.call.side_effect = [
        _vendor_reply({"Id": 999}),
        _vendor_reply({"Id": 111}),
        _vendor_reply({"Id": 222}),
    ]
    yield adapter


@contextmanager
def _triton(mock_principal) -> Iterator[TritonDigital]:
    """Triton over a stubbed vendor client, same shape as Kevel's with lowercase ids."""
    mock_principal.get_adapter_id = Mock(return_value="123")
    adapter = TritonDigital(
        config={
            "api_key": "test_key",
            "base_url": "https://api.tritondigital.com",
            "auth_token": "test_auth_token",
        },
        principal=mock_principal,
        tenant_id="tenant_123",
    )
    adapter._vendor = Mock(spec=VendorHttpClient)
    adapter._vendor.call.side_effect = [
        _vendor_reply({"id": 888}),
        _vendor_reply({"id": 333}),
        _vendor_reply({"id": 444}),
    ]
    yield adapter


@contextmanager
def _xandr(mock_principal) -> Iterator[XandrAdapter]:
    """Xandr over a stubbed ``_make_request``: an insertion order, then two line items.

    Only ``create_media_buy`` has been moved to the current adapter API (see the notes
    in ``src/adapters/xandr.py``), so the class is still abstract and the remaining
    abstract methods are stubbed to let it be built at all.
    """
    mock_principal.platform_mappings = {"xandr": {"advertiser_id": "789"}}
    with patch.multiple("src.adapters.xandr.XandrAdapter", __abstractmethods__=set()):
        adapter = XandrAdapter(
            config={
                "api_endpoint": "https://api.appnexus.com",
                "username": "test_user",
                "password": "test_pass",
                "member_id": "123",
            },
            principal=mock_principal,
            tenant_id="test_tenant",
        )
        adapter.add_creative_assets = Mock()
        adapter.associate_creatives = Mock()
        adapter.check_media_buy_status = Mock()
        adapter.update_media_buy_performance_index = Mock()
        adapter._log_operation = Mock()
        adapter.token = "test_token"
        adapter.token_expiry = datetime.now() + timedelta(hours=2)
        with patch.object(adapter, "_make_request") as make_request:
            make_request.side_effect = [
                {"response": {"insertion-order": {"id": 555}}},
                {"response": {"line-item": {"id": 666}}},
                {"response": {"line-item": {"id": 777}}},
            ]
            yield adapter


@pytest.mark.parametrize("build_adapter", [_kevel, _triton, _xandr], ids=["kevel", "triton", "xandr"])
def test_adapter_returns_one_package_per_requested_package(
    build_adapter, mock_principal, sample_request, sample_packages
):
    """The ad server's response is mapped back onto the requested package ids."""
    start_time = datetime.now()
    end_time = start_time + timedelta(days=30)

    with build_adapter(mock_principal) as adapter:
        response = adapter.create_media_buy(
            request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
        )

    assert len(response.packages) == len(sample_packages)
    assert {pkg.package_id for pkg in response.packages} == {pkg.package_id for pkg in sample_packages}


def test_kevel_without_a_network_id_refuses_to_be_built(mock_principal):
    """No network id, no Kevel adapter: there is no offline mode to fall back to."""
    mock_principal.get_adapter_id = Mock(return_value="123")

    with pytest.raises(AdCPConfigurationError) as exc:
        Kevel(
            config={"api_key": "test_key", "base_url": "https://api.kevel.com"},
            principal=mock_principal,
            tenant_id="tenant_123",
        )

    assert exc.value.field == "network_id"


def test_triton_without_an_auth_token_refuses_to_be_built(mock_principal):
    """No auth token, no Triton adapter: there is no offline mode to fall back to."""
    mock_principal.get_adapter_id = Mock(return_value="123")

    with pytest.raises(AdCPConfigurationError) as exc:
        TritonDigital(
            config={"api_key": "test_key", "base_url": "https://api.tritondigital.com"},
            principal=mock_principal,
            tenant_id="tenant_123",
        )

    assert exc.value.field == "auth_token"
