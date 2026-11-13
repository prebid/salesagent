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
    "host, expected", [("localhost", False), ("127.0.0.1", False), ("::1", False), ("proxy", True)]
)
def test_topology_depends_on_the_address(readiness, host, expected):
    assert readiness.in_network(host) is expected


@pytest.mark.parametrize("host, expected", [("::1", "[::1]:8092"), ("proxy", "proxy:8092")])
def test_http_authority_brackets_ipv6(readiness, host, expected):
    assert readiness._http_authority(host, 8092) == expected
