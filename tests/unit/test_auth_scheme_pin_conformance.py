"""Oracle: ``adcp.types.AuthenticationScheme`` matches the pinned
``enums/auth-scheme.json`` enum exactly (AdCP 3.1.1).

``AuthenticationScheme`` is the one wire enum this repo ships UNPINNED. Every
other spelling that reaches a buyer is graded against the pinned schema tree,
but the webhook auth scheme is read straight off the SDK — at the egress seam's
``match`` (``src/core/security/webhook_egress.py:248``), at the admin
registration form, and at ingest. This PR's own argument is that the SDK can
contradict the pin; that argument applies here, and this is the cheapest moment
to close it because the two sets still agree.

What a failure means:

- a member ADDED to the SDK enum that the pin does not carry — the seam would
  accept a scheme no buyer may legally send, and the ``match`` in
  ``webhook_egress`` would need a branch for a spelling that is not on the wire;
- a member REMOVED from the SDK enum that the pin still carries — a buyer can
  send a legal scheme the seam cannot name, and it lands on the refusal branch.

Either way the fix is upstream reconciliation (bump the pin, or report the SDK
divergence), never editing this expectation to match whichever side moved. The
pinned value is read through ``tests.helpers.pinned_schema.auth_scheme_values``,
which reads the SDK's generated SCHEMA tree rather than its Python enum — the
two have separate generators, which is what makes this a grading and not a
tautology.
"""

from __future__ import annotations

import pytest
from adcp.types import AuthenticationScheme

from tests.helpers.pinned_schema import auth_scheme_values

pytestmark = pytest.mark.unit


def test_auth_scheme_enum_matches_the_pin() -> None:
    """The SDK's AuthenticationScheme is exactly the pinned auth-scheme enum."""
    sdk = {member.value for member in AuthenticationScheme}
    pinned = set(auth_scheme_values())
    assert sdk == pinned, (
        "adcp.types.AuthenticationScheme has drifted from the pinned "
        "enums/auth-scheme.json:\n"
        f"  in the SDK only: {sorted(sdk - pinned)}\n"
        f"  in the pin only: {sorted(pinned - sdk)}\n"
        "Reconcile upstream (see docs/adcp-spec-version.md) — do not edit this "
        "expectation to match whichever side moved."
    )


def test_auth_scheme_pin_is_not_empty() -> None:
    """The instrument itself: an unreadable enum must fail loudly, not vacuously.

    ``auth_scheme_values()`` reading an empty list would make the equality above
    pass only when the SDK enum were also empty — but a silently empty pin is
    the failure mode a set comparison hides worst, so it is asserted directly.
    """
    assert auth_scheme_values() == frozenset({"Bearer", "HMAC-SHA256"})
