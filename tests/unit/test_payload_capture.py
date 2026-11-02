"""The recorder must refuse what it cannot write, and never write a silent gap.

Two failure modes are graded here, and both have already happened once on this
tree rather than being imagined:

* A SERIALIZER THAT DROPS. ``json_safe`` is not total — 33 of 2905 real payloads
  hold a value ``json.dump`` cannot write — and the scan's first full run lost 4
  of 8 worker shards to a truncated, unparseable artifact, written without an
  error and discovered only on read. A half-written capture that reads as a
  complete "before" is this gate's worst possible failure, so the serializer
  RAISES at the dispatch that produced the value.
* AN ABSENT ROW READING AS AGREEMENT. Two thirds of BDD nodeids dispatch nothing.
  If the recorder wrote rows only for the ones that did, "ran and dispatched
  nothing" and "did not run" would be the same artifact and the comparator's
  NOT_MEASURED verdict would have nothing to fire on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import AnyUrl

from tests.bdd import payload_capture
from tests.bdd.payload_capture import capture_payload, record_dispatched_request


@pytest.fixture(autouse=True)
def _drain() -> None:
    """Each test starts with an empty pending list."""
    payload_capture._PENDING.clear()


# ── The serializer ───────────────────────────────────────────────────


def test_pydantic_url_is_captured_as_the_string_the_wire_carries() -> None:
    captured = capture_payload({"format_ids": [{"agent_url": AnyUrl("https://creative.example/mcp"), "id": "f"}]})
    assert captured == {"format_ids": [{"agent_url": "https://creative.example/mcp", "id": "f"}]}


def test_omit_sentinels_are_interned_by_identity_not_repr() -> None:
    """``repr()`` would carry a ``0x`` address and mark every such row CHANGED forever."""
    from tests.factories.request import OMIT
    from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY

    first = capture_payload({"a": OMIT, "b": OMIT_ACCOUNT, "c": OMIT_IDEMPOTENCY_KEY})
    second = capture_payload({"a": OMIT, "b": OMIT_ACCOUNT, "c": OMIT_IDEMPOTENCY_KEY})
    assert first == {"a": "<omit>", "b": "<omit:account>", "c": "<omit:idempotency_key>"}
    assert first == second, "a sentinel must intern to the same token on every run"


def test_an_unknown_unserializable_value_raises_rather_than_disappearing() -> None:
    class Opaque:
        pass

    with pytest.raises(TypeError, match="payload_capture cannot serialize"):
        capture_payload({"creatives": [{"thing": Opaque()}]})


def test_datetimes_and_models_travel_the_way_the_wire_carries_them() -> None:
    captured = capture_payload({"start_time": datetime(2026, 1, 2, 3, 4, tzinfo=UTC)})
    assert captured == {"start_time": "2026-01-02T03:04:00+00:00"}


def test_the_captured_form_is_json_round_tripped() -> None:
    """Not "looks serializable" — actually written and read back, at capture time."""
    captured = capture_payload({"packages": ({"budget": 1},)})
    assert json.loads(json.dumps(captured)) == captured
    assert captured == {"packages": [{"budget": 1}]}, "tuples become lists, as JSON has no tuple"


# ── identity ─────────────────────────────────────────────────────────


def test_identity_is_reduced_to_whether_it_was_supplied() -> None:
    """It is not a request field (``_deliver_via_client`` pops it), it carries auth
    tokens, and its contents are factory-minted. What a migration could change is
    whether the dispatch carried auth at all, so that is what is kept."""
    record_dispatched_request({"identity": None, "req": {"x": 1}})
    record_dispatched_request({"identity": object(), "req": {"x": 1}})
    record_dispatched_request({"req": {"x": 1}})
    assert payload_capture._PENDING == [
        {"identity": "<identity:none>", "req": {"x": 1}},
        {"identity": "<identity:supplied>", "req": {"x": 1}},
        {"req": {"x": 1}},
    ]


def test_identity_never_reaches_the_artifact_verbatim() -> None:
    """An ``object()`` identity would otherwise raise in the serializer, which proves
    the tokenization happens BEFORE serialization rather than being a lucky no-op."""

    class Identity:
        auth_token = "token_00000330"

    record_dispatched_request({"identity": Identity()})
    assert "token_00000330" not in json.dumps(payload_capture._PENDING)


# ── The empty row ────────────────────────────────────────────────────


def test_the_xdist_controller_records_nothing() -> None:
    """The defect the first full run produced, in one assertion.

    ``logstart``/``logfinish`` fire on the CONTROLLER as well — xdist forwards every
    worker's report through them — so the controller built a complete set of EMPTY
    rows for all 8182 nodeids and wrote them over the rows the workers had shipped.
    The artifact parsed, looked complete, and said no test dispatched anything.
    """

    class _Pluginmanager:
        @staticmethod
        def getplugin(name: str) -> object | None:
            return object() if name == "dsession" else None

    class _ControllerSession:
        class config:  # noqa: N801 - mimics pytest.Session.config
            pluginmanager = _Pluginmanager

    payload_capture._RECORDS.clear()
    try:
        payload_capture.pytest_sessionstart(_ControllerSession)  # type: ignore[arg-type]
        assert payload_capture._RECORD_LOCALLY is False
        payload_capture.pytest_runtest_logstart("t::ran_on_a_worker", None)
        record_dispatched_request({"n": 1})
        payload_capture.pytest_runtest_logfinish("t::ran_on_a_worker", None)
        assert payload_capture._RECORDS == {}, (
            "the controller must contribute no rows: an empty row it invents for a test that "
            "ran elsewhere overwrites the worker's real one"
        )
    finally:
        payload_capture._RECORD_LOCALLY = True
        payload_capture._PENDING.clear()


def test_a_test_that_dispatched_nothing_still_gets_a_row() -> None:
    payload_capture._RECORDS.clear()
    payload_capture.pytest_runtest_logstart("tests/bdd/test_x.py::test_quiet", None)
    payload_capture.pytest_runtest_logfinish("tests/bdd/test_x.py::test_quiet", None)
    assert payload_capture._RECORDS == {"tests/bdd/test_x.py::test_quiet": []}, (
        "an absent row is indistinguishable from a test that did not run"
    )


def test_dispatches_are_attributed_to_the_test_that_made_them_in_order() -> None:
    payload_capture._RECORDS.clear()
    payload_capture.pytest_runtest_logstart("t::a", None)
    record_dispatched_request({"n": 1})
    record_dispatched_request({"n": 2})
    payload_capture.pytest_runtest_logfinish("t::a", None)
    payload_capture.pytest_runtest_logstart("t::b", None)
    record_dispatched_request({"n": 3})
    payload_capture.pytest_runtest_logfinish("t::b", None)
    assert payload_capture._RECORDS == {"t::a": [{"n": 1}, {"n": 2}], "t::b": [{"n": 3}]}


# ── Interning ────────────────────────────────────────────────────────


def test_minted_values_intern_but_keep_their_equality_relations() -> None:
    """A stable token per distinct value, not one token for all of them: two fields
    that named the same id must still read as naming the same id, and a migration
    that breaks that cross-reference must still be a diff."""
    from tests.factories.mint import begin_test, mint

    begin_test()
    generated_a = mint("mb-001-7a693ba8")
    generated_b = mint("pkg_0f1e2d3c")
    interned = payload_capture._intern_minted(
        [{"media_buy_id": generated_a, "echo": generated_a, "package_id": generated_b, "pinned": "prod_001"}]
    )
    assert interned == [
        {"media_buy_id": "<minted:0>", "echo": "<minted:0>", "package_id": "<minted:1>", "pinned": "prod_001"}
    ]
    begin_test()


def test_a_value_nobody_minted_is_diffed_verbatim() -> None:
    """The pinned literal that LOOKS generated. No rule over the value could keep it."""
    from tests.factories.mint import begin_test

    begin_test()
    assert payload_capture._intern_minted([{"idempotency_key": "test-idem-key-0001"}]) == [
        {"idempotency_key": "test-idem-key-0001"}
    ]
