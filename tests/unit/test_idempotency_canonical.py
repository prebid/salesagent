"""Unit tests for the idempotency canonical-payload hasher.

Verifies the RFC 8785 + closed-exclusion-set contract: key-order invariance,
excluded fields not affecting the hash, and real payload differences changing it.
"""

from __future__ import annotations

import hashlib
import struct
from datetime import UTC, datetime

import pytest

from src.core.idempotency_canonical import canonical_payload_hash, canonical_request_hash, strip_excluded_fields


def test_hash_is_sha256_hex() -> None:
    h = canonical_payload_hash({"brand": {"domain": "acme.com"}})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_stable_across_key_order() -> None:
    """Field insertion order must not change the canonical hash (RFC 8785 sorts keys)."""
    a = canonical_payload_hash({"brand": "acme", "po_number": "PO-1", "start_time": "asap"})
    b = canonical_payload_hash({"start_time": "asap", "brand": "acme", "po_number": "PO-1"})
    assert a == b


def test_excluded_top_level_fields_do_not_affect_hash() -> None:
    """idempotency_key / context / governance_context are stripped before hashing."""
    base = {"brand": "acme", "po_number": "PO-1"}
    with_excluded = {
        **base,
        "idempotency_key": "key-123",
        "context": {"conversation_id": "c1"},
        "governance_context": {"policy": "x"},
    }
    assert canonical_payload_hash(base) == canonical_payload_hash(with_excluded)


def test_nested_webhook_credential_excluded() -> None:
    """push_notification_config.authentication.credentials must not change the hash."""
    a = {
        "brand": "acme",
        "push_notification_config": {"url": "https://hook", "authentication": {"credentials": "secret-A"}},
    }
    b = {
        "brand": "acme",
        "push_notification_config": {"url": "https://hook", "authentication": {"credentials": "secret-B"}},
    }
    assert canonical_payload_hash(a) == canonical_payload_hash(b)


def test_different_payload_changes_hash() -> None:
    a = canonical_payload_hash({"brand": "acme", "po_number": "PO-1"})
    b = canonical_payload_hash({"brand": "acme", "po_number": "PO-2"})
    assert a != b


def test_ext_participates_in_hash() -> None:
    """The spec exclusion list is closed: ``ext`` participates, so it changes the hash."""
    a = canonical_payload_hash({"brand": "acme", "ext": {"k": 1}})
    b = canonical_payload_hash({"brand": "acme", "ext": {"k": 2}})
    assert a != b


def test_input_not_mutated() -> None:
    payload = {
        "brand": "acme",
        "idempotency_key": "key-123",
        "push_notification_config": {"authentication": {"credentials": "secret"}},
    }
    snapshot = {
        "brand": "acme",
        "idempotency_key": "key-123",
        "push_notification_config": {"authentication": {"credentials": "secret"}},
    }
    canonical_payload_hash(payload)
    assert payload == snapshot


def test_strip_excluded_fields_removes_closed_set() -> None:
    stripped = strip_excluded_fields(
        {
            "brand": "acme",
            "idempotency_key": "k",
            "context": {"x": 1},
            "governance_context": {"y": 2},
            "push_notification_config": {"authentication": {"credentials": "s", "scheme": "bearer"}},
        }
    )
    assert "idempotency_key" not in stripped
    assert "context" not in stripped
    assert "governance_context" not in stripped
    # nested credential removed, sibling 'scheme' preserved
    assert "credentials" not in stripped["push_notification_config"]["authentication"]
    assert stripped["push_notification_config"]["authentication"]["scheme"] == "bearer"
    assert stripped["brand"] == "acme"


def test_exclusion_set_is_the_spec_closed_list() -> None:
    """The closed exclusion list pinned as LITERALS from the spec.

    Engine-independent: the hasher now delegates to the SDK canonicalizer, so
    comparing our set to the SDK's would be a tautology. This pins the spec's
    documented closed list directly — if an SDK bump widens or narrows the
    exclusions, this fails and the change is reviewed instead of inherited.
    (The nested credential exclusion is pinned behaviorally by
    ``test_nested_webhook_credential_excluded`` above.)
    """
    from src.core.idempotency_canonical import _EXCLUDED_FIELDS

    assert _EXCLUDED_FIELDS == frozenset({"idempotency_key", "context", "governance_context"})


def test_pathological_nesting_rejects_as_validation_error() -> None:
    """A payload too deep to canonicalize rejects as a typed buyer error, never
    an unhandled RecursionError at the boundary."""
    from src.core.exceptions import AdCPValidationError

    deep: dict = {"leaf": True}
    for _ in range(100_000):
        deep = {"a": deep}

    with pytest.raises(AdCPValidationError):
        canonical_payload_hash(deep)


class TestRfc8785AppendixVectors:
    """RFC 8785 conformance vectors with literal expected canonicalizations.

    ``TestSdkEquivalencePin`` catches local-vs-SDK drift but both wrap the same
    ``rfc8785`` library — a shared conformance bug would pass it. These vectors
    pin the canonical BYTES against data published in the RFC itself, hashed
    through our production entrypoint (``canonical_payload_hash``; no key in
    the payloads is in the exclusion set, so stripping is a no-op).
    """

    # RFC 8785 §3.2.2 sample input / expected output.
    SAMPLE_INPUT = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": '€$\u000f\nA\'B"\\\\"/',
        "literals": [None, True, False],
    }
    SAMPLE_EXPECTED = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )

    def test_rfc_sample_canonicalization(self) -> None:
        expected_hash = hashlib.sha256(self.SAMPLE_EXPECTED.encode()).hexdigest()
        assert canonical_payload_hash(self.SAMPLE_INPUT) == expected_hash

    # RFC 8785 Appendix B number-serialization samples: IEEE-754 bit pattern →
    # expected ES6/JCS serialization.
    APPENDIX_B_NUMBERS = [
        ("zero", "0000000000000000", "0"),
        ("minus-zero", "8000000000000000", "0"),
        ("min-subnormal", "0000000000000001", "5e-324"),
        ("max-safe-integer-plus-one", "4340000000000000", "9007199254740992"),
        ("negative-2-pow-53", "c340000000000000", "-9007199254740992"),
        ("exponent-notation-boundary", "444b1ae4d6e2ef50", "1e+21"),
        ("decimal-notation-boundary", "3eb0c6f7a0b5ed8d", "0.000001"),
        ("below-decimal-boundary", "3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("max-double", "7fefffffffffffff", "1.7976931348623157e+308"),
    ]

    @pytest.mark.parametrize(("name", "bits", "expected"), APPENDIX_B_NUMBERS, ids=[v[0] for v in APPENDIX_B_NUMBERS])
    def test_appendix_b_number_serialization(self, name: str, bits: str, expected: str) -> None:
        value = struct.unpack(">d", bytes.fromhex(bits))[0]
        expected_hash = hashlib.sha256(f'{{"n":{expected}}}'.encode()).hexdigest()
        assert canonical_payload_hash({"n": value}) == expected_hash, name


class TestCanonicalRequestHash:
    """The Pydantic-model wrapper: field-order stability and exclusion invariance."""

    @staticmethod
    def _request(**overrides):
        from src.core.schemas import CreateMediaBuyRequest

        kwargs: dict = {
            "brand": {"domain": "canonical-test.example.com"},
            "packages": [],
            "start_time": datetime(2026, 6, 1, tzinfo=UTC),
            "end_time": datetime(2026, 6, 30, tzinfo=UTC),
            "po_number": "CANON-1",
            "idempotency_key": "request-hash-key-0001",
        }
        kwargs.update(overrides)
        return CreateMediaBuyRequest(**kwargs)

    def test_canonical_request_hash_pydantic_field_order_stable(self) -> None:
        """Construction order and dump key order must not change the hash."""
        from src.core.schemas import CreateMediaBuyRequest

        a = self._request()
        b = CreateMediaBuyRequest(
            idempotency_key="request-hash-key-0001",
            po_number="CANON-1",
            end_time=datetime(2026, 6, 30, tzinfo=UTC),
            start_time=datetime(2026, 6, 1, tzinfo=UTC),
            packages=[],
            brand={"domain": "canonical-test.example.com"},
        )
        assert canonical_request_hash(a) == canonical_request_hash(b)
        # ... and equals the canonical hash of a key-reversed dump of the model.
        reordered = dict(reversed(list(a.model_dump(mode="json").items())))
        assert canonical_request_hash(a) == canonical_payload_hash(reordered)

    def test_excluded_fields_do_not_affect_request_hash(self) -> None:
        """Requests differing only in excluded fields (the key itself) hash equal."""
        a = self._request(idempotency_key="excluded-invariance-0001")
        b = self._request(idempotency_key="excluded-invariance-0002")
        assert canonical_request_hash(a) == canonical_request_hash(b)

    def test_real_field_difference_changes_request_hash(self) -> None:
        a = self._request(po_number="CANON-1")
        b = self._request(po_number="CANON-2")
        assert canonical_request_hash(a) != canonical_request_hash(b)
