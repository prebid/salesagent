"""What a deployment declares as its own host, for the one tenant it bootstraps for itself.

A pure function over settings, so a unit test is the right level.

Its third rung is the reason this module exists. ``localhost:{port}`` is a true answer for a
developer's box and a FICTION on a production install, which would then hold a row that looks
configured and serves nothing — a wrong stored host, which is worse than an absent tenant and
is the same class of defect as #1845 one step earlier. So the rung is gated on not being
production, and a production install declaring neither ``ADCP_AGENT_URL`` nor
``SALES_AGENT_DOMAIN`` gets ``None``: the bootstraps then create no default tenant and log
why, which is the end state that install already has, minus the misleading row.

Patched on the settings OBJECT, never the environment: production reads a field off settings
built once, so an env write lands only before the first read.
"""

from unittest.mock import patch

import pytest

from src.core.agent_identity import deployment_virtual_host
from src.core.config import get_settings


@pytest.fixture
def runtime():
    return get_settings().runtime


def _as_production(runtime, value: bool):
    """``is_production`` is a read-only property, so it is patched on the CLASS."""
    return patch.object(type(runtime), "is_production", property(lambda _self: value))


def test_an_explicit_agent_url_contributes_its_netloc_not_the_whole_url(runtime):
    """The column stores a HOST. The scheme is re-derived from it on the way back out."""
    with patch.object(runtime, "adcp_agent_url", "https://agent.example.com:9443/a2a"):
        assert deployment_virtual_host() == "agent.example.com:9443"


def test_the_sales_agent_domain_answers_when_no_agent_url_is_declared(runtime):
    with (
        patch.object(runtime, "adcp_agent_url", None),
        patch.object(runtime, "sales_agent_domain", "sales.example.com"),
    ):
        assert deployment_virtual_host() == "sales.example.com"


def test_an_agent_url_outranks_the_domain(runtime):
    """Order matters: the more specific declaration wins."""
    with (
        patch.object(runtime, "adcp_agent_url", "https://explicit.example.com"),
        patch.object(runtime, "sales_agent_domain", "sales.example.com"),
    ):
        assert deployment_virtual_host() == "explicit.example.com"


def test_a_development_install_declaring_nothing_gets_localhost(runtime):
    """True for a developer's box: this is where the process actually answers."""
    with (
        patch.object(runtime, "adcp_agent_url", None),
        patch.object(runtime, "sales_agent_domain", None),
        patch.object(runtime, "adcp_sales_port", 8080),
        _as_production(runtime, False),
    ):
        assert deployment_virtual_host() == "localhost:8080"


def test_a_production_install_declaring_nothing_gets_no_host_at_all(runtime):
    """THE assertion this module exists for.

    Returning ``localhost`` here would store a host that looks configured and serves nothing.
    ``None`` means the bootstraps create no default tenant, which is what such an install
    already effectively has — it answers on no Host — without a row claiming otherwise.
    """
    with (
        patch.object(runtime, "adcp_agent_url", None),
        patch.object(runtime, "sales_agent_domain", None),
        _as_production(runtime, True),
    ):
        assert deployment_virtual_host() is None


def test_an_agent_url_with_no_netloc_is_not_passed_through_as_a_host(runtime):
    """A path-only setting yields no host rather than a nonsense one."""
    with (
        patch.object(runtime, "adcp_agent_url", "/a2a"),
        patch.object(runtime, "sales_agent_domain", None),
        _as_production(runtime, True),
    ):
        assert deployment_virtual_host() is None
