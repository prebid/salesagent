"""Integration tests: list_creatives must not silently fabricate a creative's status.

``src/core/tools/creatives/listing.py`` catches ``ValueError`` when the stored
``creatives.status`` string is not a member of the AdCP ``CreativeStatus`` enum and
substitutes a member of its own choosing.  Before this fix it substituted
``CreativeStatus.pending_review`` with no log and no advisory.

Why the substitution is not benign, per the pinned spec
-------------------------------------------------------
AdCP 3.1.1 ``dist/schemas/3.1.1/enums/creative-status.json`` defines ``pending_review``
as "Creative has passed processing and is awaiting platform content policy review.
Transitions to approved or rejected after review."  That is a specific, actionable
lifecycle claim: processing succeeded, the creative is not deliverable, the *seller*
owes the next transition, and the buyer need do nothing.  Asserting it for a value the
reader could not parse states a position the seller never took.

``list-creatives-response.json`` makes ``status`` REQUIRED on every creative item and
``$ref``s the closed 6-member enum — there is no "unknown" member and the field cannot
be omitted.  The same response carries an ``errors[]`` array ("Task-specific errors")
on the success path, which is the spec-provisioned channel for telling the buyer that
a record could not be fully represented.

The substitution can also erase a seller obligation: the ``suspended`` and ``rejected``
descriptions both say "Sellers MUST surface a corresponding impairment on any active
media buy that references this creative".  A stored status that degrades to
``pending_review`` drops that MUST silently.

Why the bad-data path is reachable
----------------------------------
``"pending"`` — not a member of ``CreativeStatus`` — *was* this system's own default in
three places (the ``creatives.status`` column, ``CreativeRepository.create``'s ``status``
parameter, and ``CreativeFactory``), so every row written through the production write
API with the field omitted read back as a fabricated status.  ``TestWriteSideDefault``
grades that route directly, and it is the route this change repairs: the default is now
a spec member, and a data migration rewrites the rows already written.

The READ path stays reachable independently of any default, which is why the three
read-path tests below seed an explicitly alien status rather than relying on one:

* a status added by a future AdCP version, read by a seller still on this pin;
* an operator write — ``creatives.status`` is plain ``String(50)`` with no CHECK
  constraint and no PostgreSQL enum type, so the database accepts any string;
* a row written by an earlier version of this service.

Seeding an alien value explicitly also keeps the read-path tests honest: if they took
the repository default, fixing that default would make them pass vacuously (the
``except`` branch would never run) rather than grade the reader.
"""

from __future__ import annotations

from typing import Any

import pytest
from adcp.types import CreativeStatus

from src.core.database.repositories.uow import CreativeUoW
from tests.harness import CreativeListEnv
from tests.harness.transport import Transport
from tests.helpers.log_capture import capture_logs

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_ALL_WIRE = [Transport.A2A, Transport.MCP, Transport.REST]

_SPEC_STATUSES = {member.value for member in CreativeStatus}

#: A status no AdCP version defines and no writer in this codebase produces. Chosen so
#: the read-path tests keep grading the reader after the write-side default and the data
#: migration have removed ``"pending"`` from the system entirely.
_ALIEN_STATUS = "quarantined"

#: Sentinel: omit ``status=`` from the repository call entirely, taking its default.
_REPOSITORY_DEFAULT = object()


def _seed_creative(env: CreativeListEnv, *, name: str, status: Any = _REPOSITORY_DEFAULT) -> tuple[str, str]:
    """Create a creative through the production repository. Returns ``(creative_id, stored_status)``.

    When ``status`` is left at ``_REPOSITORY_DEFAULT`` no ``status=`` argument reaches
    ``CreativeRepository.create``, so the row takes the repository's own default — the
    same value a production caller that omits the field writes.
    """
    tenant, principal = env.setup_default_data()

    status_kwargs = {} if status is _REPOSITORY_DEFAULT else {"status": status}
    with CreativeUoW(tenant.tenant_id) as uow:
        assert uow.creatives is not None
        db_creative = uow.creatives.create(
            name=name,
            agent_url="https://creative.adcontextprotocol.org",
            format="display_300x250",
            principal_id=principal.principal_id,
            data={"assets": {}},
            **status_kwargs,
        )
        return db_creative.creative_id, db_creative.status


def _seed_creative_with_repository_default_status(env: CreativeListEnv) -> tuple[str, str]:
    """Seed a creative taking ``CreativeRepository.create``'s own default status."""
    return _seed_creative(env, name="Creative with repository-default status")


def _seed_creative_with_alien_status(env: CreativeListEnv, status: str = _ALIEN_STATUS) -> tuple[str, str]:
    """Seed a creative whose stored status the reader cannot parse, independent of any default."""
    return _seed_creative(env, name=f"Creative stored as {status}", status=status)


class TestWriteSideDefault:
    """The repository's own default must be a status the buyer-facing reader can parse."""

    def test_repository_default_status_is_a_spec_status(self, integration_db):
        """CreativeRepository.create's default lands in the DB as a CreativeStatus member.

        This is the concrete route by which an unreadable status reached the reader:
        the default used to be ``"pending"``, which AdCP 3.1.1 does not define.
        """
        with CreativeListEnv() as env:
            _creative_id, stored_status = _seed_creative_with_repository_default_status(env)

        assert stored_status in _SPEC_STATUSES, (
            f"CreativeRepository.create persisted status {stored_status!r}, which is not one of "
            f"the AdCP 3.1.1 creative statuses {sorted(_SPEC_STATUSES)}; every row written through "
            f"the repository default is therefore unreadable to list_creatives"
        )


class TestUnrecognizedStoredStatus:
    """An unparseable stored status must be surfaced, never silently asserted as a real one."""

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_unreadable_status_is_not_reported_as_pending_review(self, integration_db, transport):
        """The buyer must not be told ``pending_review`` for a status the seller never asserted.

        ``pending_review`` claims processing completed and that a seller review decision
        is pending — a lifecycle position nobody took for a value the reader could not read.
        """
        with CreativeListEnv() as env:
            creative_id, stored_status = _seed_creative_with_alien_status(env)

            result = env.call_via(transport)

            assert not result.is_error, f"{transport}: listing errored: {result.error!r}"
            wire_creatives = result.wire_response["creatives"]
            assert len(wire_creatives) == 1, (
                f"{transport}: the creative must still be returned — a record the seller cannot "
                f"describe is not a record that stops existing; got {wire_creatives!r}"
            )
            record = wire_creatives[0]
            assert record["creative_id"] == creative_id
            assert record["status"] != CreativeStatus.pending_review.value, (
                f"{transport}: stored status {stored_status!r} could not be parsed, yet the buyer "
                f"is told {record['status']!r} — a specific lifecycle claim (processing complete, "
                f"seller review pending, buyer action not required) the seller never asserted"
            )

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_unreadable_status_is_surfaced_to_the_buyer(self, integration_db, transport):
        """The response tells the buyer which creative could not be described.

        ``list-creatives-response.json`` carries ``errors[]`` on the success path for exactly
        this: the read succeeds, but one record is not fully representable.
        """
        with CreativeListEnv() as env:
            creative_id, _stored_status = _seed_creative_with_alien_status(env)

            result = env.call_via(transport)

            assert not result.is_error, f"{transport}: listing errored: {result.error!r}"
            errors = result.wire_response.get("errors") or []
            assert any(creative_id in str(entry) for entry in errors), (
                f"{transport}: an unreadable stored status was substituted silently — the response "
                f"must name creative {creative_id} in errors[]; got errors={errors!r}"
            )

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_advisory_is_terminal_configuration_error(self, integration_db, transport):
        """The advisory must not tell the buyer to retry a permanently bad row.

        ``normalize_advisory_errors`` collapses any non-standard code to
        ``SERVICE_UNAVAILABLE`` with ``recovery="transient"`` — i.e. "retry me" — which
        for a seller-side data defect means polling forever.  AdCP 3.1.1
        ``enums/error-code.json`` gives ``CONFIGURATION_ERROR`` ``recovery: terminal``
        and the remediation "surface to a human at the seller".
        """
        with CreativeListEnv() as env:
            creative_id, _stored_status = _seed_creative_with_alien_status(env)

            result = env.call_via(transport)

            assert not result.is_error, f"{transport}: listing errored: {result.error!r}"
            errors = result.wire_response.get("errors") or []
            advisories = [entry for entry in errors if creative_id in str(entry)]
            assert len(advisories) == 1, (
                f"{transport}: expected exactly one advisory naming creative {creative_id}; got {errors!r}"
            )
            advisory = advisories[0]
            assert advisory["code"] == "CONFIGURATION_ERROR", (
                f"{transport}: advisory code is {advisory['code']!r}; a code outside the wire-standard "
                f"set collapses to SERVICE_UNAVAILABLE and instructs the buyer to retry a row only the "
                f"seller can repair"
            )
            assert advisory["recovery"] == "terminal", (
                f"{transport}: advisory recovery is {advisory['recovery']!r}, so the buyer is told to "
                f"retry; a seller-side data defect is terminal for the buyer"
            )

    def test_unreadable_status_is_logged_at_warning(self, integration_db):
        """The substitution is visible to the operator (No Quiet Failures).

        The sibling handler in the same module (``_coerce_concept_value``) already logs its
        drop at WARNING; this one logged nothing, so a table full of unreadable statuses
        produced no signal anywhere.
        """
        with CreativeListEnv() as env:
            creative_id, stored_status = _seed_creative_with_alien_status(env)

            # capture_logs attaches to the producing module's logger rather than using
            # caplog: caplog's root-level handler is lost when suite-level code
            # reconfigures root handlers, which makes such assertions order-dependent.
            with capture_logs("src.core.tools.creatives.listing") as handler:
                result = env.call_via(Transport.REST)

            assert not result.is_error, f"listing errored: {result.error!r}"
            assert any(creative_id in record and stored_status in record for record in handler.records), (
                f"no WARNING names creative {creative_id} and its unparseable stored status "
                f"{stored_status!r}; captured={handler.records!r}"
            )


class TestFilteredReadExcludesTheUnreadableRow:
    """A status-filtered read answers the question the buyer asked, and says so at the site.

    ``CreativeRepository.get_by_principal`` filters on the RAW persisted string, while the
    placeholder the reader renders is chosen at read time — so a row stored as an alien
    value is returned by an unfiltered read (rendered ``processing``) and is ABSENT from
    ``list_creatives(filters={"statuses": ["processing"]})``.  That asymmetry is deliberate: a filtered read
    is scoped to a status the buyer NAMED, and an unreadable row is not known to be that
    status.  It is pinned here so it is a graded choice rather than an accident.
    """

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_status_filter_returns_the_genuine_row_only(self, integration_db, transport):
        with CreativeListEnv() as env:
            alien_id, _alien_status = _seed_creative_with_alien_status(env)
            genuine_id, _genuine_status = _seed_creative(
                env,
                name="Genuinely processing creative",
                status=CreativeStatus.processing.value,
            )

            # AdCP 3.1.1 spells this filters.statuses (PLURAL, an array):
            # creative-filters.json declares `statuses` and no `status`, and
            # list-creatives-request.json has no top-level status at all. The flat
            # `status=` this used to pass was retired with the rest of the non-spec surface.
            result = env.call_via(transport, filters={"statuses": [CreativeStatus.processing.value]})

            assert not result.is_error, f"{transport}: listing errored: {result.error!r}"
            returned = {record["creative_id"] for record in result.wire_response["creatives"]}
            assert genuine_id in returned, (
                f"{transport}: the genuinely 'processing' creative {genuine_id} is missing from a "
                f"status='processing' read; got {returned!r} — without it this test could pass on an "
                f"empty result and grade nothing"
            )
            assert alien_id not in returned, (
                f"{transport}: creative {alien_id} is stored with an unreadable status, so it is not "
                f"KNOWN to be 'processing'; returning it for status='processing' answers a question "
                f"the buyer did not ask; got {returned!r}"
            )
