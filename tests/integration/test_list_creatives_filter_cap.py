"""Integration tests for the list_creatives filter-length cap (#1505).

Defense-in-depth: most CreativeFilters list fields are unbounded on the pinned
adcp schema (only creative_ids has MaxLen). An over-long list filter must be
rejected with a clean VALIDATION_ERROR rather than expanding into a very large
SQL IN (...) query. Uses the CreativeListEnv harness, mirroring
test_list_creatives_auth.py.
"""

import typing

import pytest
from adcp import CreativeFilters

from src.core.exceptions import AdCPValidationError
from src.core.tools.creatives.listing import _CAPPED_FILTER_FIELDS, _MAX_FILTER_LIST_LEN
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeListEnv, make_identity
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

# Wire transports that surface the two-layer envelope for tool-raised AdCPErrors
# (mirrors test_list_creatives_concept_filter.py's _HELPER_WIRE rationale).
_HELPER_WIRE = [Transport.A2A, Transport.REST]

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT = "cap_test_tenant"
_PRINCIPAL = "advertiser_a"


def _seed():
    tenant = TenantFactory(tenant_id=_TENANT)
    PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL)


def _identity():
    return make_identity(
        principal_id=_PRINCIPAL,
        tenant_id=_TENANT,
        tenant={"tenant_id": _TENANT, "name": "Cap Test Tenant"},
    )


class TestListCreativesFilterCap:
    def test_over_long_filter_rejected(self, integration_db):
        """A list filter longer than the cap -> VALIDATION_ERROR (correctable).

        Oracle: if the cap in _list_creatives_impl is removed, the impl runs the
        query and returns a response instead of raising, so this test fails.
        """
        with CreativeListEnv() as env:
            _seed()
            over = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)])
            with pytest.raises(AdCPValidationError) as exc:
                env.call_impl(identity=_identity(), filters=over)

        assert exc.value.recovery == "correctable"
        assert "concept_ids" in str(exc.value)
        assert str(_MAX_FILTER_LIST_LEN) in str(exc.value)
        assert exc.value.suggestion  # a remediation suggestion is surfaced

    def test_filter_at_cap_is_allowed(self, integration_db):
        """Exactly at the cap is accepted (boundary / negative control)."""
        with CreativeListEnv() as env:
            _seed()
            at_cap = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN)])
            response = env.call_impl(identity=_identity(), filters=at_cap)

        # Concrete post-condition: the query RAN (did not raise) and returned
        # an empty, well-formed result for the unmatched concept ids.
        assert response.query_summary is not None
        assert response.query_summary.total_matching == 0

    @pytest.mark.parametrize("transport", _HELPER_WIRE)
    def test_over_cap_concept_ids_emits_validation_envelope(self, integration_db, transport):
        """Over-cap structured filter surfaces the spec VALIDATION_ERROR envelope
        on the wire (Error Verification Policy: grade the wire, not the
        reconstructed exception)."""
        with CreativeListEnv() as env:
            _seed()
            result = env.call_via(
                transport,
                filters={"concept_ids": [f"c-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)]},
            )

            envelope = result.wire_error_envelope
            assert envelope is not None, f"{transport}: no wire error envelope captured"
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="concept_ids",
            )

    def test_over_cap_flat_media_buy_ids_rejected_on_wire(self, integration_db):
        """FLAT list params are capped too — the cap runs on the MERGED filters.

        Oracle for the merge placement: with the cap checked only on the
        pre-merge ``filters`` argument (the original implementation), a flat
        ``media_buy_ids`` list of 101 entries reaches the query and this test
        fails with a 200-style success instead of the envelope.
        """
        with CreativeListEnv() as env:
            _seed()
            result = env.call_via(
                Transport.REST,
                media_buy_ids=[f"mb-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)],
            )

            envelope = result.wire_error_envelope
            assert envelope is not None, "no wire error envelope captured for flat media_buy_ids"
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="media_buy_ids",
            )


def test_capped_fields_stay_in_parity_with_sdk_list_fields():
    """_CAPPED_FILTER_FIELDS is hand-maintained — pin it against the SDK.

    If a future adcp pin adds a list-typed field to CreativeFilters, this
    fails and the new field must be added to the cap (or explicitly excluded
    here with a reason) — no list filter can slip through uncapped silently.
    """
    sdk_list_fields = set()
    for name, field in CreativeFilters.model_fields.items():
        annotation = field.annotation
        candidates = [annotation, *typing.get_args(annotation)]
        if any(typing.get_origin(c) is list for c in candidates):
            sdk_list_fields.add(name)

    assert sdk_list_fields == set(_CAPPED_FILTER_FIELDS), (
        "CreativeFilters list-typed fields diverged from _CAPPED_FILTER_FIELDS — "
        f"sdk-only: {sorted(sdk_list_fields - set(_CAPPED_FILTER_FIELDS))}, "
        f"cap-only: {sorted(set(_CAPPED_FILTER_FIELDS) - sdk_list_fields)}"
    )
