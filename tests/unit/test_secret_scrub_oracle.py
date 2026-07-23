"""Known-bad self-tests for the shared secret-leak oracle.

``assert_no_secret_leak`` backs ~30 leak assertions across the A2A, webhook, and boundary
suites. Without a self-test the oracle can be silently defanged — emptying ``_SECRET_TOKENS``
(or dropping a single token) leaves every one of those callers GREEN while proving nothing.
These tests fail in exactly that case, so the oracle defends itself the same way the
failed-Task reader and the normalizer registry do.
"""

import pytest

from tests.helpers.secret_scrub import _SECRET_TOKENS, SECRET_BEARING_MESSAGE, assert_no_secret_leak


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


@pytest.mark.parametrize("token", _SECRET_TOKENS)
def test_oracle_detects_every_token_individually(token):
    """Each token in the set is load-bearing: dropping ANY ONE of them reddens here.

    A blanket "the whole message trips it" check would still pass with a single token removed,
    because the other fragments would fire. Parametrizing pins the set member-by-member.
    """
    with pytest.raises(AssertionError, match="leaked to the buyer-facing wire"):
        assert_no_secret_leak(f"prefix {token} suffix")


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
