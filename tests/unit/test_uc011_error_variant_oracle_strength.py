"""Regression test: the error-variant "no accounts array"/"no dry_run field" oracles
must grade the WIRE.

Bug: then_no_accounts_in_response and
then_no_dry_run_field read the harness-reconstructed AdCPError
(vars(error)/error.model_dump()/getattr(error, "dry_run", None)) instead of
ctx["wire_error_envelope"]. AdCPError has a fixed __dict__ shape (context, details,
error_code, field, message, recovery, retry_after, status_code, suggestion) that can
never carry an "accounts" or "dry_run" key regardless of what the seller actually put
on the wire — both assertions were structurally unfalsifiable, in steps whose
docstrings claim to prove the exclusion "on the wire".

These tests construct a ctx where the REAL wire envelope leaks the field in question
(simulating exactly the seller bug each step claims to catch) while the reconstructed
exception (as always) carries no such key. The CURRENT implementation is shown to pass
anyway (the oracle is blind); after the fix it must fail. A negative case per step
confirms a genuinely clean envelope is not flagged.
"""

from __future__ import annotations

import pytest


class _FakeAdCPError(Exception):
    """Mimics the reconstructed AdCPError's __dict__ shape (no 'accounts' key ever)."""

    def __init__(self):
        super().__init__("some accounts error")
        self.context = None
        self.details = None
        self.error_code = "ACCOUNT_NOT_FOUND"
        self.field = None
        self.message = "some accounts error"
        self.recovery = "terminal"
        self.retry_after = None
        self.status_code = 404
        self.suggestion = None


def _get_then_fns():
    from tests.bdd.steps.domain.uc011_accounts import then_no_accounts_in_response, then_no_dry_run_field

    return then_no_accounts_in_response, then_no_dry_run_field


def _clean_envelope() -> dict:
    return {
        "adcp_error": {"code": "ACCOUNT_NOT_FOUND", "message": "not found", "recovery": "terminal"},
        "errors": [{"code": "ACCOUNT_NOT_FOUND", "message": "not found", "recovery": "terminal"}],
    }


class TestNoAccountsInResponseOracleStrength:
    def test_leaked_accounts_on_the_wire_must_be_caught(self):
        """A seller that leaks 'accounts' data in the real error envelope must fail this step.

        ctx["error"] is the harness-reconstructed exception (no 'accounts' key,
        by construction — this is what the CURRENT implementation reads). The real
        wire envelope, however, carries a leaked accounts array nested in the
        error details — exactly the seller defect this step's docstring claims to
        prove doesn't happen.
        """
        then_no_accounts_in_response, _ = _get_then_fns()
        ctx = {
            "error": _FakeAdCPError(),
            "response": None,
            "wire_error_envelope": {
                "adcp_error": {"code": "ACCOUNT_NOT_FOUND", "message": "not found", "recovery": "terminal"},
                "errors": [
                    {
                        "code": "ACCOUNT_NOT_FOUND",
                        "message": "not found",
                        "recovery": "terminal",
                        "details": {"accounts": [{"account_id": "acc_leaked"}]},
                    }
                ],
            },
        }
        with pytest.raises(AssertionError, match="accounts"):
            then_no_accounts_in_response(ctx)

    def test_clean_envelope_passes(self):
        """Negative case: an envelope genuinely free of 'accounts' must NOT be flagged."""
        then_no_accounts_in_response, _ = _get_then_fns()
        ctx = {"error": _FakeAdCPError(), "response": None, "wire_error_envelope": _clean_envelope()}
        then_no_accounts_in_response(ctx)  # must not raise


class TestNoDryRunFieldOracleStrength:
    def test_leaked_dry_run_on_the_wire_must_be_caught(self):
        """A seller that leaks 'dry_run' data in the real error envelope must fail this step."""
        _, then_no_dry_run_field = _get_then_fns()
        ctx = {
            "error": _FakeAdCPError(),
            "response": None,
            "wire_error_envelope": {
                "adcp_error": {"code": "ACCOUNT_NOT_FOUND", "message": "not found", "recovery": "terminal"},
                "errors": [
                    {
                        "code": "ACCOUNT_NOT_FOUND",
                        "message": "not found",
                        "recovery": "terminal",
                        "details": {"dry_run": True},
                    }
                ],
            },
        }
        with pytest.raises(AssertionError, match="dry_run"):
            then_no_dry_run_field(ctx)

    def test_clean_envelope_passes(self):
        """Negative case: an envelope genuinely free of 'dry_run' must NOT be flagged."""
        _, then_no_dry_run_field = _get_then_fns()
        ctx = {"error": _FakeAdCPError(), "response": None, "wire_error_envelope": _clean_envelope()}
        then_no_dry_run_field(ctx)  # must not raise
