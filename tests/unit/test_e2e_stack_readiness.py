"""Pure readiness decisions; service I/O is graded in integration."""

import importlib
from contextlib import nullcontext
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
    assert readiness.e2e_in_network(host) is expected


@pytest.mark.parametrize(
    "host, published_port, expected",
    [
        ("localhost", 8092, "http://localhost:8092"),
        ("127.0.0.1", 8092, "http://127.0.0.1:8092"),
        ("::1", 8092, "http://[::1]:8092"),
        ("proxy", None, "http://creative-agent:8080"),
        ("proxy", 8092, "http://creative-agent:8080"),
        ("localhost", None, None),
    ],
)
def test_http_service_url_resolves_the_topology(readiness, host, published_port, expected):
    with pytest.raises(readiness.UnpublishedPortError) if expected is None else nullcontext():
        assert readiness.e2e_service_url(readiness.CREATIVE_AGENT, host=host, published_port=published_port) == expected
