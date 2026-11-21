"""Every e2e origin answers ``GET /health`` the same way, through ``JsonRequestHandler``.

A readback client checks the compose project in the answer to prove it reached its OWN
stack. The three origins used to answer it with three handlers whose bodies had drifted
apart (one added ``status``, one matched the path only without a query string); the handler
now lives once, in ``tests/e2e/_stdlib_json_http.py``. In-process, on loopback, no Docker.
"""

from __future__ import annotations

import json
from urllib.request import urlopen

import pytest

from tests.e2e.counterparty_origin_service import run_counterparty_origin
from tests.e2e.scripted_llm_origin_service import run_scripted_llm_origin
from tests.e2e.webhook_capture_service import run_capture_service


@pytest.mark.parametrize("run", [run_capture_service, run_counterparty_origin, run_scripted_llm_origin])
@pytest.mark.parametrize("path", ["/health", "/health?probe=1"], ids=["bare", "with-query"])
def test_health_reports_this_stack(monkeypatch, run, path):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "origin-health-stack")

    with run(host="127.0.0.1", port=0) as base_url:
        with urlopen(f"{base_url}{path}", timeout=5) as response:  # noqa: S310 - loopback origin
            status, body = response.status, json.loads(response.read())

    assert (status, body) == (200, {"compose_project_name": "origin-health-stack"})
