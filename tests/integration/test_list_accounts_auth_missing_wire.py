"""Regression test: list_accounts with NO auth token must emit AUTH_MISSING, not AUTH_REQUIRED.

ticket: #2092

Per the pinned AdCP v3.1.1 error-code enum, ``AUTH_REQUIRED`` is deprecated.
Absent-credential auth failures must emit ``AUTH_MISSING`` (recovery=correctable);
presented-but-invalid credentials must emit ``AUTH_INVALID`` (recovery=terminal).
Production (``src/core/exceptions.py`` ``AdCPAuthenticationError`` /
``AdCPAuthRequiredError``) still defaults to the deprecated ``AUTH_REQUIRED`` code
for every auth failure, including the absent-token case.

This test drives ``list_accounts`` over the real REST wire with no credentials
at all and asserts the two-layer wire envelope carries ``AUTH_MISSING``
(recovery=correctable) — the 3.1.1-compliant code for the absent-credential
case (auth.py:353 ``require_principal_id`` classification in #2092's
reproduction notes). It currently fails because production emits
``AUTH_REQUIRED`` instead.

Spec-Grounding: enums/error-code.json @ v3.1.1 (AUTH_MISSING / AUTH_INVALID split).
"""

import pytest

from tests.harness.account_list import AccountListEnv
from tests.harness.transport import Transport

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
            # require_suggestion is the obligation inherited from
            # tests/integration/test_auth_suggestion_parity.py, which is DELETED. That file
            # graded "every AUTH_MISSING/AUTH_INVALID rejection carries a non-empty
            # top-level suggestion" (#1417 round-8 items 3-4, #2092) by driving
            # src/core/auth.py's own raise sites -- require_principal_id,
            # resolve_principal_or_raise and require_tenant. All three are GONE: the
            # resolver is the one place a credential is judged (47d57e5d6), and
            # ruff-boundary.toml bans minting AUTH_MISSING / AUTH_INVALID anywhere else, so
            # those helpers have no callers and no successors to drive.
            #
            # The obligation itself is unchanged and is graded LIVE by the BDD step
            # then_error_code_with_suggestion, which asserts through the same
            # assert_wire_error(require_suggestion=...) oracle across a2a, mcp and rest.
            # Arming it here too costs nothing and puts the absent-credential case under it.
            #
            # This test does not currently reach that assertion: it fails one line up,
            # because list_accounts answers an absent credential with 200 and an empty
            # accounts[] instead of refusing it. That gap predates this change (it is the
            # same failure the box run recorded) and is tracked by this file's docstring.
            result.assert_wire_error(
                "AUTH_MISSING",
                recovery="correctable",
                require_suggestion=True,
            )
