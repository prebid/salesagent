"""The mint record is DERIVED from where values are generated, not declared.

Per-field and per-value normalization both fail on this tree, with symmetric
counterexamples measured against 2905 real dispatched payloads:
``idempotency_key`` carries the pinned literal ``"test-idem-key-0001"`` on 210 of
587 events (looks generated), and ``media_buy_ids[]`` carries
``"mb-001-7a693ba8"`` (looks pinned). One rule erases a real difference; the other
manufactures noise. The difference is not in the value, it is in where the value
came from — so the mint site is asked.

The two properties that make it trustworthy, and both are graded here:

* A CALLER'S EXPLICIT ARGUMENT IS NOT A MINT. ``Factory(field="mb-001")`` is a
  pinned literal the scenario chose, and interning it would blind the gate to
  exactly the migration it watches.
* IT DOES NOT LEAK BETWEEN TESTS. A value generated while test A ran must not
  intern a literal that test B dispatches.
"""

from __future__ import annotations

import factory
import pytest

from tests.factories.mint import (
    begin_test,
    install_declaration_recording,
    mint,
    mint_shared,
    minted_forms,
    uninstall_declaration_recording,
)


class _Thing:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _ThingFactory(factory.Factory):
    class Meta:
        model = _Thing

    ident = factory.Sequence(lambda n: f"mint-test-{n:04d}")
    label = factory.LazyFunction(lambda: "mint-test-lazy")
    count = factory.Sequence(lambda n: n)


@pytest.fixture(autouse=True)
def _isolated_record() -> None:
    begin_test()
    yield
    begin_test()
    uninstall_declaration_recording()


# ── What counts as a mint ────────────────────────────────────────────


def test_factory_declarations_are_recorded_without_naming_the_factory() -> None:
    """Derived, not declared: the wrapper sits on ``BaseDeclaration.evaluate_pre``,
    so a factory written tomorrow is recorded with no registration step."""
    install_declaration_recording()
    thing = _ThingFactory()
    assert thing.ident in minted_forms()
    assert thing.label in minted_forms()


def test_an_explicit_override_is_not_a_mint() -> None:
    """The pinned literal a scenario chose. Interning it is how the gate goes blind."""
    install_declaration_recording()
    _ThingFactory(ident="mb-001")
    assert "mb-001" not in minted_forms()


def test_numbers_are_never_recorded() -> None:
    """A ``Sequence`` yielding 0 would otherwise intern every zero in every payload."""
    install_declaration_recording()
    _ThingFactory()
    assert all(isinstance(form, str) for form in minted_forms())
    assert "0" not in minted_forms()


def test_datetimes_are_recorded_in_both_spellings() -> None:
    """``json_safe`` writes ``isoformat()``; a step formatting one into an id uses
    ``str``. Recording one spelling interns on some transports and not others."""
    from datetime import UTC, datetime

    moment = datetime(2026, 5, 4, 3, 2, tzinfo=UTC)
    mint(moment)
    assert moment.isoformat() in minted_forms()
    assert str(moment) in minted_forms()


# ── Scope ────────────────────────────────────────────────────────────


def test_the_per_test_record_does_not_leak_into_the_next_test() -> None:
    mint("generated-by-the-previous-test")
    begin_test()
    assert "generated-by-the-previous-test" not in minted_forms()


def test_a_cached_generator_records_for_the_whole_process() -> None:
    """``_campaign_window`` is ``@cache``d, so it mints once and every later test
    reuses the value without re-minting. A per-test record would forget it and
    ~200 dispatched ``start_time`` values would read CHANGED forever."""
    mint_shared("minted-once-per-process")
    begin_test()
    assert "minted-once-per-process" in minted_forms()


def test_the_real_campaign_window_is_recorded_as_shared() -> None:
    from tests.factories.request import _campaign_window

    start, end = _campaign_window()
    begin_test()
    assert start.isoformat() in minted_forms()
    assert end.isoformat() in minted_forms()


def test_the_real_idempotency_generator_is_recorded_per_test() -> None:
    from tests.factories.request import fresh_idempotency_key

    key = fresh_idempotency_key()
    assert key in minted_forms()
    begin_test()
    assert key not in minted_forms()
