"""Integration tests for the list_creatives filter-length cap (#1505).

Defense-in-depth: most CreativeFilters list fields are unbounded on the pinned
adcp schema (only creative_ids has MaxLen). An over-long list filter must be
rejected with a clean VALIDATION_ERROR rather than expanding into a very large
SQL IN (...) query. Uses the CreativeListEnv harness, mirroring
test_list_creatives_auth.py.
"""

import pytest
from adcp import CreativeFilters

from src.core.exceptions import AdCPValidationError
from src.core.tools.creatives.listing import _MAX_FILTER_LIST_LEN
from tests.harness import CreativeListEnv
from tests.harness.transport import Transport

# Wire transports only — IMPL has no wire envelope. The cap raises from
# _enforce_filter_list_caps inside _build_list_creatives_request, a
# transport-blind path, so the same VALIDATION_ERROR envelope surfaces on every
# wire transport (mirrors test_list_creatives_concept_filter.py's _ALL_WIRE,
# which grades MCP too).
_ALL_WIRE = [Transport.A2A, Transport.MCP, Transport.REST]

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestListCreativesFilterCap:
    def test_over_long_filter_rejected(self, integration_db):
        """A list filter longer than the cap -> VALIDATION_ERROR (correctable).

        Oracle: if ``_enforce_filter_list_caps`` (called from
        ``_build_list_creatives_request``) is removed, the request builds and
        ``_list_creatives_impl`` runs the query and returns a response instead of
        raising, so this test fails.
        """
        with CreativeListEnv() as env:
            env.setup_default_data()
            over = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)])
            with pytest.raises(AdCPValidationError) as exc:
                env.call_impl(filters=over)

        assert exc.value.recovery == "correctable"
        assert "concept_ids" in str(exc.value)
        assert str(_MAX_FILTER_LIST_LEN) in str(exc.value)
        assert exc.value.suggestion  # a remediation suggestion is surfaced

    def test_filter_at_cap_is_allowed(self, integration_db):
        """Exactly at the cap is accepted (boundary / negative control)."""
        with CreativeListEnv() as env:
            env.setup_default_data()
            at_cap = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN)])
            response = env.call_impl(filters=at_cap)

        # Concrete post-condition: the query RAN (did not raise) and returned
        # an empty, well-formed result for the unmatched concept ids.
        assert response.query_summary is not None
        assert response.query_summary.total_matching == 0

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    @pytest.mark.parametrize(
        ("call_kwargs", "expected_field"),
        [
            pytest.param(
                {"filters": {"concept_ids": [f"c-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)]}},
                "concept_ids",
                id="structured-concept_ids",
            ),
            pytest.param(
                {"media_buy_ids": [f"mb-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)]},
                "media_buy_ids",
                id="flat-media_buy_ids",
            ),
        ],
    )
    def test_over_cap_filter_emits_validation_envelope(self, integration_db, transport, call_kwargs, expected_field):
        """Over-cap list filter -> spec VALIDATION_ERROR envelope on every wire transport.

        Two entry paths, one behavior (the cap runs on the MERGED filters, so both
        reach it):

        * ``structured-concept_ids`` — an over-cap value inside the structured
          ``filters`` object.
        * ``flat-media_buy_ids`` — a flat top-level list param. Merge-placement
          oracle: with the cap checked only on the pre-merge ``filters`` argument
          (the original implementation), 101 flat ``media_buy_ids`` reach the
          ``IN (...)`` expansion and this case fails with a 200-style success
          instead of the envelope.

        The wire ``field`` is the bare param name the client actually sent
        (``field=field`` at listing.py:105), never a synthetic ``filters.<x>``
        path. Assertion is routed through the harness-guarded ``assert_wire_error``
        (recovery defaults to the pinned AdCP enum; ``field=`` pins both envelope
        layers) rather than hand-indexing the envelope. (Error Verification Policy:
        grade the wire, not the reconstructed exception.)
        """
        with CreativeListEnv() as env:
            env.setup_default_data()
            result = env.call_via(transport, **call_kwargs)
            result.assert_wire_error(
                "VALIDATION_ERROR",
                require_suggestion=True,
                message_substr=expected_field,
                field=expected_field,
            )


# The cap's schema-parity guard and the A2A projection-forwarding test used to live
# here. Both are DB-free, and a file under tests/integration/ never runs at the
# pre-commit gate (`make quality` and the tox `unit` env both scope to tests/unit/),
# so the "no array filter slips through uncapped" invariant was outside the gate it
# exists to hold. They now live in tests/unit/:
#   - tests/unit/test_architecture_list_creatives_filter_cap_parity.py
#   - tests/unit/test_a2a_parameter_mapping.py
#     (test_list_creatives_forwards_projection_and_enrichment_params)
# What stays here is what genuinely needs Postgres: the behavioral cap tests above.
