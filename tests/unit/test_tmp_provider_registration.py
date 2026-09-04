"""Unit tests for the transport-agnostic TMP provider registration record.

``src/core/schemas/tmp_provider.py`` owns the AdCP provider-registration
invariants that used to live in the Flask admin blueprint.  These tests grade
the invariants where they now live, so any future write surface (MCP/A2A/REST
tool, bulk import) inherits graded rules rather than re-deriving them.

Covers:
- ``TMPProviderFields`` and ``TMPProviderRegistration`` cannot drift apart
- ``VALID_UID_TYPES`` / ``VALID_STATUSES`` track the pinned SDK enums *behaviourally*
  (every SDK value accepted, unknown values rejected) — not by re-asserting a literal
- Each invariant rejects with the operator-facing message the admin UI flashes
- Every *value* constraint the pinned schema puts on a persisted field is
  enforced here, graded against the schema file itself rather than a restatement
  of its numbers — a value this record accepts is a value the discovery wire must
  be able to emit conformantly (#1197 review)
- ``to_fields`` / ``to_update_fields`` produce the repository's write record
- The shared rules agree with the SDK's own ``TmpProviderRegistration`` model
"""

from __future__ import annotations

import logging

import pytest
from adcp.types.generated_poc.enums.uid_type import UidType
from adcp.types.generated_poc.trusted_match.provider_registration import Status as ProviderStatus
from pydantic import ValidationError

from src.core.schemas.tmp_provider import (
    VALID_STATUSES,
    VALID_UID_TYPES,
    TMPProviderFields,
    TMPProviderRegistration,
    TMPProviderValidationError,
)
from src.routes.tmp_providers import PROVIDER_REGISTRATION_SCHEMA
from tests.helpers.pinned_schema import load as load_pinned_schema

_RID = "0192f3c4-5d6e-7f80-9a1b-2c3d4e5f6071"


def _rejection_message(**overrides) -> str:
    """Return the operator-facing message for a registration that must be rejected.

    Fails the test (rather than returning None) when the registration is
    *accepted* — an invariant that silently stopped firing must not read as a
    passing assertion on a message that was never produced.
    """
    with pytest.raises(TMPProviderValidationError) as excinfo:
        TMPProviderRegistration.parse(_fields(**overrides))
    return str(excinfo.value)


# Every test builds on a valid registration and mutates one thing, so a failure
# names the invariant under test rather than a setup mistake.
_VALID: TMPProviderFields = {
    "name": "Test Provider",
    "endpoint": "https://provider.example.com/tmp",
    "context_match": True,
    "identity_match": False,
    "countries": None,
    "uid_types": None,
    "properties": None,
    "timeout_ms": 50,
    "priority": 0,
    "status": "active",
    "auth_type": None,
    "auth_credentials": None,
}


def _fields(**overrides) -> TMPProviderFields:
    return {**_VALID, **overrides}  # type: ignore[typeddict-item]


class TestFieldContract:
    """TMPProviderFields is the static mirror of the model's field set."""

    def test_typeddict_keys_match_model_fields(self):
        """Mutation this pins: adding a field to one and not the other.

        ``TMPProviderFields`` is what the repository write methods are typed
        against (``**Unpack[TMPProviderFields]``); if it drifts from the model,
        ``to_fields()`` starts emitting a key the repository's type rejects.
        """
        assert set(TMPProviderFields.__annotations__) == set(TMPProviderRegistration.model_fields)

    def test_to_fields_round_trips_through_the_model(self):
        assert TMPProviderRegistration.parse(_fields()).to_fields() == _VALID

    def test_to_update_fields_omits_credentials_when_not_included(self):
        """Leaving the credential field blank must not overwrite the stored value."""
        registration = TMPProviderRegistration.parse(_fields(auth_credentials="secret-token"))

        preserved = registration.to_update_fields(include_credentials=False)
        rotated = registration.to_update_fields(include_credentials=True)

        assert "auth_credentials" not in preserved
        assert rotated["auth_credentials"] == "secret-token"
        # Only that one key differs — nothing else is dropped by the omission.
        assert set(rotated) - set(preserved) == {"auth_credentials"}


class TestEnumsTrackThePinnedSdk:
    """The uid-type and status vocabularies come from the SDK, not a literal.

    Asserted behaviourally: a hand-written frozenset would pass a set-equality
    check against itself, so these drive the validator with each SDK value.
    """

    @pytest.mark.parametrize("uid_type", [t.value for t in UidType])
    def test_every_sdk_uid_type_is_accepted(self, uid_type: str):
        registration = TMPProviderRegistration.parse(
            _fields(identity_match=True, countries=["US"], uid_types=[uid_type])
        )

        assert registration.uid_types == [uid_type]

    def test_unknown_uid_type_is_rejected(self):
        message = _rejection_message(identity_match=True, countries=["US"], uid_types=["uid2", "not_a_uid_type"])

        # The vocabulary is a FIELD TYPE now (enforced unconditionally, per the
        # schema), so the message is pydantic's enum message prefixed with the
        # field name — which is what tells the operator WHICH of three CSV inputs
        # was wrong (#1197 review).
        # The vocabulary is a FIELD TYPE now (enforced unconditionally, per the
        # schema), so the message is pydantic's — prefixed with the field AND the
        # failing index, and with the rejected value appended (#1197 review).
        assert message.startswith("uid_types[1]: ")
        assert "uid2" in message, "the message must enumerate the accepted vocabulary"
        assert message.endswith("(got 'not_a_uid_type')")

    @pytest.mark.parametrize("status", [s.value for s in ProviderStatus])
    def test_every_sdk_status_is_accepted(self, status: str):
        assert TMPProviderRegistration.parse(_fields(status=status)).status == status

    def test_unknown_status_is_rejected(self):
        assert (
            _rejection_message(status="paused") == "Invalid status 'paused'. Valid values: active, draining, inactive"
        )

    def test_vocabularies_are_derived_not_literal(self):
        """The module constants equal the SDK enums exactly (no local additions)."""
        assert VALID_UID_TYPES == frozenset(t.value for t in UidType)
        assert VALID_STATUSES == frozenset(s.value for s in ProviderStatus)


class TestRegistrationInvariants:
    """Each rule rejects with the operator-facing message the admin UI flashes."""

    def test_requires_a_name(self):
        assert _rejection_message(name="   ") == "Provider name is required"

    def test_requires_an_endpoint(self):
        assert _rejection_message(endpoint="") == "Endpoint URL is required"

    def test_requires_at_least_one_match_mode(self):
        assert (
            _rejection_message(context_match=False, identity_match=False)
            == "Provider must support at least one of context_match or identity_match"
        )

    def test_identity_match_requires_countries(self):
        message = _rejection_message(context_match=False, identity_match=True, countries=None, uid_types=["uid2"])
        assert message == "Countries are required when identity_match is enabled (ISO 3166-1 alpha-2 codes)"

    def test_identity_match_requires_uid_types(self):
        message = _rejection_message(context_match=False, identity_match=True, countries=["US"], uid_types=None)
        assert message == "UID types are required when identity_match is enabled (e.g. uid2, publisher_first_party)"

    def test_context_match_only_provider_needs_no_identity_dimensions(self):
        """The identity rules must not fire for a context-only provider."""
        registration = TMPProviderRegistration.parse(
            _fields(context_match=True, identity_match=False, countries=None, uid_types=None)
        )
        assert (registration.context_match, registration.identity_match) == (True, False)

    def test_ssrf_unsafe_endpoint_is_rejected(self, caplog):
        """The egress verdict runs inside the record, not only behind the admin form.

        Mutation this pins: dropping ``EgressPolicy.check_registration`` from the
        model would let an internal-network endpoint register from *any* write
        surface.

        Deliberately **https**, so the address rule is the only thing that can
        reject it — an http URL would be refused by the scheme rule first (see
        TestEndpointSchemeIsConditional) and this test would pass without the
        address check running at all.

        The message no longer echoes the host. #1802 made refusal reasons opaque
        to whoever supplied the URL (AdCP 3.1.1 security point 6: a reason naming
        the resolved address is a host scanner), and routes the real cause to the
        log instead — so that is where this asserts it, which is a stronger claim
        than the old substring match on a user-facing string.
        """
        with caplog.at_level(logging.WARNING, logger="src.core.schemas.tmp_provider"):
            message = _rejection_message(endpoint="https://host.docker.internal:9999/tmp")

        assert message.startswith("Endpoint URL is not allowed:")
        assert "host.docker.internal" not in message, "a refusal must not echo the address back"
        logged = [record.getMessage() for record in caplog.records]
        assert any("[TMP registration][SECURITY]" in line and "host.docker.internal" in line for line in logged), (
            f"the operator-facing log must still name the endpoint it refused; got {logged}"
        )

    def test_direct_construction_raises_validation_error(self):
        """Programmatic write surfaces get an exception, not a message tuple."""
        with pytest.raises(ValidationError, match="at least one of context_match or identity_match"):
            TMPProviderRegistration(**_fields(context_match=False, identity_match=False))


class TestAgreesWithTheSdkModel:
    """The rules shared with the SDK's own registration model agree with it.

    ``TMPProviderRegistration`` deliberately does not subclass the SDK's closed
    ``RootModel`` union (see the module docstring), so these cases pin that the
    two do not diverge on the rules they *do* share.
    """

    @staticmethod
    def _sdk_accepts(**overrides) -> bool:
        from adcp.types.generated_poc.trusted_match.provider_registration import TmpProviderRegistration

        payload = {
            "provider_id": "test_provider",
            "endpoint": "https://provider.example.com/tmp",
            "context_match": True,
            **overrides,
        }
        try:
            TmpProviderRegistration.model_validate(payload)
        except ValidationError:
            return False
        return True

    def test_identity_match_without_countries_rejected_by_both(self):
        assert not self._sdk_accepts(context_match=False, identity_match=True, uid_types=["uid2"])

        assert _rejection_message(context_match=False, identity_match=True, countries=None, uid_types=["uid2"])

    def test_identity_match_without_uid_types_rejected_by_both(self):
        assert not self._sdk_accepts(context_match=False, identity_match=True, countries=["US"])

        assert _rejection_message(context_match=False, identity_match=True, countries=["US"], uid_types=None)

    def test_no_match_mode_rejected_by_both(self):
        assert not self._sdk_accepts(context_match=False)

        assert _rejection_message(context_match=False, identity_match=False)

    def test_fully_specified_identity_provider_accepted_by_both(self):
        assert self._sdk_accepts(identity_match=True, countries=["US"], uid_types=["uid2"])

        registration = TMPProviderRegistration.parse(_fields(identity_match=True, countries=["US"], uid_types=["uid2"]))
        assert registration.uid_types == ["uid2"]


class TestValueConstraintsComeFromTheSchema:
    """Numeric and charset constraints are the pinned schema's, not this file's.

    Each test reads the constraint out of
    :data:`src.routes.tmp_providers.PROVIDER_REGISTRATION_SCHEMA` and drives the
    record with a value one step outside it, so a spec bump that widens or
    narrows a bound is graded automatically instead of being re-typed here.
    These are the constraints that were absent until #1197 round 18: a
    ``timeout_ms`` of 30000 and a ``priority`` of -1 were accepted and written,
    and lowercase ``countries`` reached the machine wire verbatim.
    """

    @staticmethod
    def _prop(name: str) -> dict:
        return load_pinned_schema(PROVIDER_REGISTRATION_SCHEMA)["properties"][name]

    def test_timeout_ms_bounds_match_the_schema(self):
        schema = self._prop("timeout_ms")
        low, high = schema["minimum"], schema["maximum"]

        assert TMPProviderRegistration.parse(_fields(timeout_ms=low)).timeout_ms == low
        assert TMPProviderRegistration.parse(_fields(timeout_ms=high)).timeout_ms == high
        assert _rejection_message(timeout_ms=low - 1)
        assert _rejection_message(timeout_ms=high + 1)

    def test_priority_minimum_matches_the_schema(self):
        minimum = self._prop("priority")["minimum"]

        assert TMPProviderRegistration.parse(_fields(priority=minimum)).priority == minimum
        assert _rejection_message(priority=minimum - 1)

    def test_countries_must_match_the_schema_item_pattern(self):
        """Uppercase ISO 3166-1 alpha-2 — the record's rule, not the form's.

        ``upper=True`` on the blueprint's CSV splitter used to be the only thing
        moving a country code toward this pattern, so the second write surface
        inherited nothing.  The pattern lives here now and the splitter is back
        to pure form shape.
        """
        assert self._prop("countries")["items"]["pattern"] == r"^[A-Z]{2}$"

        accepted = TMPProviderRegistration.parse(_fields(identity_match=True, countries=["US"], uid_types=["uid2"]))
        assert accepted.countries == ["US"]

        for bad in (["usa"], ["us"], ["U"], ["USA"], ["U1"]):
            assert _rejection_message(identity_match=True, countries=bad, uid_types=["uid2"]), bad

    def test_properties_items_must_be_uuids(self):
        assert self._prop("properties")["items"]["format"] == "uuid"

        assert TMPProviderRegistration.parse(_fields(properties=[_RID])).properties == [_RID]
        assert _rejection_message(properties=["rid-2"])

    def test_accepted_values_are_emitted_conformantly(self):
        """The boundary values this record accepts are values the wire can carry.

        The point of the constraints: the record is the gate in front of every
        write surface, so "accepted here" must imply "schema-valid there".  Built
        through ``to_fields()`` → the discovery serializer's key set so the two
        halves are graded as one path.
        """
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        schema = load_pinned_schema(PROVIDER_REGISTRATION_SCHEMA)["properties"]
        registration = TMPProviderRegistration.parse(
            _fields(
                identity_match=True,
                countries=["US", "DE"],
                uid_types=["uid2"],
                properties=[_RID],
                timeout_ms=schema["timeout_ms"]["maximum"],
                priority=schema["priority"]["minimum"],
                status="draining",
            )
        )
        fields = registration.to_fields()
        entry = {
            "provider_id": "prov_boundary",
            **{k: v for k, v in fields.items() if k not in ("name", "auth_type", "auth_credentials")},
        }
        validate_against_pinned_schema(PROVIDER_REGISTRATION_SCHEMA, entry)


class TestRejectionMessagesNameTheOffendingInput:
    """The operator-facing string for each field-level constraint, pinned exactly.

    ``_first_error_message`` used to return pydantic's bare message, which for the
    four field-level constraints says what was expected and not what was given —
    so an operator who mistyped one of three comma-separated countries got
    ``String should match pattern '^[A-Z]{2}$'`` and no way to tell which. These
    assertions are what keep the field, the index and the rejected value in the
    message (#1197 review).
    """

    def test_country_names_the_failing_index_and_value(self):
        assert (
            _rejection_message(identity_match=True, countries=["US", "usa"], uid_types=["uid2"])
            == "countries[1]: String should match pattern '^[A-Z]{2}$' (got 'usa')"
        )

    def test_uid_type_names_the_failing_index_and_value(self):
        message = _rejection_message(uid_types=["uid2", "nope"])
        assert message.startswith("uid_types[1]: Input should be ")
        assert message.endswith("(got 'nope')")

    def test_property_rid_names_the_failing_index_and_value(self):
        message = _rejection_message(properties=["not-a-uuid"])
        assert message.startswith("properties[0]: ")
        assert message.endswith("(got 'not-a-uuid')")

    def test_numeric_bound_names_the_field_and_value(self):
        assert _rejection_message(timeout_ms=1) == "timeout_ms: Input should be greater than or equal to 5 (got 1)"
        assert _rejection_message(priority=-1) == "priority: Input should be greater than or equal to 0 (got -1)"

    def test_auth_scheme_names_the_field_and_the_accepted_set(self):
        assert _rejection_message(auth_type="api_key") == "auth_type: Invalid auth_type 'api_key'. Valid values: bearer"

    def test_model_level_invariant_keeps_its_own_sentence(self):
        """A hand-written invariant names its own subject, so it is NOT field-prefixed."""
        assert (
            _rejection_message(context_match=False, identity_match=False)
            == "Provider must support at least one of context_match or identity_match"
        )


class TestEndpointSchemeIsUnconditionallyHttps:
    """https is required for EVERY provider endpoint, local dev included.

    This class used to pin the opposite for local hosts. #1802 made the repo's
    TLS gate unconditional and deleted the insecure hatch, so a per-feature
    relaxation here would have been the only place in the tree that could
    downgrade a scheme — and the generated test CA covers ``*.localhost``
    (``SAN_DNS_NAMES``), so local development speaks https and the relaxation was
    buying nothing. The pinned spec made https a MUST for this surface all along;
    the record now simply agrees with it, and with the SDK codegen it used to
    override.
    """

    def test_public_http_endpoint_is_rejected(self, caplog):
        """The pinned spec's MUST: a public provider endpoint has to be https.

        Asserted on the LOG, not the message: the refusal handed back is opaque
        by design since #1802, so "which rule refused this" is a fact the log
        carries and the buyer-facing string deliberately does not.
        """
        with caplog.at_level(logging.WARNING, logger="src.core.security.egress.policy"):
            message = _rejection_message(endpoint="http://provider.example.com/tmp")

        assert message.startswith("Endpoint URL is not allowed:")
        logged = [record.getMessage() for record in caplog.records]
        assert any("scheme is not https" in line for line in logged), (
            f"the scheme rule must be the one that refused a cleartext public endpoint; got {logged}"
        )

    def test_public_https_endpoint_is_accepted(self):
        registration = TMPProviderRegistration.parse(_fields(endpoint="https://provider.example.com/tmp"))

        assert registration.endpoint == "https://provider.example.com/tmp"

    def test_local_https_endpoint_is_accepted(self):
        """The documented dev form — and it must be registrable, not just documented.

        https, because that is what the route documents now: the generated CA
        covers ``*.localhost``, so a dev provider serves TLS like any other.
        """
        registration = TMPProviderRegistration.parse(_fields(endpoint="https://si-agent.localhost:3003"))

        assert registration.endpoint == "https://si-agent.localhost:3003"

    def test_local_http_endpoint_is_rejected(self):
        """The relaxation is gone: a local host gets no scheme exemption either."""
        assert _rejection_message(endpoint="http://si-agent.localhost:3003")

    def test_loopback_literal_is_still_rejected(self):
        """Relaxing the scheme for local hosts must not open the loopback IP.

        ``is_local_host`` is true for ``127.0.0.1``, so this pins that the SSRF
        check's literal-IP guard still runs — the relaxation skips DNS resolution,
        not the blocked-range check.
        """
        assert _rejection_message(endpoint="http://127.0.0.1:3003")

    def test_the_entry_type_applies_the_same_rule(self):
        """The wire entry and the record agree: https, unconditionally."""
        import pytest as _pytest
        from pydantic import ValidationError as _ValidationError

        from src.core.schemas.tmp_provider import TMPProviderDiscoveryEntry

        payload = {
            "provider_id": "prov_scheme",
            "context_match": True,
            "endpoint": "https://si-agent.localhost:3003",
        }
        # https: permitted, local or not.
        assert TMPProviderDiscoveryEntry.model_validate(payload) is not None
        # Cleartext: refused by the SDK codegen's own rule, which this module no
        # longer overrides — for a local host as much as a public one.
        for cleartext in ("http://si-agent.localhost:3003", "http://provider.example.com/tmp"):
            with _pytest.raises(_ValidationError):
                TMPProviderDiscoveryEntry.model_validate({**payload, "endpoint": cleartext})
