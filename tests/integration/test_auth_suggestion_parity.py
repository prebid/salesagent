"""Auth rejections must carry error.json's top-level suggestion (#1417 round-8 review, items 3-4).

Core Invariant: every AUTH_MISSING/AUTH_INVALID rejection carries a non-empty
TOP-LEVEL ``suggestion`` in the two-layer wire envelope (AdCP 3.1.1, pinned
ref dist/schemas/3.1.1/enums/error-code.json — "Suggested action to resolve
the error"). ``require_identity`` already passes
``suggestion=AUTH_MISSING_SUGGESTION``; its siblings in ``src/core/auth.py``
(invalid-token, ``resolve_principal_or_raise``, ``require_principal_id``)
raised with the hint only in message text, leaving the graded top-level
``suggestion`` field EMPTY (PR #1417 review round 8, item 4). Split from the
single deprecated ``AUTH_REQUIRED`` code to ``AUTH_MISSING``/``AUTH_INVALID``
per the v3.1.1 error-code enum split (salesagent-mkso). ``require_tenant``
(tenant-axis gap, salesagent-40kk) now completes the split too
(salesagent-otc5): no identity at all -> ``AUTH_MISSING``; identity
present but its tenant unresolvable -> ``AUTH_INVALID`` (terminal).

Wire-first per tests/CLAUDE.md § Error Verification Policy: the
``require_principal_id`` case drives the REAL A2A wire (it is the
account-resolution boundary the PR newly routes onto); the remaining helpers
are graded on the envelope the production boundary translator builds for
their raise (``build_two_layer_error_envelope`` — the same builder every
transport dispatcher calls).
"""

import pytest

from src.core.exceptions import AdCPError, build_two_layer_error_envelope
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _assert_auth_missing_with_suggestion(envelope: dict) -> None:
    from tests.harness.transport import extract_wire_suggestion

    assert_envelope_shape(envelope, "AUTH_MISSING", recovery="correctable")
    suggestion = extract_wire_suggestion(envelope)
    assert suggestion, (
        "Expected a non-empty TOP-LEVEL suggestion in the AUTH_MISSING wire "
        f"envelope (dist/schemas/3.1.1/enums/error-code.json), got: {envelope}"
    )


def _assert_auth_invalid_with_suggestion(envelope: dict) -> None:
    from tests.harness.transport import extract_wire_suggestion

    assert_envelope_shape(envelope, "AUTH_INVALID", recovery="terminal")
    suggestion = extract_wire_suggestion(envelope)
    assert suggestion, (
        "Expected a non-empty TOP-LEVEL suggestion in the AUTH_INVALID wire "
        f"envelope (dist/schemas/3.1.1/enums/error-code.json), got: {envelope}"
    )


class TestRequirePrincipalIdA2ASuggestion:
    """require_principal_id rejection on the real A2A wire carries a suggestion."""

    def test_missing_principal_a2a_envelope_carries_suggestion(self, integration_db):
        """An identity with no principal_id rejected on the A2A wire must
        produce the AUTH_MISSING envelope WITH a top-level ``suggestion`` —
        parity with ``require_identity``. Absent credential -> AUTH_MISSING
        per v3.1.1 error-code.json (salesagent-mkso).
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.media_buy_list import MediaBuyListEnv
        from tests.harness.transport import Transport

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            TenantFactory(tenant_id="t1")
            identity = PrincipalFactory.make_identity(principal_id=None, tenant_id="t1")

            result = env.call_via(Transport.A2A, identity=identity)

            assert result.is_error, (
                f"A missing principal_id must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "AUTH_MISSING",
                recovery="correctable",
                require_suggestion=True,
            )


class TestAuthHelperFamilySuggestion:
    """The remaining AUTH_MISSING/AUTH_INVALID raise sites in src/core/auth.py carry a suggestion.

    Each case drives the production helper and asserts on the envelope the
    production boundary translator builds for its raise — the same
    ``build_two_layer_error_envelope`` every transport dispatcher calls.
    """

    def test_resolve_principal_not_found_carries_suggestion(self, integration_db):
        """A presented-but-unresolvable principal_id -> AUTH_INVALID (terminal)."""
        from src.core.auth import resolve_principal_or_raise
        from tests.factories import TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv(tenant_id="auth_sugg_t1") as env:
            TenantFactory(tenant_id="auth_sugg_t1")
            env.get_session()  # commit factory data

            with pytest.raises(AdCPError) as exc_info:
                resolve_principal_or_raise("nonexistent-principal", tenant_id="auth_sugg_t1")

        _assert_auth_invalid_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_require_tenant_no_identity_at_all_is_auth_missing(self):
        """No identity presented at all -> AUTH_MISSING (salesagent-otc5).

        Completes the AUTH_MISSING/AUTH_INVALID split (salesagent-mkso) for
        the tenant-resolution axis: genuinely absent credentials must emit
        AUTH_MISSING like every other no-credential rejection, not the
        deprecated AUTH_REQUIRED alias.
        """
        from src.core.auth import require_tenant

        with pytest.raises(AdCPError) as exc_info:
            require_tenant(None)

        _assert_auth_missing_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_require_tenant_credential_presented_but_unresolvable_is_auth_invalid(self):
        """A token was presented but its tenant can't be resolved -> AUTH_INVALID (terminal) (salesagent-otc5).

        Completes the AUTH_MISSING/AUTH_INVALID split (salesagent-mkso) for
        the tenant-resolution axis: the signal is credential presence
        (``identity.auth_token``), not merely whether an identity object
        exists — resolve_identity() always builds one for discovery
        endpoints. A presented-but-unresolvable credential is the terminal
        case, not the deprecated correctable AUTH_REQUIRED alias.
        """
        from src.core.auth import require_tenant
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(tenant=None, auth_token="presented-token")

        with pytest.raises(AdCPError) as exc_info:
            require_tenant(identity)

        _assert_auth_invalid_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_require_tenant_no_credential_presented_is_auth_missing_even_with_identity(self):
        """An identity object exists (discovery endpoints always build one) but
        carries no auth_token -> AUTH_MISSING, not AUTH_INVALID (salesagent-otc5).

        Mirrors the real anonymous-discovery shape: resolve_identity() with
        require_valid_token=False builds a ResolvedIdentity even with no
        token presented at all — the tenant-resolution split must key off
        credential presence, not identity-object presence.
        """
        from src.core.auth import require_tenant
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(tenant=None, auth_token=None)

        with pytest.raises(AdCPError) as exc_info:
            require_tenant(identity)

        _assert_auth_missing_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_invalid_token_carries_suggestion(self, integration_db):
        """get_principal_from_context with an invalid token (require_valid_token=True)
        raises AUTH_INVALID (presented-but-rejected credential) whose envelope
        must carry the top-level suggestion.
        """
        from src.core.auth import get_principal_from_context
        from tests.factories import TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv(tenant_id="auth_sugg_t2") as env:
            TenantFactory(tenant_id="auth_sugg_t2")
            env.get_session()  # commit factory data

            class _HeaderCarrier:
                """Duck-typed context: get_http_headers() returns {} outside an
                HTTP request, so get_principal_from_context falls back to
                ``context.headers`` — the documented sync-tool seam."""

                headers = {
                    "x-adcp-auth": "not-a-real-token",
                    "x-adcp-tenant": "auth_sugg_t2",
                }

            with pytest.raises(AdCPError) as exc_info:
                get_principal_from_context(_HeaderCarrier())

        _assert_auth_invalid_with_suggestion(build_two_layer_error_envelope(exc_info.value))
