"""Guard: the signing-algorithm value-set has exactly ONE source of truth, ORDERED.

TDD RED for salesagent-z6nr.8 (A2 — signing key lifecycle). Nothing here passes
until ``src/core/signing/algorithms.py`` and the ``SigningKey`` ORM model exist.

Shaped after ``tests/unit/test_billing_party_parity.py``, which pins the same
disease: a DB CHECK hand-written as a literal copy of a spec enum drifts, and a
spec-valid value then IntegrityError's on INSERT (#1521).

Ordering is load-bearing HERE in a way it is not for billing.
``src.core.billing_policy.BILLING_PARTY_VALUES`` derives from an ORDERED enum,
but ``adcp.signing.crypto.ALLOWED_ALGS`` is a ``frozenset[str]`` — iterating it
yields hash order, and CPython randomizes str hashes per process. Deriving the
CHECK body straight off the frozenset emits DIFFERENT DDL text on different
runs: the ORM DDL and the migration DDL can disagree and alembic autogenerate
churns. ``SIGNING_ALG_VALUES`` must therefore be ``tuple(sorted(ALLOWED_ALGS))``
— a deterministic order that lands in the DDL — which is why guard (a) pins the
exact tuple and not merely its contents.

Refinement reference: salesagent-z6nr.8 design § "Refinement (atom
salesagent-ayfn.21)" R1 and R6.
"""

from src.core.database.models import SigningKey
from tests.helpers.orm_constraints import check_constraint_values

# The authoritative spec value-set for RFC 9421 signing algorithms, pinned by tag.
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/bundled/protocol/get-adcp-capabilities-response.json
#         pointer=/properties/webhook_signing/properties/algorithms/items/enum
_SPEC_SIGNING_ALGS = ("ed25519", "ecdsa-p256-sha256")

# The deterministic ORDER the constant must expose. Sorted, so the CHECK's DDL
# text is byte-stable across processes despite ALLOWED_ALGS being a frozenset.
_EXPECTED_ORDERED = ("ecdsa-p256-sha256", "ed25519")

# A2 mints request-signing keys ONLY — a deliberate narrowing BELOW the SDK's two
# accepted adcp_use values, because "webhook-signing" is deprecated pending
# removal (security.mdx:1073, adcp#5555) and webhooks are signed with a
# request-signing key (security.mdx:955). So ck_signing_keys_purpose is a
# hand-written literal ON PURPOSE: deriving it from the SDK's value-set would
# wrongly admit the deprecated value. What must stay pinned is the other
# direction — that the one value we DO mint is still accepted by the SDK keygen.
_MINTABLE_PURPOSES = ("request-signing",)


def test_signing_alg_values_is_the_exact_ordered_tuple():
    """(a) SIGNING_ALG_VALUES is deterministically ordered, not frozenset-iteration order.

    A set-equality assertion would pass on a nondeterministic derivation. The
    ordered pin is what makes ``tuple(sorted(ALLOWED_ALGS))`` the only
    implementation that can go green.
    """
    from src.core.signing.algorithms import SIGNING_ALG_VALUES

    assert SIGNING_ALG_VALUES == _EXPECTED_ORDERED, (
        f"SIGNING_ALG_VALUES is {SIGNING_ALG_VALUES!r}, expected {_EXPECTED_ORDERED!r}. "
        "It must be tuple(sorted(adcp.signing.crypto.ALLOWED_ALGS)) — an unsorted "
        "derivation from that frozenset emits different CHECK DDL on every process."
    )


def test_signing_alg_values_match_spec_pin_as_a_set():
    """(b) SIGNING_ALG_VALUES (SDK-derived) covers exactly the v3.1.1 spec enum.

    The installed SDK is a cross-check, not the authority. This pin is what makes
    deriving from ``ALLOWED_ALGS`` safe across SDK bumps: gaining or losing a value
    relative to the pinned spec fails here and forces a deliberate spec-version
    decision instead of silent drift.
    """
    from src.core.signing.algorithms import SIGNING_ALG_VALUES

    assert set(SIGNING_ALG_VALUES) == set(_SPEC_SIGNING_ALGS), (
        f"SIGNING_ALG_VALUES {SIGNING_ALG_VALUES!r} has drifted from the pinned AdCP "
        f"3.1.1 algorithms enum {_SPEC_SIGNING_ALGS!r}. Reconcile against the @source "
        "citation above before changing either side."
    )


def test_sdk_allowed_algs_match_spec_pin():
    """(c) The SDK frozenset itself still matches the pinned spec enum."""
    from adcp.signing.crypto import ALLOWED_ALGS

    assert set(ALLOWED_ALGS) == set(_SPEC_SIGNING_ALGS), (
        f"adcp.signing.crypto.ALLOWED_ALGS {sorted(ALLOWED_ALGS)!r} no longer matches "
        f"the pinned v3.1.1 algorithms enum {_SPEC_SIGNING_ALGS!r}."
    )


def test_orm_alg_check_derives_from_signing_alg_values():
    """(d) ck_signing_keys_alg contains exactly SIGNING_ALG_VALUES, in that order.

    Core invariant: the DB CHECK is a DERIVED copy of the single source of truth,
    never an independent hand-rolled literal.
    """
    from src.core.signing.algorithms import SIGNING_ALG_VALUES

    check_values = check_constraint_values(SigningKey, "ck_signing_keys_alg")
    assert check_values == SIGNING_ALG_VALUES, (
        f"ck_signing_keys_alg CHECK values {check_values!r} != SIGNING_ALG_VALUES "
        f"{SIGNING_ALG_VALUES!r}. The ORM constraint must derive from "
        "src.core.signing.algorithms.SIGNING_ALG_VALUES, not carry its own literal copy."
    )


def test_orm_purpose_check_narrows_to_request_signing():
    """(e) ck_signing_keys_purpose admits request-signing and nothing else."""
    check_values = check_constraint_values(SigningKey, "ck_signing_keys_purpose")
    assert check_values == _MINTABLE_PURPOSES, (
        f"ck_signing_keys_purpose CHECK values {check_values!r} != {_MINTABLE_PURPOSES!r}. "
        "A2 mints request-signing keys only; webhook-signing is deprecated pending "
        "removal (adcp#5555) and webhooks are signed with a request-signing key."
    )


def test_mintable_purpose_still_accepted_by_sdk_keygen():
    """(f) The one purpose we mint is still in the SDK's accepted adcp_use set.

    Reaching into a private SDK name is acceptable in a guard (and only here) —
    production code must not. This is the direction the narrowing CANNOT check
    for itself: our CHECK is deliberately a subset, so it cannot detect the SDK
    dropping the value we depend on.
    """
    from adcp.signing.keygen import _ADCP_USE_VALUES

    for purpose in _MINTABLE_PURPOSES:
        assert purpose in _ADCP_USE_VALUES, (
            f"{purpose!r} is no longer in adcp.signing.keygen._ADCP_USE_VALUES "
            f"{_ADCP_USE_VALUES!r} — generate_signing_keypair would raise ValueError "
            "for every key A2 provisions."
        )
