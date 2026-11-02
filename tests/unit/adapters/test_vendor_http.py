"""The two rules ``VendorHttpClient`` enforces that no other test can see.

Both are invisible to the adapter suites by construction. The per-client
``timeout`` defaults to the same 30 seconds every existing caller already used,
so a test that asserts ``timeout=30.0`` passes whether the field is wired up or
deleted outright. The overlap rule fires on a key clash, and no production call
site has one — which is the reason to test it here, not a reason to skip it.

The overlap rule's refusal is graded by CHANNEL, not by sentence. Under ADR-010
the buyer-facing text is a function of the code, so an assertion on ``str(exc)``
grades ``CODE_TABLE`` rather than this module: the clashing keys are the
structured fact and live in ``details.rejected_value``, and the buyer-facing
message must name NEITHER the keys nor the vendor's dial coordinates — AdCP 3.1.1
``transport-errors.mdx`` § Security Considerations forbids credentials, tokens
and internal service names in any client-facing field.
"""

import json
from types import MappingProxyType
from unittest.mock import patch

import pytest
from adcp.types import ErrorCode

from src.adapters.vendor_http import VendorHttpClient
from src.core.errors.codes import CODE_TABLE, Recovery
from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError

#: A query-string credential distinctive enough that its absence from every
#: buyer-facing channel is a fact rather than a coincidence.
_CREDENTIAL = "s3cr3t-query-credential"

#: The host the test client dials. An internal service name, so it is subject to
#: the same buyer-facing prohibition as the credential.
_VENDOR_HOST = "vendor.example"


def _client(**overrides):
    kwargs = {"base_url": f"https://{_VENDOR_HOST}", "headers": {}}
    return VendorHttpClient(**{**kwargs, **overrides})


def _assert_clash_refusal(exc: AdCPConfigurationError, *, clashing_keys: list[str]) -> None:
    """Grade one clash refusal on every channel it is allowed to speak through.

    Written once because both clash tests grade the same envelope; only the key
    set differs.
    """
    # Classification. Terminal and 500 are the point of choosing this class: the
    # deployment's own wiring is wrong, so the buyer has no lever and MUST NOT retry.
    assert exc.error_code == ErrorCode.CONFIGURATION_ERROR
    assert exc.recovery == Recovery.TERMINAL
    assert exc.status_code == 500

    # The structured fact: exactly the offending keys, in sorted order. Equality,
    # not membership, is what proves a non-clashing key is never blamed.
    assert isinstance(exc.details, ConfigurationDetails)
    assert exc.details.rejected_value == clashing_keys

    # Buyer-facing text is a function of the code (ADR-010) — not authored here.
    assert exc.message == CODE_TABLE[exc.error_code].message
    assert str(exc) == exc.message

    # AdCP 3.1.1 transport-errors.mdx § Security Considerations: no credential, no
    # internal service name, in any client-facing field.
    wire = json.dumps({"message": exc.message, "details": exc.details.to_wire()})
    assert _CREDENTIAL not in wire, "a query-string credential must never reach the buyer"
    assert _VENDOR_HOST not in wire, "the vendor host must never reach the buyer"
    for key in clashing_keys:
        assert key not in exc.message, "the buyer-facing sentence must not name our wiring"


class TestPerClientTimeout:
    def test_a_clients_own_timeout_reaches_the_seam(self):
        """A non-default timeout is what the seam is called with.

        Asserted with a value that is not 30: the default and the wired-up field
        are indistinguishable at 30, so a test written there grades nothing.
        """
        with patch("src.adapters.vendor_http.send") as mock_send:
            _client(timeout=7.5).call("GET", "/ping")

        assert mock_send.call_args.kwargs["timeout"] == 7.5

    def test_a_client_that_names_no_timeout_still_sends_the_default(self):
        with patch("src.adapters.vendor_http.send") as mock_send:
            _client().call("GET", "/ping")

        assert mock_send.call_args.kwargs["timeout"] == 30.0


class TestParamsMerge:
    def test_client_params_and_per_call_params_arrive_together(self):
        client = _client(params=MappingProxyType({"access_token": "T"}))

        with patch("src.adapters.vendor_http.send") as mock_send:
            client.call("GET", "/records", params={"start_date": "2026-01-01"})

        assert mock_send.call_args.kwargs["params"] == {
            "access_token": "T",
            "start_date": "2026-01-01",
        }

    def test_a_client_param_survives_a_call_that_passes_none(self):
        client = _client(params=MappingProxyType({"access_token": "T"}))

        with patch("src.adapters.vendor_http.send") as mock_send:
            client.call("GET", "/records")

        assert mock_send.call_args.kwargs["params"] == {"access_token": "T"}

    def test_a_caller_cannot_shadow_a_client_level_parameter(self):
        """A key the client already fixed is a defect, not a value to resolve.

        The client-level mapping is where a query-string credential lives, so a
        caller supplying the same key is forging it or shadowing a dial
        coordinate. Picking a winner silently would let either bug ship.
        """
        client = _client(params=MappingProxyType({"access_token": _CREDENTIAL}))

        with patch("src.adapters.vendor_http.send") as mock_send:
            with pytest.raises(AdCPConfigurationError) as exc_info:
                client.call("GET", "/records", params={"access_token": "forged"})

        assert not mock_send.called, "the clash must be caught before any request is sent"
        _assert_clash_refusal(exc_info.value, clashing_keys=["access_token"])

    def test_the_clash_error_names_every_offending_key(self):
        client = _client(params=MappingProxyType({"access_token": _CREDENTIAL, "network": "1"}))

        with patch("src.adapters.vendor_http.send"):
            with pytest.raises(AdCPConfigurationError) as exc_info:
                client.call("GET", "/r", params={"access_token": "x", "network": "2", "ok": "y"})

        # Equality on the structured channel is what excludes the non-clashing "ok":
        # the old substring read of the sentence needed quoted forms to keep "ok" from
        # matching inside "access_token", and graded prose to do it.
        _assert_clash_refusal(exc_info.value, clashing_keys=["access_token", "network"])
        assert "ok" not in exc_info.value.details.rejected_value, "a key that does not clash must not be blamed"


class TestImmutability:
    def test_a_caller_cannot_mutate_the_clients_params_through_the_sent_mapping(self):
        """The mapping handed to the seam is a copy, not the client's own."""
        client = _client(params=MappingProxyType({"access_token": "T"}))

        with patch("src.adapters.vendor_http.send") as mock_send:
            client.call("GET", "/r", params={"a": "1"})

        mock_send.call_args.kwargs["params"]["access_token"] = "mutated"
        assert client.params["access_token"] == "T"
