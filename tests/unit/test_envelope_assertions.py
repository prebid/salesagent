"""Unit tests for the envelope-assertion helper's field pinning.

Guards the ``field=`` (exact, both layers) vs ``field_substr=`` (substring, one
layer) distinction added in #1329: a substring token like
``governance_agents`` is a prefix of several governance field paths and stays
green on a field wrong for the scenario, so field-level assertions should prefer
exact ``field=``.
"""

from __future__ import annotations

import pytest

from tests.helpers.envelope_assertions import assert_envelope_shape


def _envelope(field: str) -> dict:
    """A two-layer VALIDATION_ERROR envelope whose field is mirrored on both layers."""
    err = {"code": "VALIDATION_ERROR", "message": "m", "recovery": "correctable", "field": field}
    return {"adcp_error": dict(err), "errors": [dict(err)]}


def test_field_exact_match_passes():
    assert_envelope_shape(
        _envelope("accounts[0].governance_agents"),
        "VALIDATION_ERROR",
        recovery="correctable",
        field="accounts[0].governance_agents",
    )


def test_field_exact_mismatch_fails():
    with pytest.raises(AssertionError):
        assert_envelope_shape(
            _envelope("accounts[0].governance_agents[0].authentication.credentials"),
            "VALIDATION_ERROR",
            recovery="correctable",
            field="accounts[0].governance_agents",
        )


def test_exact_field_catches_what_substring_misses():
    """The core D concern: substring "governance_agents" passes on the credentials path
    (wrong field for a cardinality scenario); exact field= rejects it."""
    env = _envelope("accounts[0].governance_agents[0].authentication.credentials")
    # Loose substring stays green on the wrong field...
    assert_envelope_shape(env, "VALIDATION_ERROR", recovery="correctable", field_substr="governance_agents")
    # ...exact equality catches it.
    with pytest.raises(AssertionError):
        assert_envelope_shape(env, "VALIDATION_ERROR", recovery="correctable", field="accounts[0].governance_agents")


def test_field_checks_both_layers():
    """Exact field= pins BOTH layers — a divergent adcp_error.field mirror fails."""
    env = _envelope("accounts")
    env["adcp_error"]["field"] = "wrong"
    with pytest.raises(AssertionError):
        assert_envelope_shape(env, "VALIDATION_ERROR", recovery="correctable", field="accounts")
