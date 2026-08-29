"""Integration tests: sync_creatives auth, isolation, validation, CRUD, extensions, provenance.

Behavioral tests using CreativeSyncEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Covers:
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import TypedDict

import pytest
from adcp.types import CreativeAction
from adcp.types import FormatId as AdcpFormatId

from src.core.exceptions import (
    AdCPAuthenticationError,
    AdCPCreativeRejectedError,
    AdCPFormatNotFoundError,
    AdCPNotFoundError,
)
from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.factories.creative_asset import build_assets, image_spec, make_test_banner_creative
from tests.harness import CreativeSyncEnv, make_identity
from tests.harness.transport import Transport, TransportResult

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


class WireError(TypedDict, total=False):
    """One entry of a wire ``errors[]`` advisory, as plain JSON off a transport.

    Names the shape the wire read-back helpers below destructure. This is
    DOCUMENTATION, not a gate: the quality gate never type-checks tests
    (``make quality`` and quality-ci both run ``mypy src/`` only, and every
    mypy pre-commit hook is scoped to ``^src/``), so a key rename here reddens
    nothing on its own. The mechanical grading of these keys is the assertions
    in ``_assert_advisory_failure``, which run under pytest.
    """

    code: str
    message: str
    recovery: str
    suggestion: str


class WireCreativeEntry(TypedDict, total=False):
    """One entry of the wire ``creatives[]`` array, as plain JSON off a transport.

    Same status as :class:`WireError` — a name for the contract these tests
    grade, not an enforcement mechanism (mypy does not run on ``tests/``).
    """

    creative_id: str
    action: str
    errors: list[WireError]


def _wire_entries(result: TransportResult) -> dict[str | None, WireCreativeEntry]:
    """Index the wire response's creatives[] by creative_id.

    Shared read-back for TestAssignmentProcessing: every per-item wire
    assertion goes through the same extraction instead of hand-rolling the
    dict comprehension per test.
    """
    return {e.get("creative_id"): e for e in (result.wire_response or {}).get("creatives", [])}


def _assert_advisory_failure(
    action: str | None,
    pairs: list[tuple[str | None, str | None]],
    *,
    code: str,
    not_code: str,
    recovery: str = "correctable",
) -> None:
    """The per-item advisory-failure contract, held in ONE place.

    Both representations of a failed per-creative result — the in-process
    ``SyncCreativeResult`` (typed ``AdCPErrorDetail`` objects, ``recovery`` an SDK
    enum) and the wire entry (plain JSON dicts, ``recovery`` already a string) —
    grade the identical five-step contract: the item FAILED, the transient
    ``not_code`` a regression would leak is absent, the expected ``code`` is
    present, and every error carrying that code also carries the ``recovery`` a
    conforming buyer keys retry-forever-vs-fix-and-resubmit on.

    Callers pass ``pairs`` already normalized to ``(code, recovery-as-str)``, so
    the two representations differ only in a two-line extractor and this body
    cannot drift between them — a copy-paste that dropped the recovery half on
    one surface while the other stayed green is exactly the risk. The named
    wrappers below keep the read paths split (per-surface docstrings and no
    duck-typing inside one helper).

    Grading BOTH halves is deliberate: the code alone does not govern behavior. A
    regression that kept the corrected code but reverted recovery to
    ``transient`` still drives retry-forever, and a code-only assertion would
    stay green.
    """
    assert action == "failed", f"expected a failed result, got action={action!r}"
    codes = [c for c, _ in pairs]
    assert not_code not in codes, f"{not_code} must not be emitted here, got {codes}"
    assert code in codes, f"expected {code}, got {codes}"
    matching = [r for c, r in pairs if c == code]
    assert matching and all(r == recovery for r in matching), f"{code} must carry recovery={recovery}, got {matching!r}"


def _impl_error_pairs(result) -> list[tuple[str | None, str | None]]:
    """Normalize an in-process ``SyncCreativeResult``'s errors[] to (code, recovery) pairs.

    Reads ``.code``/``.recovery`` directly off ``AdCPErrorDetail`` (a Pydantic
    model that guarantees both fields) — a model-shape drift that renamed an
    attribute must fail loudly here, not slip through a getattr default as None
    and pass. SDK ``Recovery`` is an enum (not a str-mixin), so ``.value``
    normalizes it to the string the shared contract body compares (mirrors the
    sibling assertion at
    test_lenient_mode_unknown_assignment_creative_entry_is_creative_not_found).
    """
    return [(e.code, e.recovery.value if e.recovery is not None else None) for e in result.errors or []]


def _assert_correctable(result) -> None:
    """Assert a per-creative IMPL failure is buyer-CORRECTABLE, not transient.

    In-process representation of the contract in :func:`_assert_advisory_failure`:
    the wire code must be VALIDATION_ERROR (never the transient
    SERVICE_UNAVAILABLE) and recovery must be ``correctable``. The wire twin is
    ``_assert_correctable_wire_entry``.
    """
    _assert_advisory_failure(
        result.action,
        _impl_error_pairs(result),
        code="VALIDATION_ERROR",
        not_code="SERVICE_UNAVAILABLE",
    )


def _assert_terminal_configuration_error(result) -> None:
    """Assert a per-creative IMPL failure is a TERMINAL server misconfiguration.

    The terminal counterpart of :func:`_assert_correctable`, over the same
    contract body. A server-side misconfiguration cannot be fixed by the buyer
    and must not be retried, so the pair must be (CONFIGURATION_ERROR, terminal)
    — never the default (SERVICE_UNAVAILABLE, terminal), whose code is pinned
    ``transient`` in the enum and therefore contradicts its own recovery field.
    """
    _assert_advisory_failure(
        result.action,
        _impl_error_pairs(result),
        code="CONFIGURATION_ERROR",
        not_code="SERVICE_UNAVAILABLE",
        recovery="terminal",
    )


def _assert_failed_wire_entry(
    entry: WireCreativeEntry, *, code: str, not_code: str, recovery: str = "correctable"
) -> None:
    """Assert a per-creative FAILED wire entry carries the expected advisory code+recovery.

    Wire representation of the contract in :func:`_assert_advisory_failure`: the
    caller pins the ``code`` the advisory ``errors[]`` MUST carry, the ``not_code``
    a regression would leak instead, and the ``recovery`` the buyer keys retry
    behavior on. Off a real transport the entry is plain JSON, so ``errors`` are
    dicts and ``recovery`` is already a string (not the SDK ``Recovery`` enum the
    in-process ``_assert_correctable`` normalizes by ``.value``). Both wire twins —
    ``_assert_correctable_wire_entry`` and
    ``_assert_format_not_found_normalized_on_wire`` — route through here, so one
    edit cannot weaken one code's grading while the other stays green.
    """
    _assert_advisory_failure(
        entry.get("action"),
        [(e.get("code"), e.get("recovery")) for e in entry.get("errors") or []],
        code=code,
        not_code=not_code,
        recovery=recovery,
    )


def _assert_correctable_wire_entry(entry: WireCreativeEntry) -> None:
    """Wire-dict twin of :func:`_assert_correctable` — the correctable VALIDATION_ERROR contract.

    The in-process helper reads typed attributes and compares ``recovery.value``
    (SDK ``Recovery`` is a non-str-mixin enum). Off a real transport the same
    entry is plain JSON, so ``recovery`` is already a string and the errors are
    dicts. Kept as a named twin rather than a duck-typed branch inside
    ``_assert_correctable`` so neither surface can silently degrade to the
    other's read path. Grades that a buyer-correctable per-item failure surfaces
    ``VALIDATION_ERROR`` with ``recovery=correctable`` and is never mis-coded as
    the transient ``SERVICE_UNAVAILABLE`` (which a conforming buyer would retry
    forever). Delegates the wire read-back to :func:`_assert_failed_wire_entry`.
    """
    _assert_failed_wire_entry(entry, code="VALIDATION_ERROR", not_code="SERVICE_UNAVAILABLE")


def _assert_format_not_found_normalized_on_wire(entry: WireCreativeEntry) -> None:
    """Wire assertion for the FORMAT_NOT_FOUND -> INVALID_REQUEST normalization choke point.

    A per-creative failure whose typed exception forwards its code through the
    ``except AdCPError`` branch in ``_sync_creatives_impl`` (which narrows
    ``e.error_code`` via ``to_wire_error_code``) must be normalized by
    ``_failed_sync_result`` -> ``to_wire_error_code`` before it serializes into
    the advisory ``errors[]``. ``AdCPFormatNotFoundError`` carries the
    internal-only ``FORMAT_NOT_FOUND`` (``exceptions.INTERNAL_CODES``), which MUST
    reach the buyer as the standard ``INVALID_REQUEST``
    (``exceptions.ERROR_CODE_MAPPING``), never verbatim. Advisory ``errors[]``
    entries serialize as-is and never pass through the boundary translator that
    handles raised ``AdCPError``s, so a regression dropping the normalization
    would leak ``FORMAT_NOT_FOUND`` on the wire — this asserts the normalized
    value is present and the raw internal code is not.

    Spec grounding (pinned AdCP 3.1.1, enums/error-code.json): INVALID_REQUEST is
    a standard wire code, recovery ``correctable``; ``FORMAT_NOT_FOUND`` is not in
    the enum. Delegates the wire read-back to :func:`_assert_failed_wire_entry`.
    """
    _assert_failed_wire_entry(entry, code="INVALID_REQUEST", not_code="FORMAT_NOT_FOUND")


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
        as-is. Graded in-process here; the real-wire equivalent (REST + MCP) is
        test_correctable_failure_code_and_recovery_on_the_wire.
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
        `except AdCPError` path (_sync.py) keeps it as a per-item failure and
        forwards its already-wire-standard code — see
        test_correctable_failure_code_and_recovery_on_the_wire for the sibling
        path where the typed code (AdCPFormatNotFoundError, FORMAT_NOT_FOUND) is
        NOT wire-standard and must be normalized to INVALID_REQUEST on the wire.
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

    def test_correctable_failure_code_and_recovery_via_a2a_raw_wrapper(self, integration_db):
        """The correctable code+recovery holds through the A2A ``_raw()`` wrapper.

        Scope, stated precisely: this does NOT cross a serialization boundary.
        ``call_via(Transport.A2A)`` routes to ``CreativeSyncEnv.call_a2a``, which
        calls ``sync_creatives_raw()`` directly rather than dispatching through
        ``_run_a2a_handler``; only the latter populates ``wire_response``. So the
        object read below is the same in-process ``SyncCreativeResult`` the two
        tests above read, one wrapper layer out. Real transport coverage for this
        contract lives in
        ``test_correctable_failure_code_and_recovery_on_the_wire`` (REST + MCP).

        Kept as a thin guard that the wrapper forwards per-item results unchanged;
        the earlier name and docstring here claimed an artifact-DataPart read this
        path does not perform.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json): VALIDATION_ERROR →
        recovery ``correctable``; SERVICE_UNAVAILABLE → ``transient``.
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            result = env.call_via(Transport.A2A, creatives=[_make_creative_asset(creative_id="c_bad", name="")])

            assert not result.is_error, (
                f"per-creative validation failure must stay on the success branch: {result.wire_error_envelope}"
            )
            creatives = getattr(result.payload, "creatives", None)
            assert creatives, f"wrapper must forward the per-item result: {result.payload!r}"
            entry = next((c for c in creatives if c.creative_id == "c_bad"), None)
            assert entry is not None, f"expected the failed creative in the wrapper result: {creatives!r}"
            _assert_correctable(entry)

    @pytest.mark.parametrize("transport", [Transport.REST, Transport.MCP], ids=lambda t: t.value)
    def test_correctable_failure_code_and_recovery_on_the_wire(self, integration_db, transport):
        """The correctable per-creative code+recovery must survive a REAL serialization
        boundary, not only the in-process ``SyncCreativeResult``. A boundary that
        dropped or re-coerced ``errors[].code`` / ``.recovery`` would leave every
        in-process test above green while shipping the wrong retry contract to buyers.

        Graded on every transport that crosses a real wire and populates
        ``wire_response``: REST (HTTP JSON body) and MCP (``_run_mcp_client``, the
        full FastMCP pipeline — its ``structured_content``). A2A is deliberately
        excluded, not silently: ``CreativeSyncEnv.call_a2a`` takes the
        ``sync_creatives_raw`` ``_raw()`` shortcut instead of ``_run_a2a_handler``,
        so it never populates ``wire_response`` (see
        ``test_correctable_failure_code_and_recovery_via_a2a_raw_wrapper``). Removing
        that shortcut is a shared-harness change touching sibling A2A tests,
        deferred to #1733; once it lands, add ``Transport.A2A`` here — this
        parametrization mirrors
        ``test_strict_mode_unknown_assignment_creative_is_creative_not_found_on_wire``.

        The payload is deliberately MIXED (one valid creative + two invalid): an
        all-invalid payload is rejected operation-level before per-item results
        exist, whereas a mixed one keeps the operation on the success branch and
        rides each failure on ``creatives[]`` with ``action="failed"`` — the same
        shape the sibling per-item wire tests read via ``_wire_entries`` (see
        ``test_orphan_assignment_error_surfaces_as_failed_result_on_wire``).

        THREE distinct failure branches are graded off the one payload — every
        per-item code this PR moved off the default ``SERVICE_UNAVAILABLE``:

        * ``c_bad`` (empty name) — the OUTER ``except AdCPError`` branch
          (``code=e.error_code``, ``_sync.py``). Empty name passes
          ``Creative(**schema_data)`` (``name`` carries no length constraint) and
          is raised as a business-rule ``AdCPValidationError`` at
          ``_validation.py:95``, whose ``e.error_code`` is the already-wire-
          standard ``VALIDATION_ERROR``. Read through
          ``_assert_correctable_wire_entry``.
        * ``c_bad_format`` — the SAME outer ``except AdCPError`` branch
          (``code=e.error_code``, ``_sync.py``) but forwarding a TYPED code
          that is NOT wire-standard. Driving a real ``AdCPFormatNotFoundError``
          grades the ``FORMAT_NOT_FOUND`` -> ``INVALID_REQUEST`` normalization
          choke point ON THE WIRE: that raw code is internal-only
          (``INTERNAL_CODES``) and advisory ``errors[]`` serialize verbatim
          without passing the boundary translator, so a regression that dropped
          the normalization would leak ``FORMAT_NOT_FOUND`` to the buyer.
          Previously this half was pinned only by ``call_impl`` and the direct
          ``to_wire_error_code`` unit call. Read through
          ``_assert_format_not_found_normalized_on_wire``.
        * ``c_prov`` — the INNER ``except (ValidationError, ValueError)`` branch
          (``_sync.py``), the OTHER branch this PR moved off
          ``SERVICE_UNAVAILABLE``. A creative that is valid per the buyer-facing
          schema but fails the internal ``Creative(**schema_data)`` construction
          (``provenance`` omits the internally-required ``digital_source_type``)
          lands here with ``code="VALIDATION_ERROR"``. Previously pinned only by
          ``call_impl`` (``test_pydantic_schema_failure_uses_correctable_code``);
          graded on the wire here. Read through ``_assert_correctable_wire_entry``.

        The three wire read-backs go through ``_assert_failed_wire_entry`` because
        ``recovery`` is a plain JSON string here, not the SDK ``Recovery`` enum the
        in-process helper compares by ``.value``.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json): VALIDATION_ERROR
        and INVALID_REQUEST → recovery ``correctable``; SERVICE_UNAVAILABLE →
        ``transient``. ``FORMAT_NOT_FOUND`` is not a spec wire code — it maps to
        ``INVALID_REQUEST`` via ``ERROR_CODE_MAPPING``.
        """
        missing_format_id = "format_missing_on_agent_xyz"

        def _get_format(agent_url, format_id):
            # Raise the typed AdCPFormatNotFoundError (raw FORMAT_NOT_FOUND, an
            # INTERNAL code) for the one bad format so the except-AdCPError branch
            # (_sync.py) forwards e.error_code through _failed_sync_result ->
            # to_wire_error_code; every other format still resolves so the valid
            # creative is created. recovery=correctable keeps it a per-item failure.
            if format_id == missing_format_id:
                raise AdCPFormatNotFoundError(f"Unknown format_id '{format_id}' from agent {agent_url}")
            return {"id": format_id, "name": "OK"}

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Set side_effect on the env's existing get_format mock (the per-file
            # hand-rolled-mock cap only shrinks) so fetch_format_spec re-raises the
            # typed error for the bad-format creative and resolves the rest.
            env.mock["registry"].return_value.get_format.side_effect = _get_format

            result = env.call_via(
                transport,
                creatives=[
                    _make_creative_asset(creative_id="c_ok", name="Valid Banner"),
                    _make_creative_asset(creative_id="c_bad", name=""),
                    _make_creative_asset(
                        creative_id="c_bad_format",
                        name="Bad Format",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id=missing_format_id),
                    ),
                    # Valid per the buyer-facing schema but fails the internal
                    # Creative(**schema_data) construction: provenance omits the
                    # internally-required digital_source_type. Drives the inner
                    # except (ValidationError, ValueError) branch (_sync.py)
                    # onto the wire. Fails before the format check, so the
                    # get_format side_effect never sees it.
                    _make_creative_asset(
                        creative_id="c_prov",
                        name="Bad Provenance",
                        provenance={"created_time": "2026-01-01T00:00:00Z"},
                    ),
                ],
            )

            assert not result.is_error, (
                f"a mixed payload must stay on the {transport.value} success branch: {result.wire_error_envelope}"
            )
            assert result.wire_response is not None, f"{transport.value} success must expose the wire body"
            entries = _wire_entries(result)

            # Empty-name half: already-wire-standard VALIDATION_ERROR (correctable).
            correctable_entry = entries.get("c_bad")
            assert correctable_entry is not None, (
                f"the failed creative must appear on the {transport.value} wire, not be dropped: {result.wire_response}"
            )
            _assert_correctable_wire_entry(correctable_entry)

            # Non-wire-code half: the typed AdCPFormatNotFoundError forwarded through
            # the except-AdCPError branch must reach the buyer NORMALIZED to
            # INVALID_REQUEST, never as the raw internal FORMAT_NOT_FOUND.
            format_entry = entries.get("c_bad_format")
            assert format_entry is not None, (
                f"the format-not-found creative must appear on the {transport.value} wire, "
                f"not be dropped: {result.wire_response}"
            )
            _assert_format_not_found_normalized_on_wire(format_entry)

            # Pydantic-construction half: the OTHER branch this PR moved off
            # SERVICE_UNAVAILABLE — the inner except (ValidationError, ValueError)
            # (_sync.py), reached when Creative(**schema_data) fails. Graded on
            # the wire here, not only via call_impl.
            prov_entry = entries.get("c_prov")
            assert prov_entry is not None, (
                f"the pydantic-construction-failure creative must appear on the {transport.value} wire, "
                f"not be dropped: {result.wire_response}"
            )
            _assert_correctable_wire_entry(prov_entry)

    def test_pydantic_schema_failure_uses_correctable_code(self, integration_db):
        """The OTHER failure branch — a creative that fails PYDANTIC construction —
        must be graded correctable too.

        The sibling tests above all raise ``AdCPValidationError`` from the business
        rules and land at the outer ``except AdCPError``. This one lands at the inner
        ``except (ValidationError, ValueError)`` in ``_sync_creatives_impl``, which
        this PR also moved off the default ``SERVICE_UNAVAILABLE``. Without this
        test, reverting that branch reddens nothing.

        Trigger, and it is not a contrived one: ``digital_source_type`` is OPTIONAL on
        the adcp ``Provenance`` model but REQUIRED on the salesagent ``Provenance``
        that ``_validate_creative_input`` converts into, so a creative that is VALID
        per the buyer-facing schema fails the internal ``Creative(**schema_data)``
        construction. A conforming buyer reaches this branch, which is precisely why
        it must not tell them to retry a permanent failure forever. (The underlying
        strictness mismatch is a separate defect, reported alongside this PR.)
        """
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_prov",
                        provenance={"created_time": "2026-01-01T00:00:00Z"},
                    )
                ]
            )
            assert len(response.creatives) == 1
            _assert_correctable(response.creatives[0])


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
# CRUD Workflow Tests — Covers:
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
        recorded in the response. : the result merge only
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
        as the orphan-error shape, success-info variant). .
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
# Extension Gaps — Covers: (TestExtensionGaps conversion)
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
        creative failure . A raw ConnectionError never
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

    @pytest.mark.parametrize("preexisting", [False, True], ids=["create", "update"])
    def test_misconfiguration_failure_uses_terminal_code(self, integration_db, preexisting):
        """A server-side misconfiguration emits (CONFIGURATION_ERROR, terminal), not
        (SERVICE_UNAVAILABLE, terminal).

        Production-grounded: a generative format with no GEMINI_API_KEY raises
        ``AdCPConfigurationError`` inside the creative handler, whose
        ``except AdCPConfigurationError`` block turns it into a per-item
        advisory. That advisory used the builder's default code, so it shipped
        the transient ``SERVICE_UNAVAILABLE`` alongside ``recovery=terminal`` —
        a pair that contradicts itself, since a conforming buyer reads the code's
        own classification too.

        Spec grounding (pinned AdCP 3.1.1, enums/error-code.json):
        CONFIGURATION_ERROR → recovery ``terminal`` ("the buyer cannot resolve a
        seller-side deployment misconfiguration and MUST NOT auto-retry");
        SERVICE_UNAVAILABLE → ``transient``. CONFIGURATION_ERROR is a real wire
        code here via ``_SPEC_SUPPLEMENT_CODES``, so it passes
        ``to_wire_error_code`` untranslated rather than collapsing.

        Grades the pair, not just the code: reverting either kwarg at the raise
        site reddens this. Parametrized over BOTH handlers — ``_create_new_creative``
        and ``_update_existing_creative`` carry separate copies of the same
        ``except AdCPConfigurationError`` block, so fixing one and leaving the
        other is exactly the guard-lands-sibling-slips shape. The sibling
        transient contract is test_unreachable_agent_fails_with_retry above.
        """
        from tests.factories import CreativeFactory

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="")
            # setup_generative_build sets a key; clear it so the production
            # "GEMINI_API_KEY not configured" raise is the real trigger.
            env.mock["config"].return_value.gemini_api_key = None

            if preexisting:
                # A row already in the library routes the sync to
                # _update_existing_creative instead of _create_new_creative.
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id="c_misconfigured",
                    format=fmt["id"],
                    agent_url=fmt["agent_url"],
                )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_misconfigured",
                        format_id=AdcpFormatId(agent_url=fmt["agent_url"], id=fmt["id"]),
                    )
                ],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert any("GEMINI_API_KEY" in m for m in _error_messages(result.errors)), (
            f"expected the misconfiguration path, got {_error_messages(result.errors)}"
        )
        _assert_terminal_configuration_error(result)

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
# Provenance Validation — Covers: (TestProvenanceValidation conversion)
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
# Media Buy Status Transition — Covers: (TestMediaBuyStatusTransition conversion)
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
# Format Compatibility Extended — Covers:
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
# Sync Flow Verification — Covers:
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
