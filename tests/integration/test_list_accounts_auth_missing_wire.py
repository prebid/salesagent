"""Regression test: list_accounts with NO auth token must emit AUTH_MISSING, not AUTH_REQUIRED.

beads: salesagent-mkso

Per the pinned AdCP v3.1.1 error-code enum, ``AUTH_REQUIRED`` is deprecated.
Absent-credential auth failures must emit ``AUTH_MISSING`` (recovery=correctable);
presented-but-invalid credentials must emit ``AUTH_INVALID`` (recovery=terminal).
Production (``src/core/exceptions.py`` ``AdCPAuthenticationError`` /
``AdCPAuthRequiredError``) still defaults to the deprecated ``AUTH_REQUIRED`` code
for every auth failure, including the absent-token case.

This test drives ``list_accounts`` over the real REST wire with no credentials
at all and asserts the two-layer wire envelope carries ``AUTH_MISSING``
(recovery=correctable) — the 3.1.1-compliant code for the absent-credential
case (auth.py:353 ``require_principal_id`` classification in salesagent-mkso's
reproduction notes). It currently fails because production emits
``AUTH_REQUIRED`` instead.

Spec-Grounding: enums/error-code.json @ v3.1.1 (AUTH_MISSING / AUTH_INVALID split).
"""

import pytest

from tests.harness.account_list import AccountListEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestListAccountsNoTokenEmitsAuthMissing:
    """list_accounts with an absent auth token must emit AUTH_MISSING on the wire."""

    def test_no_token_rest_wire_emits_auth_missing(self, integration_db):
        with AccountListEnv(tenant_id="la_auth_missing_t1", principal_id="agent_la_am") as env:
            env.setup_default_data()

            result = env.call_via(Transport.REST, identity=None)

            assert result.is_error, (
                f"list_accounts with no auth token must be rejected, got success payload: {result.payload!r}"
            )
            assert result.wire_error_envelope is not None, (
                f"Expected a wire rejection envelope, got none (envelope={result.envelope!r})"
            )
            # This is the regression: production currently emits the deprecated
            # AUTH_REQUIRED code (correctable) instead of the 3.1.1 AUTH_MISSING
            # code (correctable) for the absent-credential case.
            assert_envelope_shape(
                result.wire_error_envelope,
                "AUTH_MISSING",
                recovery="correctable",
            )
