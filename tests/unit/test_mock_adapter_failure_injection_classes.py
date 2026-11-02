"""The mock adapter's injected-failure knob selects an exception CLASS, not a recovery.

``_raise_injected_failure`` used to build ``AdCPAdapterError(..., recovery=<the
injected string>)``. That was the last place a free string became a wire recovery:
whatever a BDD Given step wrote into ``test_behavior`` reached the buyer verbatim,
including spellings that are not classifications at all — ``"retryable"`` shipped
that way from two steps.

``recovery`` is now a read-only property derived from the pinned enumMetadata, so
the knob cannot set it. It selects the class whose PINNED recovery is the value
asked for, which is the same invariant every production raise site now lives
under: possession of the class is the proof.

These grade that mapping directly. It is otherwise ungraded — the in-process BDD
suites inject through the mocked adapter and never reach this code, and the e2e
scenarios that do reach it assert non-persistence and the presence of a
suggestion, never the emitted class. A wrong mapping here would reach a buyer
through the Docker-hosted adapter with no test disagreeing.

Everything buyer-facing is graded by CHANNEL, not by sentence. Under ADR-010
``message`` and ``suggestion`` are read-only functions of the code, so an
assertion on ``str(exc)`` would grade ``CODE_TABLE`` rather than this adapter.
What belongs to this module is which CODE it raises, and that the injected
free text — a test fixture's own string, authored nowhere near the pin — stays
in the non-wire ``internal_detail`` and reaches no client-facing field
(AdCP 3.1.1 ``transport-errors.mdx`` § Security Considerations).
"""

from __future__ import annotations

import json

import pytest
from adcp.types import ErrorCode

from src.adapters.mock_ad_server import MockAdServer
from src.core.errors.codes import CODE_TABLE, ErrorCodeT, Recovery
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPConfigurationError,
    AdCPSalesAgentError,
    AdCPValidationError,
)


def _adapter_with_behavior(monkeypatch: pytest.MonkeyPatch, behavior: dict) -> MockAdServer:
    """A MockAdServer whose DB-backed test_behavior is *behavior*.

    Patches only the DB read: the mapping under test is pure, and standing up an
    AdapterConfig row would grade the repository rather than the classification.
    """
    adapter = MockAdServer.__new__(MockAdServer)
    monkeypatch.setattr(MockAdServer, "_read_test_behavior", lambda self: behavior)
    return adapter


def _buyer_facing_wire(exc: AdCPSalesAgentError) -> str:
    """Every field of *exc* a buyer can read, as one string to search.

    ``internal_detail`` is deliberately absent — it is non-wire by construction
    (``AdcpErrorResponse.of`` never carries it), which is exactly the property
    these tests assert about the injected text.
    """
    return json.dumps(
        {
            "message": exc.message,
            "suggestion": exc.suggestion,
            "details": exc.details.to_wire() if exc.details is not None else None,
        }
    )


def _assert_pinned_envelope(exc: AdCPSalesAgentError, *, code: ErrorCodeT) -> None:
    """Grade one raised failure against the pin, on every derived channel.

    Written once because all four raising tests grade the same envelope; only
    the code differs. The point of asserting the derivations rather than the
    literals is that no raise site can author them: a suggestion cannot be
    forgotten at a raise site the way it could when it was a parameter.
    """
    entry = CODE_TABLE[code]

    assert exc.error_code == code
    assert exc.recovery == entry.recovery
    assert exc.status_code == entry.status

    # Buyer-facing text is a function of the code (ADR-010), never authored here.
    assert exc.message == entry.message
    assert str(exc) == exc.message

    # The suggestion is first-class: owned by the table, resolved for every code,
    # and actionable — non-empty and saying what to DO, not restating what happened.
    assert exc.suggestion == entry.suggestion
    assert exc.suggestion.strip(), f"{code} must resolve a real correction hint, not an empty string"
    assert exc.suggestion != exc.message, "a suggestion that merely repeats the message tells the buyer nothing"


@pytest.mark.parametrize(
    ("injected", "expected_cls", "expected_code", "expected_recovery"),
    [
        ("transient", AdCPAdapterError, ErrorCode.SERVICE_UNAVAILABLE, Recovery.TRANSIENT),
        ("terminal", AdCPConfigurationError, ErrorCode.CONFIGURATION_ERROR, Recovery.TERMINAL),
        ("correctable", AdCPValidationError, ErrorCode.VALIDATION_ERROR, Recovery.CORRECTABLE),
    ],
)
def test_knob_selects_the_class_whose_pinned_recovery_it_names(
    monkeypatch: pytest.MonkeyPatch,
    injected: str,
    expected_cls: type[AdCPSalesAgentError],
    expected_code: ErrorCodeT,
    expected_recovery: Recovery,
) -> None:
    """Each pinned classification maps to the class that DERIVES it."""
    adapter = _adapter_with_behavior(monkeypatch, {"fail_on_create": True, "recovery": injected})

    with pytest.raises(expected_cls) as excinfo:
        adapter._raise_injected_failure("fail_on_create")

    exc = excinfo.value
    _assert_pinned_envelope(exc, code=expected_code)
    assert exc.recovery == expected_recovery, (
        f"injecting {injected!r} produced {exc.error_code}/{exc.recovery} — the knob must select "
        f"the class the pin classifies {injected!r}, not merely a class that looks related"
    )


def test_the_default_knob_is_the_transient_adapter_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An injection with no recovery key keeps the historical behaviour."""
    adapter = _adapter_with_behavior(monkeypatch, {"fail_on_create": True})

    with pytest.raises(AdCPAdapterError) as excinfo:
        adapter._raise_injected_failure("fail_on_create")

    _assert_pinned_envelope(excinfo.value, code=ErrorCode.SERVICE_UNAVAILABLE)
    assert excinfo.value.recovery == Recovery.TRANSIENT


def test_a_non_classification_knob_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value outside the pinned vocabulary raises instead of shipping.

    ``"retryable"`` is the live example — two BDD steps injected it, and before the
    mapping it reached the wire as a recovery no spec defines. No Quiet Failures:
    an unmappable knob is a broken test fixture and must say so, not silently pick
    a class.

    "Say so" is graded on the CLASS, which is the only channel the refusal has
    left. Buyer-facing text is derived from the code under ADR-010, and
    ``internal_detail`` is typed ``BaseException | None`` and never takes an
    authored string, so the refusal carries no sentence naming the rejected
    spelling. What is gradable is that the knob refuses rather than picking a
    class, with the classification that says the fault is the deployment's own
    (CONFIGURATION_ERROR / terminal / 500), and that the rejected spelling — a
    fixture's free string — reaches no client-facing field.
    """
    adapter = _adapter_with_behavior(monkeypatch, {"fail_on_create": True, "recovery": "retryable"})

    with pytest.raises(AdCPConfigurationError) as excinfo:
        adapter._raise_injected_failure("fail_on_create")

    exc = excinfo.value
    # A broken fixture is deployment/test configuration, and the buyer has no lever:
    # terminal and 5xx are the point of the class, not an incidental detail.
    _assert_pinned_envelope(exc, code=ErrorCode.CONFIGURATION_ERROR)

    # The refusal is the class itself, carrying no authored sentence about the knob.
    assert exc.internal_detail is None

    # The rejected spelling is a test fixture's own string; it has no business on a
    # buyer's wire, and neither does our knob vocabulary.
    wire = _buyer_facing_wire(exc)
    assert "retryable" not in wire, "a rejected fixture knob must never reach the buyer"
    assert "test_behavior" not in wire, "the buyer must not be told about our injection plumbing"


def test_the_flag_gates_the_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flag, no failure — the injection is opt-in per operation."""
    adapter = _adapter_with_behavior(monkeypatch, {"recovery": "terminal"})

    adapter._raise_injected_failure("fail_on_create")  # must not raise


def test_suggestion_stays_first_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised failure always carries a real suggestion, and the knob cannot author it.

    The suggestion used to be a ``suggestion=`` parameter, so "first-class" meant
    lifting an injected string out of ``details`` onto the error's own field —
    which a raise site could simply forget. Under ADR-010 it is OWNED by
    CODE_TABLE and resolved per read, so the obligation is stronger and sits one
    level up: the code raised here must resolve a real, actionable hint, and the
    injected ``error_details.suggestion`` — a fixture string, authored nowhere
    near the pin — must not displace it or leak anywhere a buyer can read.
    """
    adapter = _adapter_with_behavior(
        monkeypatch,
        {"fail_on_upload": True, "recovery": "terminal", "error_details": {"suggestion": "Fix the thing"}},
    )

    with pytest.raises(AdCPConfigurationError) as excinfo:
        adapter._raise_injected_failure("fail_on_upload")

    exc = excinfo.value
    # Asserts the suggestion is non-empty, actionable, and the pinned one for the code.
    _assert_pinned_envelope(exc, code=ErrorCode.CONFIGURATION_ERROR)

    assert "Fix the thing" not in _buyer_facing_wire(exc), (
        "a fault-injection knob must not author buyer-facing text — the suggestion is a "
        "function of the code, so an injected one can only be ignored, never promoted"
    )
