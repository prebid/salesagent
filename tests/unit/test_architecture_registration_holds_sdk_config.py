"""Guard: the webhook registration value HOLDS the adcp SDK config, never a copy.

Critical Pattern #1 says local types extend the adcp library type rather than
duplicating it. That is critical pattern #1 rather than a guard, but
only inside ``src/core/schemas/`` — and ``ValidatedWebhookRegistration`` was
introduced in ``src/core/webhooks/``, outside that scan, as a frozen dataclass
carrying ``url`` plus two FLATTENED auth primitives.

What that cost, concretely: the pinned
``dist/schemas/3.1.1/core/push-notification-config.json`` defines four fields
(``url``, ``operation_id``, ``token``, ``authentication``) and the hand-rolled
type modelled two of them. So ``operation_id`` and ``token`` were silently
dropped at ingest — both carry echo obligations the seller MUST honour, and
``operation_id``'s is graded by the conformance universal
``dist/compliance/3.1.1/universal/webhook-emission.yaml`` requirement 1, which
also forbids recovering it by parsing the receiver URL. Once dropped at
registration it is unrecoverable.

Why this guard is NARROW rather than a general "no hand-rolled SDK parallels"
scan: a general field-overlap detector was written first and REJECTED, because
measured against this very case it scored 25% — the flattening renamed the
fields, so the duplication was invisible to field comparison — while flagging 41
mostly-coincidental hits elsewhere. A check that misses the case it exists for is
worse than none. This one asserts the specific invariant that was violated, with
no heuristic and no false positives.

The general duplication debt those 41 hits partly represent is real and
uncovered, but it is pre-existing and belongs to its own change.
"""

from __future__ import annotations

import dataclasses

import pytest
from adcp.types import PushNotificationConfig

from src.core.webhooks.registration import ValidatedWebhookRegistration

# Every field the pinned schema defines for this object. If the SDK gains one,
# holding the model means we gain it for free; re-declaring means we lose it.
_SPEC_FIELDS = frozenset({"url", "operation_id", "token", "authentication"})


@pytest.mark.arch_guard
def test_registration_holds_the_library_config() -> None:
    """The value must hold the SDK model as a field, not re-declare its contents."""
    fields = {f.name: f for f in dataclasses.fields(ValidatedWebhookRegistration)}

    assert "config" in fields, (
        "ValidatedWebhookRegistration must HOLD the library PushNotificationConfig "
        "(a `config` field), not re-declare a subset of it. A hand-rolled subset "
        "cannot track the pinned schema, and every field it omits is unrecoverable "
        "for anything registered through it."
    )
    assert fields["config"].type in ("PushNotificationConfig", PushNotificationConfig), (
        f"the held config must be the adcp library type, got {fields['config'].type!r}"
    )


@pytest.mark.arch_guard
def test_no_spec_field_is_flattened_onto_the_value() -> None:
    """No dataclass FIELD may shadow a spec field — that is how the copy crept in.

    Properties deriving ``url``/``operation_id``/``token`` from the held config are
    correct and expected; a stored FIELD of the same name means the value is
    carrying its own copy again, which is exactly the shape that dropped two
    fields the first time.
    """
    stored = {f.name for f in dataclasses.fields(ValidatedWebhookRegistration)}
    shadowed = stored & _SPEC_FIELDS

    assert not shadowed, (
        f"{sorted(shadowed)} are stored as dataclass fields, duplicating the held "
        f"PushNotificationConfig. Derive them from `config` instead, so the library "
        f"model stays the single source of the registration document."
    )


@pytest.mark.arch_guard
def test_every_spec_field_survives_a_round_trip() -> None:
    """The whole point: nothing the buyer registered is lost by being received.

    Grades the obligation rather than the shape — a future refactor that keeps a
    `config` field but drops data on the way through still fails here.
    """
    document = {
        "url": "https://buyer.example.com/hook",
        "operation_id": "op-abc-123",
        "token": "buyer-echo-token-1234567890",
        "authentication": {"schemes": ["HMAC-SHA256"], "credentials": "s" * 32},
    }

    from src.core.webhooks.registration import accept_push_notification_config

    registration = accept_push_notification_config(document)
    stashed = registration.to_stash()

    for field in sorted(_SPEC_FIELDS):
        assert field in stashed, (
            f"{field!r} did not survive registration -> stash. The seller MUST echo "
            f"operation_id and token back to the buyer (webhook-emission.yaml req. 1 "
            f"and the schema's token description), so a field dropped here cannot be "
            f"recovered later — the URL is opaque to us by spec."
        )
    assert stashed["operation_id"] == "op-abc-123"
    assert stashed["token"] == "buyer-echo-token-1234567890"
