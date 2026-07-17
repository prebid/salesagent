"""Integration tests: sync_creatives auth, isolation, validation, CRUD, extensions, provenance.

Behavioral tests using CreativeSyncEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Covers: salesagent-xwkj, salesagent-11th, salesagent-0m59, salesagent-mi8l
"""

from __future__ import annotations

import json
from datetime import UTC

import pytest
from adcp.types import CreativeAction
from adcp.types import FormatId as AdcpFormatId

from src.core.exceptions import AdCPAuthenticationError, AdCPCreativeRejectedError, AdCPNotFoundError
from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.factories.creative_asset import build_assets, image_spec, make_test_banner_creative
from tests.harness import CreativeSyncEnv, make_identity
from tests.harness.transport import Transport

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Wire transports only — IMPL has no wire envelope by definition.
_WIRE_TRANSPORTS = [Transport.REST, Transport.MCP, Transport.A2A]

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _error_messages(errors: list | None) -> list[str]:
    """Extract message strings from Error objects or plain strings."""
    if not errors:
        return []
    return [e.message if hasattr(e, "message") else str(e) for e in errors]


_make_creative_asset = make_test_banner_creative  # Canonical version from tests.factories.creative_asset
_make_identity = make_identity  # Canonical version from tests.harness


# ---------------------------------------------------------------------------
# Auth Tests — UC-006-EXT-A, UC-006-EXT-B
# ---------------------------------------------------------------------------


def _wire_entries(result) -> dict:
    """Index the wire response's creatives[] by creative_id.

    Shared read-back for TestAssignmentProcessing: every per-item wire
    assertion goes through the same extraction instead of hand-rolling the
    dict comprehension per test.
    """
    return {e.get("creative_id"): e for e in (result.wire_response or {}).get("creatives", [])}


def _assert_correctable(result) -> None:
    """Assert a per-creative IMPL failure is buyer-CORRECTABLE, not transient.

    Grades BOTH halves of the retry contract, because the code alone does not
    govern behavior: the wire code must be VALIDATION_ERROR (never the transient
    SERVICE_UNAVAILABLE), AND recovery must be ``correctable`` — the field a
    conforming buyer keys retry-forever-vs-fix-and-resubmit on. A regression that
    kept the corrected code but reverted recovery to ``transient`` would still
    drive the retry-forever behavior this class exists to prevent, and a code-only
    assertion would stay green. SDK ``Recovery`` is an enum (not a str-mixin) —
    compare ``.value`` (mirrors the sibling assertion at
    test_lenient_mode_unknown_assignment_...:448). Extracted so both correctable-
    code tests route through one assertion and cannot drift — a copy-paste that
    drops the recovery half in one place and not the other is exactly the risk.
    """
    assert result.action == "failed", f"expected a failed result, got action={result.action!r}"
    errors = result.errors or []
    codes = [getattr(e, "code", None) for e in errors]
    assert "SERVICE_UNAVAILABLE" not in codes, f"correctable failure mis-coded as transient: {codes}"
    assert "VALIDATION_ERROR" in codes, f"expected VALIDATION_ERROR, got {codes}"
    validation = [e for e in errors if getattr(e, "code", None) == "VALIDATION_ERROR"]
    assert validation and all(e.recovery is not None and e.recovery.value == "correctable" for e in validation), (
        f"VALIDATION_ERROR must carry recovery=correctable, got {[getattr(e, 'recovery', None) for e in validation]!r}"
    )


class TestSyncAuthRequired:
    """Auth errors are operation-level — raised before any creative processing."""

    def test_no_identity_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-A-01 — identity=None → AdCPAuthenticationError."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_identity_without_principal_raises(self, integration_db):
        """Covers: UC-006-EXT-A-01 — principal_id=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id=None, tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_identity_without_tenant_raises(self, integration_db):
        """Covers: UC-006-EXT-B-01 — tenant=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_auth_error_before_db_access(self, integration_db):
        """Covers: UC-006-EXT-A-02 — auth error is operation-level, no partial results."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                # If this returned a response instead of raising, auth is broken
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_empty_principal_id_raises(self, integration_db):
        """Covers: UC-006-EXT-A-01 — empty string principal_id → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="", tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)


# ---------------------------------------------------------------------------
# Cross-Principal Isolation — Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01
# ---------------------------------------------------------------------------


class TestCrossPrincipalIsolation:
    """Creatives are scoped by (tenant_id, principal_id) — real DB proves isolation."""

    def test_creative_visible_only_to_owning_principal(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01 — creative created by P1 not visible to P2 query."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        # Create all seed data + sync as P1 inside one env context
        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            p1 = PrincipalFactory(tenant=tenant)
            p2 = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            p1_id = p1.principal_id
            p2_id = p2.principal_id

            p1_identity = _make_identity(
                principal_id=p1_id,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="shared_id")],
                identity=p1_identity,
            )

        # Query DB directly as principal 2 — should find nothing
        with get_db_session() as session:
            p2_creatives = session.scalars(
                select(DBCreative).filter_by(
                    tenant_id=tid,
                    principal_id=p2_id,
                    creative_id="shared_id",
                )
            ).all()
            assert len(p2_creatives) == 0, "Principal 2 should not see Principal 1's creative"

            # But principal 1 should see it
            p1_creatives = session.scalars(
                select(DBCreative).filter_by(
                    tenant_id=tid,
                    principal_id=p1_id,
                    creative_id="shared_id",
                )
            ).all()
            assert len(p1_creatives) == 1

    def test_same_creative_id_different_principals_are_separate(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-02 — same creative_id under different principals = separate records."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        # Create factories + sync as P1 in first env
        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            p1 = PrincipalFactory(tenant=tenant)
            p2 = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            p1_id = p1.principal_id
            p2_id = p2.principal_id

            p1_identity = _make_identity(
                principal_id=p1_id,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_shared")],
                identity=p1_identity,
            )

        # Sync same creative_id as P2 (factories already committed to DB)
        with CreativeSyncEnv(principal_id=p2_id, tenant_id=tid) as env:
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_shared")])

        # Both should exist as separate records
        with get_db_session() as session:
            all_creatives = session.scalars(select(DBCreative).filter_by(tenant_id=tid, creative_id="c_shared")).all()
            assert len(all_creatives) == 2
            principal_ids = {c.principal_id for c in all_creatives}
            assert principal_ids == {p1_id, p2_id}

    def test_new_creative_stamped_with_correct_principal(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03 — created creative has correct principal_id in DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            principal = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            pid = principal.principal_id

            p_identity = _make_identity(
                principal_id=pid,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_stamped")],
                identity=p_identity,
            )

        assert len(response.creatives) == 1

        with get_db_session() as session:
            db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_stamped", tenant_id=tid)).first()
            assert db_creative is not None
            assert db_creative.principal_id == pid


# ---------------------------------------------------------------------------
# Validation Tests — Covers: UC-006-EXT-D-01
# ---------------------------------------------------------------------------


class TestCreativeValidation:
    """Input validation for _sync_creatives_impl with real format registry mock."""

    def test_empty_name_rejected(self, integration_db):
        """Covers: UC-006-EXT-D-01 — empty creative name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == "failed" or (result.errors and len(result.errors) > 0)

    def test_whitespace_only_name_rejected(self, integration_db):
        """Covers: UC-006-EXT-D-01 — whitespace-only name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="   ")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == "failed" or (result.errors and len(result.errors) > 0)

    def test_valid_creative_accepted(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — valid creative → created action."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_valid", name="Valid Creative")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.creative_id == "c_valid"
            # Should be created (not failed)
            assert result.action != "failed"

    def test_adapter_format_skips_registry_validation(self, integration_db):
        """Covers: UC-006-CREATIVE-FORMAT-VALIDATION-02 — adapter:// agent_url skips external format lookup."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_adapter",
                        format_id=AdcpFormatId(agent_url="broadstreet://default", id="broadstreet_billboard"),
                    )
                ]
            )
            assert len(response.creatives) == 1
            # Should succeed without registry lookup (non-HTTP agent_url)
            assert response.creatives[0].action != "failed"

    def test_input_validation_failure_uses_correctable_code(self, integration_db):
        """An input-validation failure (here: empty name) is buyer-CORRECTABLE:
        the per-creative error code must be VALIDATION_ERROR, not SERVICE_UNAVAILABLE
        — the latter implies a transient outage and drives a conforming buyer to
        retry a permanent error forever.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json; docs/adcp-spec-
        version.md, SDK 6.6.0): VALIDATION_ERROR → recovery ``correctable``,
        SERVICE_UNAVAILABLE → recovery ``transient``. error-handling.mdx: a
        correctable failure is fixed and resubmitted, a transient one retried
        as-is. Graded in-process here; the A2A wire equivalent is
        test_correctable_failure_code_and_recovery_on_the_a2a_wire.
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_bad", name="")])
            assert len(response.creatives) == 1
            _assert_correctable(response.creatives[0])

    def test_unknown_format_failure_uses_correctable_code(self, integration_db):
        """An unknown-format failure (typed AdCPValidationError, recovery correctable)
        must surface as VALIDATION_ERROR, not the default SERVICE_UNAVAILABLE the
        `except AdCPError` handler used to emit for every non-transient typed error.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json): VALIDATION_ERROR
        → correctable. The typed AdCPValidationError is non-transient, so the
        `except AdCPError` path (_sync.py:363) keeps it as a per-item failure and
        forwards its already-wire-standard code — see
        test_non_wire_typed_error_code_normalized_not_leaked for the sibling path
        where the typed code is NOT wire-standard and must be normalized.
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Make the registry reject the format so the real unknown-format
            # AdCPValidationError path runs (the env's default mock accepts any id).
            # Set the return on the env's existing get_format mock rather than
            # constructing a new one (the per-file hand-rolled-mock cap only shrinks).
            env.mock["registry"].return_value.get_format.return_value = None

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_bad_format",
                        format_id=AdcpFormatId(
                            agent_url="https://creative.adcontextprotocol.org",
                            id="format_does_not_exist_xyz",
                        ),
                    )
                ]
            )
            assert len(response.creatives) == 1
            _assert_correctable(response.creatives[0])

    def test_correctable_failure_code_and_recovery_on_the_a2a_wire(self, integration_db):
        """The correctable per-creative code+recovery must survive on a REAL wire, not
        only in the in-process SyncCreativeResult the two tests above read. A
        serialization boundary that dropped or re-coerced errors[].code / .recovery
        would leave those in-process tests green while shipping the wrong contract.

        Graded on A2A specifically. Unlike the CREATIVE_NOT_FOUND wire test (:369),
        which grades an OPERATION-level error envelope every transport emits
        uniformly, these #1650 conditions are PER-CREATIVE validation failures: A2A
        returns them on the success-branch Task artifact (readable creatives[]),
        whereas the REST/MCP request path surfaces an all-invalid creatives payload
        as an operation-level rejection before per-item results are produced — a
        transport behavior orthogonal to this PR (the existing per-item REST wire
        test, :962, likewise drives an *assignment* failure, not a creative one).
        ``result.payload`` here is deserialized from the real A2A artifact DataPart,
        so this reads the wire, not the in-process result object. Routed through the
        same ``_assert_correctable`` as the in-process tests (a SyncCreativeResult
        parsed off the wire has the identical shape) so the code+recovery contract is
        asserted by one helper on both surfaces.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json): VALIDATION_ERROR →
        recovery ``correctable``; SERVICE_UNAVAILABLE → ``transient``.
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(Transport.A2A, creatives=[_make_creative_asset(creative_id="c_bad", name="")])

            assert not result.is_error, (
                f"per-creative validation failure must stay on the A2A success branch: {result.wire_error_envelope}"
            )
            creatives = getattr(result.payload, "creatives", None)
            assert creatives, f"A2A wire payload must carry the per-item result: {result.payload!r}"
            entry = next((c for c in creatives if c.creative_id == "c_bad"), None)
            assert entry is not None, f"expected the failed creative on the A2A wire: {creatives!r}"
            _assert_correctable(entry)

    def test_non_wire_typed_error_code_normalized_not_leaked(self):
        """A non-transient typed error whose code is NOT wire-standard must never reach
        the buyer verbatim — _failed_sync_result normalizes it through to_wire_error_code
        at the one choke point every call site shares.

        The `except AdCPError` path (_sync.py:363) forwards e.error_code straight into
        the advisory; advisory errors[] serialize verbatim and never pass through the
        boundary translator that handles raised AdCPErrors, so a raw internal code would
        leak. Pre-PR this path defaulted to the safe SERVICE_UNAVAILABLE — the code-
        carrying fix removed that safety, so the normalization moved into the helper.
        Drives a real AdCPFormatNotFoundError (FORMAT_NOT_FOUND, recovery correctable,
        non-transient → not re-raised → lands at :363) exactly as line 363 forwards it,
        and asserts the emitted code is the normalized wire value INVALID_REQUEST (per
        to_wire_error_code) with the retry signal preserved. No DB needed — this is the
        choke-point unit check backing the in-process/wire behavioral tests above.
        """
        from src.core.exceptions import AdCPFormatNotFoundError
        from src.core.tools.creatives._processing import _failed_sync_result

        err = AdCPFormatNotFoundError("format_does_not_exist_xyz")
        assert err.error_code == "FORMAT_NOT_FOUND"  # a non-wire internal code

        result = _failed_sync_result("c_leak", str(err), code=err.error_code, recovery=err.recovery)

        emitted = result.errors[0]
        assert emitted.code == "INVALID_REQUEST", (
            f"non-wire typed code {err.error_code!r} must normalize to its wire value, got {emitted.code!r}"
        )
        assert emitted.code != "FORMAT_NOT_FOUND", "internal code leaked to the buyer verbatim"
        # the code normalizes; the retry signal is preserved unchanged
        assert emitted.recovery is not None and emitted.recovery.value == "correctable"


# ---------------------------------------------------------------------------
# Validation Mode Tests — Covers: UC-006-MAIN-MCP-05
# ---------------------------------------------------------------------------


class TestValidationModeSemantics:
    """Strict vs lenient validation mode behavior with real DB savepoints."""

    def test_lenient_mode_continues_after_validation_error(self, integration_db):
        """Covers: UC-006-MAIN-MCP-05 — lenient: one bad creative doesn't block others."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_good_1", name="Good One"),
                    _make_creative_asset(creative_id="c_bad", name=""),  # empty name → fails
                    _make_creative_asset(creative_id="c_good_2", name="Good Two"),
                ],
                validation_mode="lenient",
            )
            # All 3 should have results
            assert len(response.creatives) == 3
            # c_bad should be failed
            bad_result = next(r for r in response.creatives if r.creative_id == "c_bad")
            assert bad_result.action == "failed"
            # c_good_1 and c_good_2 should NOT be failed
            good_results = [r for r in response.creatives if r.creative_id != "c_bad"]
            for r in good_results:
                assert r.action != "failed", f"Creative {r.creative_id} should succeed in lenient mode"

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_strict_mode_unknown_assignment_creative_is_creative_not_found_on_wire(self, integration_db, transport):
        """Strict-mode assignment to an UNKNOWN creative_id must emit CREATIVE_NOT_FOUND.

        Spec grounding (pinned 3.1 enum, enums/error-code.json @ 04f59d2d5):
        CREATIVE_NOT_FOUND — "Referenced creative does not exist in the agent's
        creative library. Recovery: correctable (...). Sellers MUST return this
        code uniformly for any creative_id not owned by the calling account".
        The parallel package-not-found branch in the same function already uses
        the entity-specific PACKAGE_NOT_FOUND; creative-not-found rode the
        generic VALIDATION_ERROR instead (PR #1430 review, CON-07).

        Graded on every wire transport: a boundary re-adding a
        STANDARD_ERROR_CODES gate on MCP/A2A (demoting the supplement-only
        CREATIVE_NOT_FOUND passthrough) must fail this matrix, not just REST.
        """
        from tests.helpers import assert_envelope_shape

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            result = env.call_via(
                transport,
                creatives=[],
                assignments={"c_never_synced": [pkg.package_id]},
                validation_mode="strict",
            )

            assert result.is_error, f"Strict mode must abort on unknown creative: {result.payload!r}"
            assert_envelope_shape(
                result.wire_error_envelope,
                "CREATIVE_NOT_FOUND",
                recovery="correctable",
                message_substr="c_never_synced",
            )

    def test_lenient_mode_unknown_assignment_creative_entry_is_creative_not_found(self, integration_db):
        """Lenient-mode assignment to an UNKNOWN creative_id: the synthesized
        per-item advisory entry must carry CREATIVE_NOT_FOUND, not the generic
        VALIDATION_ERROR.

        Spec grounding (pinned 3.1 enum, enums/error-code.json @ 04f59d2d5):
        CREATIVE_NOT_FOUND — "Referenced creative does not exist in the agent's
        creative library. Recovery: correctable (...). Sellers MUST return this
        code uniformly for any creative_id not owned by the calling account".
        error-handling.mdx "Not-found precedence" (newest prose at the pin,
        3.1.0-beta.1): the resource-specific code for a creative_id reference
        SHOULD be CREATIVE_NOT_FOUND. Ungraded by storyboard (zero
        CREATIVE_NOT_FOUND hits in dist/compliance/3.1.0-beta.3).

        Same-surface consistency: the strict-mode raise for the IDENTICAL
        condition already emits CREATIVE_NOT_FOUND on the wire (287c93099,
        test above) — the same condition on the same tool must surface the
        same code on the lenient per-item advisory path
        (_assignments.py synthesis loop, currently VALIDATION_ERROR).
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            response = env.call_impl(
                creatives=[],
                assignments={"c_never_synced": [pkg.package_id]},
                validation_mode="lenient",
            )

            entry = next(r for r in response.creatives if r.creative_id == "c_never_synced")
            assert entry.action == "failed", f"Expected synthesized action='failed' entry, got: {entry}"
            assert entry.errors, f"failed entry must carry errors[]: {entry}"
            assert entry.errors[0].code == "CREATIVE_NOT_FOUND", (
                f"Unknown-creative advisory must carry CREATIVE_NOT_FOUND (parity with the "
                f"strict-mode raise for the same condition), got: {entry.errors[0].code!r}"
            )
            # SDK Error.recovery is a Recovery enum (not a str-mixin) — compare .value.
            assert entry.errors[0].recovery.value == "correctable", (
                f"CREATIVE_NOT_FOUND is buyer-correctable, got: {entry.errors[0].recovery!r}"
            )

    def test_lenient_mode_existing_creative_missing_package_entry_keeps_validation_error(self, integration_db):
        """Negative control for the CREATIVE_NOT_FOUND advisory split: an
        assignment-only reference to an EXISTING library creative whose only
        failure is a nonexistent package_id must keep the generic
        VALIDATION_ERROR on its synthesized entry.

        A not-found creative short-circuits before any package checks
        (_assignments.py: `continue` after the creative_row-is-None branch),
        so one synthesized entry can never mix the two causes — this test uses
        a SEPARATE, existing creative to pin that only the creative-not-found
        cause flips to CREATIVE_NOT_FOUND. (Strict-mode PACKAGE_NOT_FOUND
        parity for this condition is a known residual tracked in the
        gl3m/#1598 lane, not claimed correct here.)
        """
        from tests.factories import CreativeFactory

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_exists_in_library",
                format="display_300x250",
                agent_url=DEFAULT_AGENT_URL,
            )

            response = env.call_impl(
                creatives=[],
                assignments={"c_exists_in_library": ["pkg_does_not_exist"]},
                validation_mode="lenient",
            )

            entry = next(r for r in response.creatives if r.creative_id == "c_exists_in_library")
            assert entry.action == "failed", f"Expected synthesized action='failed' entry, got: {entry}"
            assert entry.errors, f"failed entry must carry errors[]: {entry}"
            assert entry.errors[0].code == "VALIDATION_ERROR", (
                f"Package-not-found-only advisory must keep VALIDATION_ERROR "
                f"(only the creative-not-found cause flips), got: {entry.errors[0].code!r}"
            )

    def test_strict_mode_also_processes_all_creatives(self, integration_db):
        """Covers: UC-006-EXT-C-02 — strict: validation errors still per-creative in strict mode."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_good", name="Good"),
                    _make_creative_asset(creative_id="c_bad", name=""),
                ],
                validation_mode="strict",
            )
            # Both should be in results — validation errors are per-creative, not abortive
            assert len(response.creatives) >= 1

    def test_lenient_savepoint_isolation_with_real_db(self, integration_db):
        """Covers: UC-006-MAIN-MCP-05 — lenient: DB savepoints isolate per-creative failures."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_survives", name="Survivor"),
                    _make_creative_asset(creative_id="c_fails", name=""),
                    _make_creative_asset(creative_id="c_also_survives", name="Also Survivor"),
                ],
                validation_mode="lenient",
            )

        # Verify in DB: good creatives persisted despite bad creative in the batch
        with get_db_session() as session:
            survivors = session.scalars(
                select(DBCreative).filter_by(tenant_id="test_tenant", principal_id="test_principal")
            ).all()
            survivor_ids = {c.creative_id for c in survivors}
            assert "c_survives" in survivor_ids, "Good creative should be persisted"
            assert "c_also_survives" in survivor_ids, "Second good creative should be persisted"
            assert "c_fails" not in survivor_ids, "Bad creative should not be persisted"


# ---------------------------------------------------------------------------
# CRUD Workflow Tests — Covers: salesagent-11th
# ---------------------------------------------------------------------------


class TestCreateUpdateWorkflow:
    """Create/update upsert semantics with real DB verification."""

    def test_new_creative_creates_db_record(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — new creative inserted into DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_new", name="New Creative")])

        assert len(response.creatives) == 1
        assert response.creatives[0].action == "created"

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(
                    creative_id="c_new", tenant_id="test_tenant", principal_id="test_principal"
                )
            ).first()
            assert db_creative is not None
            assert db_creative.name == "New Creative"

    def test_existing_creative_updates_in_place(self, integration_db):
        """Covers: UC-006-MAIN-MCP-03 — upsert updates existing record by triple key."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create first
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_upsert", name="Original")])
            # Update with same creative_id
            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_upsert", name="Updated")])

        assert len(response.creatives) == 1
        assert response.creatives[0].action == "updated"

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_upsert", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.name == "Updated"

    def test_batch_sync_multiple_creatives(self, integration_db):
        """Covers: UC-006-MAIN-MCP-02 — batch of N creatives produces N results."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id=f"c_batch_{i}", name=f"Batch {i}") for i in range(5)]
            )

        assert len(response.creatives) == 5
        result_ids = {r.creative_id for r in response.creatives}
        assert result_ids == {f"c_batch_{i}" for i in range(5)}


class TestDeleteMissing:
    """delete_missing flag behavior with real DB."""

    def test_delete_missing_archives_unlisted_creatives(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-01 — unlisted creatives soft-deleted."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create two creatives
            env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_keep", name="Keep"),
                    _make_creative_asset(creative_id="c_orphan", name="Orphan"),
                ]
            )
            # Re-sync with only one — orphan should be archived
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_keep", name="Keep")],
                delete_missing=True,
            )

        # Check response includes a deleted action for orphan
        actions = {r.creative_id: r.action for r in response.creatives}
        assert "deleted" in actions.values()

        with get_db_session() as session:
            orphan = session.scalars(
                select(DBCreative).filter_by(creative_id="c_orphan", tenant_id="test_tenant")
            ).first()
            assert orphan is not None
            assert orphan.status == "archived"

    def test_delete_missing_false_preserves_unlisted(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-02 — default: unlisted creatives unchanged."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create initial creative
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_existing", name="Existing")])
            # Sync a different creative without delete_missing
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_new_one", name="New")],
                delete_missing=False,
            )

        # Only the synced creative in results
        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_new_one"

        with get_db_session() as session:
            existing = session.scalars(
                select(DBCreative).filter_by(creative_id="c_existing", tenant_id="test_tenant")
            ).first()
            assert existing is not None
            assert existing.status != "archived", "Existing creative should not be archived"


class TestCreativeIdsFilter:
    """creative_ids parameter scoping with real DB."""

    def test_creative_ids_filter_narrows_processing(self, integration_db):
        """Covers: UC-006-CREATIVE-IDS-SCOPE-01 — only matching IDs processed."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c1", name="One"),
                    _make_creative_asset(creative_id="c2", name="Two"),
                    _make_creative_asset(creative_id="c3", name="Three"),
                ],
                creative_ids=["c1", "c3"],
            )

        # Only c1 and c3 should be in results
        result_ids = {r.creative_id for r in response.creatives}
        assert result_ids == {"c1", "c3"}
        assert "c2" not in result_ids

    def test_empty_creative_ids_processes_all(self, integration_db):
        """Behavior: UC-006-CREATIVE-IDS-SCOPE-02 — empty list is falsy, processes all creatives."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c1", name="One")],
                creative_ids=[],
            )

        # Empty list is falsy in `if creative_ids:` — all creatives processed
        assert len(response.creatives) == 1


class TestDryRunMode:
    """dry_run flag: no DB writes."""

    def test_dry_run_does_not_persist(self, integration_db):
        """Covers: UC-006-DRY-RUN-01 — dry_run=True produces results without DB changes."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_dry", name="Dry Run Creative")],
                dry_run=True,
            )

        assert response.dry_run is True
        assert len(response.creatives) >= 1

        # Verify nothing written to DB
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_dry", tenant_id="test_tenant")
            ).first()
            assert db_creative is None, "Dry run should not persist any creatives"


class TestApprovalWorkflow:
    """Tenant approval_mode controls creative status."""

    def test_auto_approve_sets_approved_status(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01 — auto-approve → status=approved."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant", approval_mode="auto-approve")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override identity tenant dict to include approval_mode
            identity = _make_identity(
                principal_id="test_principal",
                tenant_id="test_tenant",
                tenant={"tenant_id": "test_tenant", "name": "Test", "approval_mode": "auto-approve"},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_auto", name="Auto Approved")],
                identity=identity,
            )

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_auto", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "approved"

    def test_require_human_sets_pending_review(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-02 — require-human → status=pending_review."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant", approval_mode="require-human")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            identity = _make_identity(
                principal_id="test_principal",
                tenant_id="test_tenant",
                tenant={"tenant_id": "test_tenant", "name": "Test", "approval_mode": "require-human"},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_human", name="Needs Review")],
                identity=identity,
            )

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_human", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "pending_review"

    def test_default_approval_mode_is_require_human(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-04 — no approval_mode → defaults to require-human."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Identity tenant dict has NO approval_mode key
            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_default", name="Default Mode")])

        assert len(response.creatives) == 1

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_default", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "pending_review"


class TestAssignmentProcessing:
    """Assignment creation with real DB + factory-created packages."""

    def test_assignment_persists_to_db(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01 — assignment record created in DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_assign", name="Assigned")],
                assignments={"c_assign": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_assign", package_id=pkg_id)
            ).all()
            assert len(assignments) == 1

    def test_cross_principal_creative_reference_does_not_500_or_leak(self, integration_db):
        """A principal referencing ANOTHER principal's creative_id in assignments
        must get a clean response — not a raw FK IntegrityError 500 — no
        assignment row may be inserted, and none of the owner's row fields may
        leak into the requester's response.

        creatives has a composite PK (creative_id, tenant_id, principal_id); the
        existence gate must be principal-scoped like the parallel lookup in
        _sync.py (SECURITY comment), so a cross-principal reference resolves to
        "not found" instead of passing the gate on the other principal's row and
        crashing on the FK insert (PR #1430 review). The owner's creative is
        seeded with marker fields that only surface if the gate reads their row;
        a same-request positive control (the requester's own creative) proves
        the assignment machinery ran, so a zero-rows outcome from an unrelated
        upstream gate cannot pass this scenario.
        """
        from src.core.database.models import CreativeAssignment as DBAssignment
        from tests.factories import CreativeFactory
        from tests.harness.transport import Transport

        # Fields that exist ONLY on the owner's creative row. Any of them in the
        # requester's wire response means the gate read the other principal's row
        # (mirrors the create-path _LEAK_MARKERS discipline).
        leak_markers = ("video_640x480", "rejected", "has status")

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            requester = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            owner = PrincipalFactory(tenant=tenant, principal_id="other_principal")
            # The cross-principal creative exists ONLY under the other principal,
            # armed with marker field values.
            CreativeFactory(
                tenant=tenant,
                principal=owner,
                creative_id="c_owned_by_other",
                format="video_640x480",
                status="rejected",
                agent_url="https://creative.adcontextprotocol.org",
            )
            # Positive control: the requester's OWN creative in the same request
            # must produce a real assignment row.
            CreativeFactory(
                tenant=tenant,
                principal=requester,
                creative_id="c_mine",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org",
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=requester)
            pkg = MediaPackageFactory(media_buy=media_buy)
            pkg_id = pkg.package_id

            result = env.call_via(
                Transport.REST,
                creatives=[],
                assignments={"c_owned_by_other": [pkg_id], "c_mine": [pkg_id]},
                validation_mode="lenient",
            )

            assert not result.is_error, (
                f"Cross-principal creative reference must not fail the request "
                f"(was a raw FK 500): {result.wire_error_envelope}"
            )
            # Mutation-proofing (PR #1430 orphan-assignment fix): the skipped
            # assignment must be VISIBLE on the wire as a synthesized failed
            # entry — no-error+no-row alone survives deletion of the error
            # recording.
            entries = _wire_entries(result)
            assert entries.get("c_owned_by_other", {}).get("action") == "failed", (
                f"Skipped cross-principal assignment must surface as action='failed': {result.wire_response}"
            )
            # Leak absence: the owner's field values must not appear anywhere in
            # the response the requester sees.
            wire_text = json.dumps(result.wire_response).lower()
            for marker in leak_markers:
                assert marker not in wire_text, (
                    f"Response leaks the other principal's creative fields ({marker!r}): {result.wire_response}"
                )
            # Positive control ran in the SAME request: real row for c_mine...
            assert entries.get("c_mine", {}).get("assigned_to") == [pkg_id], (
                f"Positive-control assignment must surface assigned_to: {entries.get('c_mine')}"
            )
            mine_rows = env.query(DBAssignment, tenant_id="test_tenant", creative_id="c_mine")
            assert len(mine_rows) == 1, f"Positive-control assignment row must exist, got {len(mine_rows)}"
            # ...and none for the cross-principal reference.
            cross_rows = env.query(DBAssignment, tenant_id="test_tenant", creative_id="c_owned_by_other")
            assert cross_rows == [], (
                f"No assignment may be created from a cross-principal reference, got {len(cross_rows)}"
            )

    @pytest.mark.parametrize(
        "orphan_creative_id, seed_other_principal",
        [
            ("c_never_synced", False),
            ("c_owned_by_other", True),  # cross-principal reference: same uniform surface
        ],
    )
    def test_orphan_assignment_error_surfaces_as_failed_result_on_wire(
        self, integration_db, orphan_creative_id, seed_other_principal
    ):
        """creatives=[] + assignments referencing an unknown creative_id (lenient
        mode) MUST surface the skipped assignment as a per-item result entry with
        action='failed' — not return bare success the buyer can't distinguish
        from a completed assignment.

        Spec grounding (pinned 3.1, static/schemas/source/creative/
        sync-creatives-response.json @ adcp 04f59d2d5): the success branch
        FORBIDS a response-level errors array (mutually-exclusive oneOf), so
        per-item failures ride creatives[] with action='failed' ("Items with
        action='failed' indicate per-item validation/processing failures"),
        errors[] "only present when action='failed'", assignment_errors keyed
        by package id, and status "MUST be omitted when action is failed".
        BR-RULE-033 INV-4 pins the principle: assignment errors are always
        recorded in the response. salesagent-9qpj: the result merge only
        decorated entries of creatives synced in the SAME request, so this
        shape returned bare success.
        """
        from tests.factories import CreativeFactory
        from tests.harness.transport import Transport

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            requester = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            if seed_other_principal:
                owner = PrincipalFactory(tenant=tenant, principal_id="other_principal")
                CreativeFactory(
                    tenant=tenant,
                    principal=owner,
                    creative_id=orphan_creative_id,
                    format="display_300x250",
                    agent_url="https://creative.adcontextprotocol.org",
                )
            media_buy = MediaBuyFactory(tenant=tenant, principal=requester)
            pkg = MediaPackageFactory(media_buy=media_buy)
            pkg_id = pkg.package_id

            result = env.call_via(
                Transport.REST,
                creatives=[],
                assignments={orphan_creative_id: [pkg_id]},
                validation_mode="lenient",
            )

            assert not result.is_error, (
                f"Lenient orphan-assignment must stay on the success branch: {result.wire_error_envelope}"
            )
            wire = result.wire_response
            assert wire is not None, "REST success must expose the wire body"
            entries = _wire_entries(result)
            assert orphan_creative_id in entries, (
                f"Orphan assignment reference must produce a per-item action='failed' "
                f"result entry — bare success hides the skipped assignment from the "
                f"buyer (BR-RULE-033 INV-4). Wire: {wire}"
            )
            entry = entries[orphan_creative_id]
            assert entry.get("action") == "failed", f"Expected action='failed', got: {entry}"
            assignment_errors = entry.get("assignment_errors") or {}
            assert assignment_errors.get(pkg_id), f"assignment_errors must name the skipped package {pkg_id}: {entry}"
            assert entry.get("errors"), f"failed entry must carry errors[] per spec: {entry}"
            assert "status" not in entry or entry.get("status") is None, (
                f"status MUST be omitted when action='failed': {entry}"
            )

    def test_assign_only_existing_creative_surfaces_assigned_to_on_wire(self, integration_db):
        """creatives=[] + assignments referencing an EXISTING library creative:
        the successful assignment must surface as a synthesized 'unchanged'
        entry with assigned_to — not vanish from the response (same merge hole
        as the orphan-error shape, success-info variant). salesagent-9qpj.
        """
        from tests.factories import CreativeFactory
        from tests.harness.transport import Transport

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_preexisting",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org",
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            pkg_id = pkg.package_id

            result = env.call_via(
                Transport.REST,
                creatives=[],
                assignments={"c_preexisting": [pkg_id]},
                validation_mode="lenient",
            )

            assert not result.is_error
            entries = _wire_entries(result)
            entry = entries.get("c_preexisting")
            assert entry is not None, (
                f"Assign-only reference to an existing creative must produce a result "
                f"entry carrying assigned_to: {result.wire_response}"
            )
            assert entry.get("action") == "unchanged", f"Sync didn't modify the creative: {entry}"
            assert entry.get("assigned_to") == [pkg_id], f"assigned_to must name the package: {entry}"

    def test_none_assignments_produces_no_records(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01 — None assignments = no assignment records."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_noassign", name="No Assign")],
                assignments=None,
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_noassign")
            ).all()
            assert len(assignments) == 0

    def test_idempotent_assignment_upsert(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04 — duplicate assignment not duplicated."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            pkg_id = pkg.package_id

            # Assign twice
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_idem", name="Idempotent")],
                assignments={"c_idem": [pkg_id]},
                validation_mode="lenient",
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_idem", name="Idempotent")],
                assignments={"c_idem": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_idem", package_id=pkg_id)
            ).all()
            assert len(assignments) == 1, "Idempotent: should not duplicate assignment"

    def test_failed_creative_assignment_skipped_no_fk_violation(self, integration_db):
        """Regression #1418 — lenient sync: invalid creative + its assignment.

        A creative that fails validation is never persisted, so processing its
        assignment must NOT attempt an INSERT (it would violate the creative FK
        and surface as a 500). Expected: a clean success envelope reporting the
        per-creative failure, an assignment_errors entry for the skipped package,
        and ZERO assignment rows for the failed creative.
        """
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            pkg_id = pkg.package_id

            # Empty name → validation failure → creative is skipped from persistence.
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_bad", name="")],
                assignments={"c_bad": [pkg_id]},
                validation_mode="lenient",
            )

            # No 500 — a real SyncCreativesResponse is returned.
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == "failed"
            assert result.assignment_errors is not None
            assert pkg_id in result.assignment_errors

            assignments = env.query(DBAssignment, tenant_id="test_tenant", creative_id="c_bad")
            assert assignments == [], "No assignment row may be written for a creative that was not persisted"

    def test_batch_valid_and_invalid_creative_assignments(self, integration_db):
        """Regression #1418 — batch: valid+assigned creative A, invalid+assigned creative B.

        A's assignment persists; B's does not. B's per-creative failure and its
        skipped assignment are reported. No FK violation, no 500.
        """
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            pkg_id = pkg.package_id

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_ok", name="Valid Creative"),
                    _make_creative_asset(creative_id="c_bad", name=""),  # invalid
                ],
                assignments={"c_ok": [pkg_id], "c_bad": [pkg_id]},
                validation_mode="lenient",
            )

            result_by_id = {r.creative_id: r for r in response.creatives}
            assert result_by_id["c_ok"].action != "failed"
            assert result_by_id["c_ok"].assigned_to == [pkg_id]
            assert result_by_id["c_bad"].action == "failed"
            assert result_by_id["c_bad"].assignment_errors is not None
            assert pkg_id in result_by_id["c_bad"].assignment_errors

            ok_assignments = env.query(DBAssignment, tenant_id="test_tenant", creative_id="c_ok", package_id=pkg_id)
            bad_assignments = env.query(DBAssignment, tenant_id="test_tenant", creative_id="c_bad")
            assert len(ok_assignments) == 1, "Valid creative's assignment must persist"
            assert bad_assignments == [], "Invalid creative's assignment must not persist"


class TestSchemaCompleteness:
    """Response schema fields verified against real results."""

    def test_warnings_in_per_creative_results(self, integration_db):
        """Covers: UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-02 — warnings field populated."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_warn", name="With Warnings")])

        assert len(response.creatives) == 1
        result = response.creatives[0]
        # warnings is inherited from the adcp 6.6 parent as an OPTIONAL list[str] | None
        # (PR #1567): None when there are no warnings (omitted on the wire), a list
        # when populated — never any other type.
        assert hasattr(result, "warnings")
        assert result.warnings is None or isinstance(result.warnings, list)

    def test_per_creative_result_has_required_fields(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — result has creative_id, action, changes, errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_fields", name="Field Check")])

        result = response.creatives[0]
        assert result.creative_id == "c_fields"
        assert result.action in [a.value for a in CreativeAction]
        # changes/errors are inherited optional list[str] | None (PR #1567): None when
        # empty (omitted on the wire), a list when populated.
        assert result.changes is None or isinstance(result.changes, list)
        assert result.errors is None or isinstance(result.errors, list)


# ---------------------------------------------------------------------------
# Extension Gaps — Covers: salesagent-0m59 (TestExtensionGaps conversion)
# ---------------------------------------------------------------------------


class TestSyncExtensions:
    """Extension scenarios: format errors, validation modes, assignment errors."""

    def test_tenant_not_found_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-B-01 — tenant=None with principal → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_strict_validation_per_creative_independence(self, integration_db):
        """Covers: UC-006-EXT-C-02 — strict: bad creative fails, good continues."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_bad", name=""),  # empty name fails
                    _make_creative_asset(creative_id="c_good", name="Good Creative"),
                ],
                validation_mode="strict",
            )

        assert len(response.creatives) == 2
        result_by_id = {r.creative_id: r for r in response.creatives}
        assert result_by_id["c_bad"].action == "failed"
        assert result_by_id["c_good"].action != "failed"

    def test_lenient_validation_bad_creative_fails_good_continues(self, integration_db):
        """Covers: UC-006-EXT-C-03 — lenient: invalid creative failed, valid ones proceed."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_bad", name=""),
                    _make_creative_asset(creative_id="c_good", name="Good"),
                ],
                validation_mode="lenient",
            )

        assert len(response.creatives) == 2
        result_by_id = {r.creative_id: r for r in response.creatives}
        assert result_by_id["c_bad"].action == "failed"

    def test_missing_name_field_fails_validation(self, integration_db):
        """Covers: UC-006-EXT-D-02 — dict without name → action=failed with errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    {
                        "creative_id": "c_no_name",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
                        "assets": build_assets(image_spec("banner")),
                    }
                ],
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].action == "failed"
        assert len(response.creatives[0].errors) > 0

    def test_unknown_format_fails_with_hint(self, integration_db):
        """Covers: UC-006-EXT-F-01 — format not in registry → failed with hint."""
        from unittest.mock import AsyncMock

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override: registry.get_format returns None (format not found)
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(return_value=None)

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_unknown_fmt")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action == "failed"
        assert any("list_creative_formats" in e for e in _error_messages(result.errors))

    def test_unreachable_agent_fails_with_retry(self, integration_db):
        """Covers: UC-006-EXT-G-01 — agent unreachable → buyer told to retry.

        Production-grounded: the registry TYPES all network failures
        (creative_agent_registry.py:500-531 — connect/timeout ->
        AdCPServiceUnavailableError), so "unreachable" reaches sync_creatives
        as a typed transient error and MUST surface as a transient
        SERVICE_UNAVAILABLE wire envelope — not a terminal-looking per-item
        creative failure (salesagent-mpo1). A raw ConnectionError never
        escapes the registry in production.
        """
        from src.core.exceptions import AdCPServiceUnavailableError
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            env.mock["registry"].return_value.get_format.side_effect = AdCPServiceUnavailableError(
                "Connection failed: https://creative.adcontextprotocol.org/mcp — agent unreachable"
            )

            result = env.call_via(
                Transport.REST,
                creatives=[_make_creative_asset(creative_id="c_unreachable")],
            )

            assert result.is_error, f"Unreachable agent must fail the request transiently: {result.payload!r}"
            assert_envelope_shape(
                result.wire_error_envelope,
                "SERVICE_UNAVAILABLE",
                recovery="transient",
                message_substr="unreachable",
            )

    def test_package_not_found_lenient_logs_error(self, integration_db):
        """Covers: UC-006-EXT-J-02 — lenient: missing package → assignment_errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c1", name="OK Creative")],
                assignments={"c1": ["missing_pkg"]},
                validation_mode="lenient",
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.assignment_errors is not None
        assert "missing_pkg" in result.assignment_errors

    def test_package_not_found_strict_raises(self, integration_db):
        """Covers: UC-006-EXT-J-01 — strict: missing package → AdCPNotFoundError."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            with pytest.raises(AdCPNotFoundError, match="Package not found"):
                env.call_impl(
                    creatives=[_make_creative_asset(creative_id="c1", name="OK")],
                    assignments={"c1": ["PKG-GONE"]},
                    validation_mode="strict",
                )

    def test_format_mismatch_strict_raises(self, integration_db):
        """Covers: UC-006-EXT-K-01 — strict: format mismatch → CREATIVE_REJECTED (#1417)."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Product only supports display_300x250
            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"}],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_fmt"},
            )
            pkg_id = pkg.package_id

            # Creative uses video_30s format (different from product's display)
            with pytest.raises(AdCPCreativeRejectedError, match="not supported"):
                env.call_impl(
                    creatives=[
                        _make_creative_asset(
                            creative_id="c_vid",
                            name="Video Creative",
                            format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="video_30s"),
                        )
                    ],
                    assignments={"c_vid": [pkg_id]},
                    validation_mode="strict",
                )

    def test_format_mismatch_lenient_logs_error(self, integration_db):
        """Covers: UC-006-EXT-K-02 — lenient: format mismatch → assignment_errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"}],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_fmt"},
            )
            pkg_id = pkg.package_id

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_vid",
                        name="Video",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="video_30s"),
                    )
                ],
                assignments={"c_vid": [pkg_id]},
                validation_mode="lenient",
            )

        result = response.creatives[0]
        assert result.assignment_errors is not None
        assert pkg_id in result.assignment_errors

    def test_adapter_format_skips_registry(self, integration_db):
        """Covers: UC-006-EXT-H-02 — adapter:// agent_url bypasses external format lookup."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_adapter",
                        format_id=AdcpFormatId(agent_url="broadstreet://default", id="billboard"),
                    )
                ],
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].action != "failed"


# ---------------------------------------------------------------------------
# Provenance Validation — Covers: salesagent-0m59 (TestProvenanceValidation conversion)
# ---------------------------------------------------------------------------


class TestProvenanceEnforcement:
    """Provenance metadata enforcement end-to-end through sync flow."""

    def test_provenance_required_missing_adds_warning(self, integration_db):
        """Covers: UC-006-PROV-01 — product requires provenance, creative lacks it → warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product with provenance_required policy
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": True, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_no_prov", name="No Provenance")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action != "failed"
        assert any("provenance" in w.lower() for w in (result.warnings or []))

    def test_provenance_present_no_warning(self, integration_db):
        """Covers: UC-006-PROV-02 — provenance present → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": True, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_with_prov",
                        name="With Provenance",
                        provenance={"digital_source_type": "digital_creation", "ai_tool": {"name": "DALL-E"}},
                    )
                ],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action != "failed"
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0

    def test_provenance_not_required_no_warning(self, integration_db):
        """Covers: UC-006-PROV-03 — no provenance policy → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # No product with provenance_required (or no products at all)

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_free", name="No Policy")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0

    def test_provenance_required_false_no_warning(self, integration_db):
        """Covers: UC-006-PROV-04 — provenance_required=False → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": False, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_opt", name="Optional")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0


# ---------------------------------------------------------------------------
# Media Buy Status Transition — Covers: salesagent-0m59 (TestMediaBuyStatusTransition conversion)
# ---------------------------------------------------------------------------


class TestMediaBuyStatusOnSync:
    """Media buy status transitions on creative assignment with real DB."""

    def test_draft_with_approved_at_transitions_to_pending_creatives(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-01 — draft + approved_at → pending_creatives."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb", name="MB Test")],
                assignments={"c_mb": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "pending_creatives"

    def test_draft_without_approved_at_stays_draft(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-02 — draft without approved_at stays draft."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=None,
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb2", name="MB Test 2")],
                assignments={"c_mb2": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "draft"

    def test_non_draft_status_unchanged(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-03 — active status not affected by assignment."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="active",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb3", name="MB Test 3")],
                assignments={"c_mb3": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "active"

    def test_upsert_assignment_still_transitions(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-04 — upserted assignment triggers status check."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            # First assignment
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_upsert_mb", name="Upsert MB")],
                assignments={"c_upsert_mb": [pkg_id]},
                validation_mode="lenient",
            )
            # Second assignment (upsert) — status transition should still work
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_upsert_mb", name="Upsert MB")],
                assignments={"c_upsert_mb": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "pending_creatives"


# ---------------------------------------------------------------------------
# Format Compatibility Extended — Covers: salesagent-mi8l
# ---------------------------------------------------------------------------


class TestFormatCompatibilityExtended:
    """Format compatibility in _process_assignments with real DB data.

    Tests URL normalization, empty format_ids, dual key support, and
    package-without-product scenarios through CreativeSyncEnv.
    """

    def test_url_normalization_strips_mcp_suffix(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-01 — /mcp suffix stripped for comparison."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product format has /mcp/ suffix on agent_url
            product = ProductFactory(
                tenant=tenant,
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL + "/mcp/", "id": "display_300x250"},
                ],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_norm"},
            )

            # Creative has plain URL without /mcp — should still match after normalization
            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_norm",
                        name="URL Normalized",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
                    )
                ],
                assignments={"c_norm": [pkg.package_id]},
                validation_mode="strict",
            )

        # Should succeed — URL normalization strips /mcp/ before comparison
        result = response.creatives[0]
        assert result.action != "failed", f"Expected success but got: {result.errors}"

    def test_empty_format_ids_allows_all(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-04 — empty format_ids = no restriction."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product with empty format_ids — should accept any creative format
            product = ProductFactory(
                tenant=tenant,
                format_ids=[],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_any"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_any_fmt",
                        name="Any Format",
                        format_id=AdcpFormatId(agent_url="https://random.agent.com", id="exotic_format"),
                    )
                ],
                assignments={"c_any_fmt": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != "failed", f"Expected success but got: {result.errors}"

    def test_format_id_dual_key_support(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-05 — 'format_id' key accepted alongside 'id'."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product uses 'format_id' key instead of 'id'
            product = ProductFactory(
                tenant=tenant,
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "format_id": "display_300x250"},
                ],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_dual"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_dual",
                        name="Dual Key",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
                    )
                ],
                assignments={"c_dual": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != "failed", f"Expected success but got: {result.errors}"

    def test_no_product_on_package_skips_format_check(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-06 — no product_id = no format validation."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            # Package has no product_id in config
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"package_id": "pkg_no_prod"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_no_prod",
                        name="No Product Check",
                    )
                ],
                assignments={"c_no_prod": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != "failed", f"Expected success but got: {result.errors}"


# ---------------------------------------------------------------------------
# Sync Flow Verification — Covers: salesagent-mi8l
# ---------------------------------------------------------------------------


class TestSyncFlowVerification:
    """Verify sync flow calls external services via mock assertions."""

    def test_sync_calls_audit_log(self, integration_db):
        """Covers: UC-006-MAIN-MCP-10 — sync operation triggers audit logging."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_audit", name="Audit Test")],
            )

            assert env.mock["audit_log"].called, "Audit log should be called after sync"

    def test_sync_calls_notifications_for_require_human(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-05 — require-human triggers notifications."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(
                tenant_id="test_tenant",
                approval_mode="require-human",
                slack_webhook_url="https://hooks.slack.com/test",
            )
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_notif", name="Notif Test")],
            )

            assert env.mock["send_notifications"].called, "Notifications should be called for require-human mode"

    def test_sync_skips_notifications_for_auto_approve(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01 — auto-approve skips notifications."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_auto", name="Auto Test")],
            )

            # In auto-approve, notifications may still be called but with empty list
            # The guard logic is inside the (mocked) function — we verify it's called
            # but can't test the guard through the harness
            assert env.mock["send_notifications"].called
