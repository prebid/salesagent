"""Known-bad self-tests for the shared secret-leak oracle.

``assert_no_secret_leak`` backs ~30 leak assertions across the A2A, webhook, and boundary
suites. Without a self-test the oracle can be silently defanged — emptying ``_SECRET_TOKENS``,
or dropping a single token, leaves every one of those callers GREEN while proving nothing.

The expectations here are stated INDEPENDENTLY of the constants they grade (see
``_EXPECTED_LEAK_FRAGMENTS``): a test parametrized over ``_SECRET_TOKENS`` would delete its own
case when a token was removed rather than reddening, which is no oracle at all.
"""

import pytest

from tests.helpers.secret_scrub import _SECRET_TOKENS, SECRET_BEARING_MESSAGE, assert_no_secret_leak

# INDEPENDENT restatement of what the oracle must detect — deliberately NOT derived from
# ``_SECRET_TOKENS``. Parametrizing over the constant under test is circular: deleting a token
# would delete its own test case instead of reddening one, so the suite would stay green while
# the oracle silently weakened. Declared here, every drop from the shared set fails
# ``test_shared_token_set_matches_the_independent_expectation`` below.
#
# Not listed: the ``svc`` user and ``prod`` database name from the connection string. They are
# real secret fragments but unusable as bare substrings — "prod" matches the legitimate word
# "product", which appears throughout this codebase's buyer-facing copy, so tokenizing them
# would fire on clean messages. The surrounding ``postgresql://`` and ``db.internal`` fragments
# already catch any leak of that connection string.
_EXPECTED_LEAK_FRAGMENTS = (
    "hunter2",  # password
    "postgresql://",  # connection-string scheme
    "db.internal",  # internal hostname
    "TOKEN=abc123",  # bearer credential
    "SELECT",  # inline SQL
    "principals",  # internal table name
)


def test_oracle_rejects_the_secret_bearing_message_as_a_string():
    """The canonical leak payload must trip the oracle in its string form.

    Reddens if ``_SECRET_TOKENS`` is emptied — the exact defang the oracle can't otherwise see.
    """
    with pytest.raises(AssertionError, match="leaked to the buyer-facing wire"):
        assert_no_secret_leak(SECRET_BEARING_MESSAGE)


def test_oracle_rejects_the_secret_bearing_message_inside_an_envelope_dict():
    """Same payload nested in a two-layer envelope — the shape most callers actually pass.

    The oracle JSON-serializes non-str input, so a secret buried in a nested error object must
    be caught just as it is in a flat string.
    """
    envelope = {
        "adcp_error": {"code": "SERVICE_UNAVAILABLE", "message": SECRET_BEARING_MESSAGE},
        "errors": [{"code": "SERVICE_UNAVAILABLE", "message": SECRET_BEARING_MESSAGE}],
    }
    with pytest.raises(AssertionError, match="leaked to the buyer-facing wire"):
        assert_no_secret_leak(envelope)


@pytest.mark.parametrize("fragment", _EXPECTED_LEAK_FRAGMENTS)
def test_oracle_detects_every_expected_fragment_individually(fragment):
    """Each fragment must trip the oracle ON ITS OWN.

    Parametrized over the INDEPENDENT list, not ``_SECRET_TOKENS``: dropping a token from the
    shared set leaves this case standing and RED, which is the whole point. A blanket
    "the whole message trips it" check would stay green with a single token removed, because
    the remaining fragments would still fire.
    """
    with pytest.raises(AssertionError, match="leaked to the buyer-facing wire"):
        assert_no_secret_leak(f"prefix {fragment} suffix")


def test_shared_token_set_matches_the_independent_expectation():
    """The shared ``_SECRET_TOKENS`` must equal the independent list above.

    This is the tie that makes the parametrization non-circular: a token added to the shared set
    without updating the expectation (or removed from it) fails here, so the two literals cannot
    drift apart silently.
    """
    assert set(_SECRET_TOKENS) == set(_EXPECTED_LEAK_FRAGMENTS), (
        "shared token set drifted from the independent expectation — update both deliberately"
    )


@pytest.mark.parametrize("fragment", _EXPECTED_LEAK_FRAGMENTS)
def test_canonical_message_actually_carries_every_fragment(fragment):
    """``SECRET_BEARING_MESSAGE`` must literally contain each fragment it is supposed to model.

    The message and the token set are two independent literals; nothing else ties them. Without
    this, the canonical payload could stop carrying (say) the bearer token while every caller
    that injects it kept passing — proving the scrub only against the fragments that remained.
    """
    assert fragment in SECRET_BEARING_MESSAGE, (
        f"{fragment!r} is expected to leak but is absent from SECRET_BEARING_MESSAGE"
    )


def test_oracle_passes_a_clean_blob():
    """The counter-control: a scrubbed message must NOT trip the oracle.

    Without this, an oracle that raised unconditionally would satisfy every known-bad test
    above while making all ~30 callers vacuous in the opposite direction.
    """
    assert_no_secret_leak("An internal error occurred while processing the request.")
    assert_no_secret_leak({"adcp_error": {"code": "SERVICE_UNAVAILABLE", "message": "Request failed."}})


def test_oracle_refuses_none_rather_than_passing_vacuously():
    """An absent value must fail loudly: a caller asserting on a field production stopped
    populating would otherwise "pass" while proving nothing."""
    with pytest.raises(AssertionError, match="cannot prove a scrub"):
        assert_no_secret_leak(None)
