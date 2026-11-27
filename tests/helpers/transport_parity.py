"""Assertions shared by in-process and live transport parity tests."""


def _assert_success_equivalent(via, client_result) -> None:
    """Shared success-path equivalence assertion ``env.call_via`` and ``AdCPTestClient(env).call`` must agree on
    success, transport tag, and the exact wire-visible response body.
    Module-level so both ``TestEnvVsClientEquivalence`` (in-process) and
    ``TestEnvVsClientEquivalenceE2E`` (live-stack) share one assertion
    instead of duplicating it (CLAUDE.md DRY invariant).
    """
    assert via.is_success, via.error
    assert client_result.is_success, client_result.error
    assert via.envelope.get("transport") == client_result.envelope.get("transport")
    assert via.wire_response == client_result.wire_response


def _assert_error_equivalent(via, client_result, code: str, *, recovery: str, check_auth: bool = False) -> None:
    """Shared error-path equivalence assertion. See ``_assert_success_equivalent``.

    ``recovery`` is passed EXPLICITLY rather than left to ``assert_wire_error``'s
    CODE_TABLE default. The default is derived from the same table production
    emits from, so a drift in that table would move the expectation and the
    emission together and this assertion would stay green through it; the literal
    is the pinned spec's classification, checked independently.
    """
    from src.core.errors.codes import CODE_TABLE

    assert via.is_error, "env.call_via unexpectedly succeeded"
    assert client_result.is_error, "AdCPTestClient.call unexpectedly succeeded"
    via.assert_wire_error(code, recovery=recovery)
    client_result.assert_wire_error(code, recovery=recovery)
    if check_auth:
        via.assert_auth_challenge(code)
        client_result.assert_auth_challenge(code)
    # ADR-010: the buyer-facing sentence is a read-only CODE_TABLE property, so it
    # is asserted by identity with the table rather than against a literal.
    assert via.wire_error_envelope["adcp_error"]["message"] == CODE_TABLE[code].message, (
        f"adcp_error.message={via.wire_error_envelope['adcp_error']['message']!r}, "
        f"expected the first-party sentence {CODE_TABLE[code].message!r}"
    )
    assert via.wire_error_envelope == client_result.wire_error_envelope
    # The transport envelope too, not just the wire body: the two paths held
    # SEPARATE copies of each transport's error unwrap, which is how the derived
    # `status` came to exist on the dispatcher path and not on the client path
    # while every wire_error_envelope assertion stayed green. Comparing the
    # envelope makes that divergence observable here (wire-grading remediation).
    assert via.envelope == client_result.envelope, (
        f"env.call_via and AdCPTestClient.call disagree on the error envelope: "
        f"{via.envelope!r} vs {client_result.envelope!r}"
    )
