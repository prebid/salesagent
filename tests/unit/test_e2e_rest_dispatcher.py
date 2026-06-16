"""CI-level proofs for the e2e_rest transport plumbing (PR #1420 review follow-ups).

These are pure unit tests — no Docker, no live stack. They pin three behaviours
the reviewer flagged on the in-network test runner:

* ``DISPATCHERS`` covers every ``Transport`` member (no latent ``KeyError``).
* ``RestE2EDispatcher`` exposes ``wire_error_envelope`` on a structured JSON
  error (so ``assert_envelope_shape`` works on this transport) and leaves it
  unset on a non-JSON crash.
* The shared ``"invalid"`` Then-step rejects a server crash (5xx / INTERNAL_ERROR)
  instead of accepting it as a correct validation rejection.

The live e2e_rest pass/xfail behaviour is not exercised by CI; these faithful
unit tests are the CI-level proof.
"""

import httpx
import pytest

from src.core.exceptions import AdCPError, AdCPValidationError
from tests.harness.dispatchers import DISPATCHERS, RestE2EDispatcher
from tests.harness.transport import E2EConfig, Transport


# --------------------------------------------------------------------------- #
# qw8w — DISPATCHERS completeness
# --------------------------------------------------------------------------- #
def test_dispatchers_cover_every_transport():
    """Every Transport member must have a registered dispatcher.

    The E2E_MCP/E2E_A2A enum members predate this PR while ``main``'s
    ``DISPATCHERS`` had no entry for them — a latent ``KeyError`` at dispatch.
    Guard the map so adding a Transport without a dispatcher fails here, not at
    runtime deep in a scenario.
    """
    assert set(DISPATCHERS) == set(Transport), (
        f"DISPATCHERS missing {set(Transport) - set(DISPATCHERS)} / extra {set(DISPATCHERS) - set(Transport)}"
    )


# --------------------------------------------------------------------------- #
# Fakes for RestE2EDispatcher (no real HTTP)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, status_code, json_data=None, text="", content_type="application/json"):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = {"content-type": content_type}

    def json(self):
        if self._json is None:
            raise ValueError("response body is not JSON")
        return self._json


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, endpoint, json=None, headers=None):
        return self._response


class _FakeEnv:
    """Minimal env exposing the REST contract the dispatcher consumes."""

    REST_ENDPOINT = "/mcp/tools/get_delivery"

    def __init__(self, config):
        self.e2e_config = config

    def build_rest_body(self, **kwargs):
        return {"kwargs": kwargs}

    def parse_rest_error(self, status_code, data):
        # Faithful enough: a structured 4xx body maps to a coded error.
        return AdCPValidationError(data.get("message", "invalid"), status_code=status_code)

    def parse_rest_response(self, data):  # pragma: no cover - not hit by error tests
        raise AssertionError("error tests should not reach the success path")


def _dispatch_with(monkeypatch, response):
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(response))
    env = _FakeEnv(E2EConfig(base_url="http://stack", postgres_url=""))
    return RestE2EDispatcher().dispatch(env)


# --------------------------------------------------------------------------- #
# mj0y — wire_error_envelope parity with in-process RestDispatcher
# --------------------------------------------------------------------------- #
def test_wire_error_envelope_set_on_structured_json_error(monkeypatch):
    body = {"message": "bad date_range", "error_code": "VALIDATION_ERROR"}
    result = _dispatch_with(monkeypatch, _FakeResponse(400, json_data=body))

    assert result.wire_error_envelope == body, "structured JSON error must expose the wire envelope"
    assert isinstance(result.error, AdCPValidationError)


def test_wire_error_envelope_none_on_non_json_crash(monkeypatch):
    result = _dispatch_with(monkeypatch, _FakeResponse(500, json_data=None, text="<html>500</html>"))

    assert result.wire_error_envelope is None, "a non-JSON crash has no structured envelope to expose"
    assert isinstance(result.error, AdCPError)
    assert result.error.error_code == "INTERNAL_ERROR"
    assert result.error.status_code == 500


# --------------------------------------------------------------------------- #
# vu2j — the "invalid" Then-step reads the WIRE envelope, not a reconstruction
# --------------------------------------------------------------------------- #
# These pin the wire-based rejection discriminator (_assert_wire_rejection) that
# replaced the lossy reconstructed-status check. The function consumes the
# two-layer envelope the dispatcher captured into ctx["wire_error_envelope"]; we
# feed it real envelope shapes (not a faked transport) and assert which count as a
# genuine rejection of an invalid field. See tests/CLAUDE.md § Error Verification.


def _envelope(code: str, recovery: str) -> dict:
    layer = {"code": code, "message": f"{code} message", "recovery": recovery}
    return {"adcp_error": dict(layer), "errors": [dict(layer)]}


def _run_invalid_with_wire(envelope: dict) -> None:
    from tests.bdd.steps.domain.uc004_delivery import _assert_partition_or_boundary

    _assert_partition_or_boundary({"wire_error_envelope": envelope}, "invalid", "sampling_method")


def test_wire_rejection_accepts_invalid_request():
    """A schema rejection (INVALID_REQUEST / correctable) is a genuine rejection."""
    _run_invalid_with_wire(_envelope("INVALID_REQUEST", "correctable"))


def test_wire_rejection_accepts_terminal_content_error():
    """A terminal error ABOUT the request content (e.g. ACCOUNT_NOT_FOUND) counts."""
    _run_invalid_with_wire(_envelope("ACCOUNT_NOT_FOUND", "terminal"))


def test_wire_rejection_rejects_server_crash():
    """A server crash (INTERNAL_ERROR) is not a rejection of the field."""
    with pytest.raises(AssertionError, match="server crash or auth failure is not a field rejection"):
        _run_invalid_with_wire(_envelope("INTERNAL_ERROR", "transient"))


def test_wire_rejection_rejects_transient_fault():
    """A transient server fault (SERVICE_UNAVAILABLE) is not a rejection."""
    with pytest.raises(AssertionError, match="transient server fault is not a rejection"):
        _run_invalid_with_wire(_envelope("SERVICE_UNAVAILABLE", "transient"))


def test_wire_rejection_rejects_auth_failure():
    """An auth failure must not pass as a field rejection (vacuous-pass guard)."""
    with pytest.raises(AssertionError, match="server crash or auth failure is not a field rejection"):
        _run_invalid_with_wire(_envelope("AUTH_TOKEN_INVALID", "terminal"))
