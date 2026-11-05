"""Pure readiness decisions; service I/O is graded in integration."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def readiness():
    path = Path(__file__).parents[1] / "e2e" / "stack_readiness.py"
    assert path.is_file(), "E2E readiness must have one shared owner"
    return importlib.import_module("tests.e2e.stack_readiness")


@pytest.mark.parametrize(
    "record, expected",
    [
        ({"Health": "healthy"}, "healthy"),
        ({"Health": "unhealthy", "State": "running (healthy)"}, "unhealthy"),
        ({"Health": "starting"}, "starting"),
        ({"Health": "", "State": "running (unhealthy)"}, "unhealthy"),
        ({"Health": "", "Status": "Up 2 minutes (unhealthy)"}, "unhealthy"),
        ({"Health": "", "State": "running (healthy)"}, "healthy"),
        ({"Status": "Up 2 minutes (healthy)"}, "healthy"),
        ({"State": "running", "Status": "Up 2 minutes (healthy)"}, "healthy"),
        ({"State": "running (starting)"}, "starting"),
        ({"Health": "invented", "State": "running (healthy)"}, "unknown"),
        ({"State": "running"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_health_is_a_closed_verdict(readiness, record, expected):
    assert readiness.health_of(record).value == expected


@pytest.mark.parametrize(
    "host, expected", [("localhost", False), ("127.0.0.1", False), ("::1", False), ("proxy", True)]
)
def test_topology_depends_on_the_address(readiness, host, expected):
    assert readiness.in_network(host) is expected


@pytest.mark.parametrize("host, expected", [("::1", "[::1]:8092"), ("proxy", "proxy:8092")])
def test_http_authority_brackets_ipv6(readiness, host, expected):
    assert readiness._http_authority(host, 8092) == expected
