"""Integration tests for the list_creatives statuses filter (#1502).

`list_creatives` accepts a structured ``CreativeFilters.statuses`` array with match-any
semantics — a creative matches if its status is any one of the requested statuses. Before
this fix, ``_list_creatives_impl`` derived the DB-query status from only the FIRST element
of the structured list (``enum_value(req_filters.statuses[0])``) while
``query_summary.filters_applied`` echoed the whole array — so a buyer sending a multi-status
filter like ``["approved", "rejected"]`` had it silently narrowed to ``["approved"]`` and
received FEWER creatives than ``filters_applied`` claimed. The report did not match the
scoped result set.

The fix mirrors the ``concept_ids`` thread-into-query pattern (#1407): the structured
``statuses`` value (into which the flat ``status`` is already folded, flat-wins, by
``_build_list_creatives_request``) is threaded in full into
``CreativeRepository.get_by_principal`` and applied via ``Creative.status.in_(...)``.

The ``statuses`` match-any semantics and their spec grounding are stated once at the
enforcement site — ``CreativeRepository.get_by_principal``'s ``Creative.status.in_(...)``
in ``src/core/database/repositories/creative.py``; see that comment rather than
re-deriving it here. Verified on every wire transport (a2a/mcp/rest); the structured
filters object reaches all three after #1407.
"""

import pytest

from tests.harness import CreativeListEnv
from tests.harness.transport import ALL_WIRE, TransportResult
from tests.helpers.creative_test_helpers import assert_empty_array_filter_rejected, seed_creative_in_status

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _returned_creative_ids(result: TransportResult) -> set[str]:
    """The set of creative_ids in the success-path wire response.

    Reads through the guarded ``require_wire()`` accessor so a wire-absent path fails
    transport-named instead of raising an opaque ``TypeError`` on ``None`` subscript.
    """
    return {c["creative_id"] for c in result.require_wire()["creatives"]}


class TestStatusesFilterScopesAndReportsTruthfully:
    """filters.statuses scopes the result set AND filters_applied reports the scoped set
    truthfully — one scaffold, two arities.

    Both arms seed a known status mix, dispatch a ``statuses`` filter, and assert the
    scoped set equals exactly the seeded creatives whose status is in the filter, plus the
    ``filters_applied`` report matches. They differ ONLY in array arity, so they are one
    parametrized test rather than two copy-pasted classes (CLAUDE.md DRY):

    - Multi-value ``["approved","rejected"]`` grades BOTH halves of the #1502 regression.
      Scoping: pre-fix the array was narrowed to its first element (``["approved"]``),
      dropping the rejected row → the scoped-set assertion reddens. Reporting:
      ``filters_applied`` echoed the whole array while the query used one status → the
      full-list ``filters_applied`` assertion reddens on a regression that emits only the
      first status. Reverting the full-list threading (back to ``statuses[0]``) reddens both.
    - Single-value ``["approved"]`` guards the report-is-truthful property (report == scoped
      set); it cannot catch the regression (report and result already agree at arity 1).

    Parametrized over every wire transport (a2a/mcp/rest): the three paths are not
    interchangeable — input coercion differs (``coerce_creative_filters`` for REST/A2A vs the
    FastMCP TypeAdapter), each wrapper forwards a different subset of ``list_creatives_raw``'s
    params, and a future ``model_dump()`` override on QuerySummary/ListCreativesResponse would
    be honored on REST/A2A yet bypassed by MCP's ``structured_content`` path. A per-transport
    regression in any of those would slip an asserted-on-REST-only test.
    """

    @pytest.mark.parametrize("transport", ALL_WIRE)
    @pytest.mark.parametrize(
        ("filter_statuses", "seed_statuses", "expected_report"),
        [
            # Multi-value match-any: both requested returned, a third status excluded.
            (["approved", "rejected"], ("approved", "rejected", "pending_review"), "statuses=approved,rejected"),
            # Single-value: report and result already agree; guards report-is-truthful.
            (["approved"], ("approved", "rejected"), "statuses=approved"),
        ],
    )
    def test_statuses_filter_scopes_and_reports(
        self, integration_db, transport, filter_statuses, seed_statuses, expected_report
    ):
        with CreativeListEnv() as env:
            tenant, principal = env.setup_default_data()
            # (status, creative_id) per seeded row; the kept set is exactly those whose
            # status is in the requested filter (match-any) — derived, never hardcoded.
            seeded = [(status, seed_creative_in_status(tenant, principal, status)) for status in seed_statuses]
            expected_kept = {cid for status, cid in seeded if status in filter_statuses}

            result = env.call_via(transport, filters={"statuses": filter_statuses})

            assert not result.is_error, f"{transport}: {result.error!r}"
            # Scoping half: the scoped set is exactly the creatives whose status matches.
            assert _returned_creative_ids(result) == expected_kept, (
                f"{transport}: scoped set must equal the creatives whose status is in {filter_statuses}"
            )
            # Reporting half: filters_applied echoes the FULL applied list on the wire, so a
            # regression that emits only the first status (or over-claims) reddens here.
            filters_applied = result.require_wire()["query_summary"]["filters_applied"]
            assert expected_report in filters_applied, (
                f"{transport}: filters_applied must report the full applied statuses list "
                f"({expected_report!r}), got {filters_applied}"
            )


class TestStatusesFilterValidation:
    """A malformed statuses filter (empty array) is rejected with a spec envelope on every
    wire transport — the structurally identical sibling of the concept_ids ``minItems:1``
    case (``test_list_creatives_concept_filter.py``). Gives the repository's "``[]`` never
    legitimately reaches here" comment a wire oracle: ``[]`` is a ``minItems:1``
    VALIDATION_ERROR upstream, never a "match nothing" narrowing."""

    @pytest.mark.parametrize("transport", ALL_WIRE)
    def test_empty_statuses_array_emits_validation_envelope(self, integration_db, transport):
        """filters={'statuses': []} violates minItems:1 → two-layer VALIDATION_ERROR on every transport."""
        with CreativeListEnv() as env:
            env.setup_default_data()
            assert_empty_array_filter_rejected(env, transport, "statuses")
