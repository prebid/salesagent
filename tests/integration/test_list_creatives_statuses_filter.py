"""Integration tests for the list_creatives statuses filter (#1502).

`list_creatives` accepts a structured ``CreativeFilters.statuses`` array ("match any of
these statuses"). Before this fix, ``_list_creatives_impl`` reported ``statuses`` back in
``query_summary.filters_applied`` but only ever applied the flat singular ``status`` to
the query — so a buyer could send ``filters.statuses=["approved"]``, see it echoed as
applied, and still receive creatives in other statuses. The response misrepresented what
shaped the result set.

The fix mirrors the ``concept_ids`` thread-into-query pattern (#1493): the structured
``statuses`` value (into which the flat ``status`` is already folded, flat-wins, by
``_build_list_creatives_request``) is threaded into ``CreativeRepository.get_by_principal``
and applied via ``Creative.status.in_(...)``.

Spec: AdCP 3.1.1 ``core/creative-filters.json`` — ``statuses`` is an array filter
("match any of these statuses"). Verified on every wire transport (a2a/mcp/rest); the
structured filters object reaches all three after #1493.
"""

import pytest

from tests.factories import CreativeFactory
from tests.harness import CreativeListEnv
from tests.harness.transport import ALL_WIRE, Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_creative(tenant, principal, status: str) -> str:
    """Create one creative in ``status`` and return its id. Collapses the repeated factory
    calls so the status under test is the only thing that varies per creative (DRY); the
    remaining fields (format, data with real assets) come from CreativeFactory defaults."""
    return CreativeFactory(tenant=tenant, principal=principal, status=status).creative_id


class TestStatusesFilterApplied:
    """filters.statuses actually scopes the result set — not merely reported as applied."""

    @pytest.mark.parametrize("transport", ALL_WIRE)
    def test_statuses_filter_scopes_results(self, integration_db, transport):
        """statuses=["approved"] returns only approved; a rejected creative is excluded.

        The rejected decoy is the falsifiable negative control: it is exactly what leaks
        back if the ``Creative.status.in_(...)`` clause (or the effective_statuses
        threading in _impl) is removed — with no status filter applied, both return.
        """
        with CreativeListEnv() as env:
            tenant, principal = env.setup_default_data()
            keep = _seed_creative(tenant, principal, "approved")
            _seed_creative(tenant, principal, "rejected")  # decoy — must be excluded

            result = env.call_via(transport, filters={"statuses": ["approved"]})

            assert not result.is_error, f"{transport}: {result.error!r}"
            returned = {c["creative_id"] for c in result.wire_response["creatives"]}
            assert returned == {keep}, f"{transport}: rejected decoy leaked — statuses filter not applied"

    @pytest.mark.parametrize("transport", ALL_WIRE)
    def test_multi_value_statuses_matches_any(self, integration_db, transport):
        """statuses=["approved","rejected"] returns both; a third-status creative is excluded."""
        with CreativeListEnv() as env:
            tenant, principal = env.setup_default_data()
            approved = _seed_creative(tenant, principal, "approved")
            rejected = _seed_creative(tenant, principal, "rejected")
            _seed_creative(tenant, principal, "pending_review")  # third status — excluded

            result = env.call_via(transport, filters={"statuses": ["approved", "rejected"]})

            assert not result.is_error, f"{transport}: {result.error!r}"
            returned = {c["creative_id"] for c in result.wire_response["creatives"]}
            assert returned == {approved, rejected}, f"{transport}: expected only the two matching statuses"


class TestStatusesFilterReportedTruthfully:
    """filters_applied reports statuses AND that report is now truthful — it matches the
    actually-scoped result set. REST-only: reads the real HTTP response body."""

    def test_filters_applied_matches_scoped_results(self, integration_db):
        with CreativeListEnv() as env:
            tenant, principal = env.setup_default_data()
            keep = _seed_creative(tenant, principal, "approved")
            _seed_creative(tenant, principal, "rejected")

            result = env.call_via(Transport.REST, filters={"statuses": ["approved"]})

            assert not result.is_error, result.error
            filters_applied = result.wire_response["query_summary"]["filters_applied"]
            # Reported as the enum value ("approved"), not "CreativeStatus.approved".
            assert "statuses=approved" in filters_applied, filters_applied
            # ...and the report is truthful: the scoped set matches what was claimed.
            returned = {c["creative_id"] for c in result.wire_response["creatives"]}
            assert returned == {keep}
