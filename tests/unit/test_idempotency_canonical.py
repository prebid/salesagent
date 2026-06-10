"""Unit tests for the idempotency canonical-payload hasher.

Verifies the RFC 8785 + closed-exclusion-set contract: key-order invariance,
excluded fields not affecting the hash, and real payload differences changing it.
"""

from __future__ import annotations

import pytest

from src.core.idempotency_canonical import canonical_payload_hash, strip_excluded_fields


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


class TestSdkEquivalencePin:
    """Our salesagent-native hasher stays byte-equivalent to the SDK reference.

    The spec (security.mdx#idempotency) is the authority and the SDK's
    ``adcp.server.idempotency`` is the cross-check — but a drift between the
    two would silently change which retries replay vs conflict, so the parity
    is pinned: the exclusion sets must match and a corpus spanning key
    ordering, excluded fields (top-level and nested credential), unicode,
    large ints, bools/nulls, and nested arrays must hash identically.
    """

    CORPUS = [
        ("reordered-keys", {"b": 1, "a": 2, "z": {"y": [3, 2, 1]}}),
        (
            "excluded-fields-present",
            {
                "brand": {"domain": "acme.example"},
                "idempotency_key": "pin-test-key-0001",
                "context": {"trace": "t-1"},
                "governance_context": {"g": 1},
            },
        ),
        (
            "nested-credential",
            {
                "packages": [{"product_id": "p1", "budget": 5000.0}],
                "push_notification_config": {
                    "url": "https://buyer.example/hook",
                    "authentication": {"credentials": "s" * 40, "schemes": ["Bearer"]},
                },
            },
        ),
        ("unicode-and-numbers", {"name": "café — ünïcode", "big": 2**53 - 1, "neg": -0.5}),
        ("bools-and-nulls", {"flag": True, "off": False, "nothing": None, "arr": [None, True]}),
    ]

    def test_excluded_fields_match_sdk(self) -> None:
        from adcp.server.idempotency import EXCLUDED_FIELDS as SDK_EXCLUDED

        from src.core.idempotency_canonical import _EXCLUDED_FIELDS

        assert _EXCLUDED_FIELDS == SDK_EXCLUDED

    @pytest.mark.parametrize(("name", "payload"), CORPUS, ids=[c[0] for c in CORPUS])
    def test_hash_matches_sdk(self, name: str, payload: dict) -> None:
        from adcp.server.idempotency import canonical_json_sha256

        assert canonical_payload_hash(payload) == canonical_json_sha256(payload), name

    def test_real_field_difference_diverges_on_both_sides(self) -> None:
        """Both implementations agree a NON-excluded change is a different payload."""
        from adcp.server.idempotency import canonical_json_sha256

        base = {"po_number": "PO-1", "packages": [{"product_id": "p1"}]}
        changed = {"po_number": "PO-2", "packages": [{"product_id": "p1"}]}
        assert canonical_payload_hash(base) != canonical_payload_hash(changed)
        assert canonical_json_sha256(base) != canonical_json_sha256(changed)
