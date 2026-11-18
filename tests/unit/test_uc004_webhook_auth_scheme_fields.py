"""Regression tests for salesagent-fo99.6.

The BDD step tier translates a Gherkin auth scheme into the
``PushNotificationConfig`` auth columns. Two obligations, at two different loci:

``_auth_scheme_to_db_fields`` (per-call, during incremental Given setup):
  1. no scheme named                -> ``{}``
  2. canonical scheme + credential  -> both columns
  3. NOT a pinned AuthenticationScheme member -> raises ValueError

``_wire_webhook_db`` (end-state, at dispatch, after every Given has run):
  4. a scheme was named but no credential ever arrived -> raises AssertionError

Outcomes 3 and 4 are the bug: both used to return the SAME empty dict that
outcome 1 legitimately returns, so the caller persisted a row with no
authentication columns. A scenario whose Gherkin claims signing then graded an
unauthenticated config and passed, because unsigned delivery succeeds -- green
while measuring nothing.

Why 4 is graded at dispatch and not per-call: scenarios name the scheme on one
line and supply the credential on the next (BR-UC-004:252-253, :264-265), and
``given_webhook_auth_scheme`` persists the row on the first line. A missing
credential mid-setup is legitimate; only at dispatch is it a defect.

The canonical set is never spelled locally -- it comes from the SDK's pinned
``AuthenticationScheme``, whose members' values ARE the spec spellings.
"""

from __future__ import annotations

import pytest
from adcp.types import AuthenticationScheme

from tests.bdd.steps.domain.uc004_delivery import (
    _auth_scheme_to_db_fields,
    _credential_in_ctx,
)

SECRET = "s" * 40
BEARER = "b" * 40

HMAC = AuthenticationScheme.HMAC_SHA256
BEARER_SCHEME = AuthenticationScheme.Bearer


# --- Outcome 1: no scheme named -> {} ---


@pytest.mark.parametrize("ctx", [{}, {"webhook_secret": SECRET}, {"webhook_bearer_token": BEARER}])
def test_no_scheme_named_returns_no_columns(ctx):
    """A scenario that names no scheme wants an unauthenticated webhook."""
    assert _auth_scheme_to_db_fields(None, ctx) == {}


# --- Outcome 2: canonical scheme + its credential -> both columns ---


def test_hmac_with_secret_sets_both_columns():
    assert _auth_scheme_to_db_fields(HMAC, {"webhook_secret": SECRET}) == {
        "authentication_type": "HMAC-SHA256",
        "authentication_token": SECRET,
    }


def test_bearer_with_token_sets_both_columns():
    assert _auth_scheme_to_db_fields(BEARER_SCHEME, {"webhook_bearer_token": BEARER}) == {
        "authentication_type": "Bearer",
        "authentication_token": BEARER,
    }


def test_persisted_type_is_the_sdk_spelling_not_a_local_one():
    """The column value must be the pinned enum's spelling, byte for byte."""
    fields = _auth_scheme_to_db_fields(HMAC, {"webhook_secret": SECRET})
    assert fields["authentication_type"] == AuthenticationScheme.HMAC_SHA256


# --- Outcome 3: not a pinned member -> ValueError (was: silent {}) ---


@pytest.mark.parametrize(
    "scheme",
    [
        "hmac-sha256",  # canonical member, non-canonical casing
        "hmac_sha256",  # underscore spelling
        "hmac",  # the fifth spelling of GH #1894
        "bearer",  # canonical member, non-canonical casing
        "Basic",  # outside the pinned enum entirely
        "Frobnicate-Not-A-Scheme",  # not a scheme at all
    ],
)
def test_non_canonical_spelling_raises(scheme):
    """An unmappable spelling must raise, not persist a row with no auth."""
    with pytest.raises(ValueError) as exc:
        _auth_scheme_to_db_fields(scheme, {"webhook_secret": SECRET, "webhook_bearer_token": BEARER})
    assert scheme in str(exc.value), "the refusal must name the offending token"


def test_refusal_lists_the_canonical_spellings():
    """The message must tell a scenario author what IS accepted."""
    with pytest.raises(ValueError) as exc:
        _auth_scheme_to_db_fields("hmac", {})
    message = str(exc.value)
    assert "HMAC-SHA256" in message and "Bearer" in message


# --- Mid-setup tolerance: absence of a credential is NOT yet a defect ---


@pytest.mark.parametrize("scheme", [HMAC, BEARER_SCHEME])
def test_credential_not_yet_supplied_returns_no_columns(scheme):
    """The scheme Given runs before the credential Given; the row updates later."""
    assert _auth_scheme_to_db_fields(scheme, {}) == {}


# --- Exhaustive credential lookup: Bearer must not borrow the HMAC secret ---


def test_bearer_does_not_borrow_the_hmac_secret():
    """The lookup must be an exhaustive match, not not-HMAC-means-Bearer.

    The predecessor was a binary if/else on one member, so a scheme was read as
    Bearer purely by not being HMAC-SHA256.
    """
    assert _credential_in_ctx(AuthenticationScheme.Bearer, {"webhook_secret": SECRET}) is None


def test_hmac_does_not_borrow_the_bearer_token():
    assert _credential_in_ctx(AuthenticationScheme.HMAC_SHA256, {"webhook_bearer_token": BEARER}) is None


@pytest.mark.parametrize(
    ("scheme", "ctx", "expected"),
    [
        (AuthenticationScheme.HMAC_SHA256, {"webhook_secret": SECRET}, SECRET),
        (AuthenticationScheme.Bearer, {"webhook_bearer_token": BEARER}, BEARER),
    ],
)
def test_each_scheme_reads_its_own_credential(scheme, ctx, expected):
    assert _credential_in_ctx(scheme, ctx) == expected


def test_every_pinned_member_has_a_credential_source():
    """A new enum member must be a visible gap, not a silent wrong default."""
    for member in AuthenticationScheme:
        _credential_in_ctx(member, {})  # must not raise AssertionError
