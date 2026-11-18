"""An unserializable test report must not silently void an xdist session.

Background and the full measurement live in ``tests/_xdist_report_safety``.
In short: pytest copies ``report.__dict__`` onto the execnet wire and sanitizes
only three keys; execnet dispatches on EXACT type; and a ``DumpError`` on a
worker ends the whole session after reporting only the tests already collected
back, behind a summary line that says zero failures.

These tests pin three things:

1. the walker replaces exactly what execnet refuses and nothing else,
2. the result genuinely round-trips through **execnet's own serializer** rather
   than through this module's model of it,
3. end to end, a run that would otherwise be truncated reports every item -- and
   the control case proves that assertion is not vacuous.
"""

from __future__ import annotations

import collections
import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, Mock, NonCallableMock, create_autospec

import pytest
from execnet.gateway_base import DumpError, dumps

from tests._xdist_report_safety import make_execnet_safe, sanitize_serialized_report

REPO_ROOT = Path(__file__).resolve().parents[2]


class _WeirdList(list):
    pass


class _WeirdSet(set):
    pass


# ---------------------------------------------------------------------------
# 1. The walker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, -1, 2**70, 1.5, complex(1, 2), "s", b"b", (), [], {}, set(), frozenset()],
    ids=repr,
)
def test_already_safe_values_are_returned_unchanged(value):
    """The common path must not rewrite a report that was already fine."""
    safe, offenders, _changed = make_execnet_safe(value)
    assert offenders == []
    assert safe == value
    assert type(safe) is type(value)


def test_unserializable_leaf_is_replaced_by_its_repr_and_named():
    mock = MagicMock()
    safe, offenders, _changed = make_execnet_safe({"leaked": mock})
    assert offenders == [".leaked = unittest.mock.MagicMock"]
    assert safe["leaked"] == repr(mock)


def test_offender_path_locates_the_value_inside_a_nested_report():
    """The reported path is the diagnostic — it must pinpoint the real key."""
    payload = {"_json_report_extra": {"call": {"log": [{"name": "x", "process": MagicMock()}]}}}
    _, offenders, _changed = make_execnet_safe(payload)
    assert offenders == ["._json_report_extra.call.log[0].process = unittest.mock.MagicMock"]


def test_subclasses_of_serializable_types_are_replaced_too():
    """execnet dispatches on exact type, so a str subclass is refused like a mock."""

    class Weird(str):
        pass

    safe, offenders, _changed = make_execnet_safe({"k": Weird("v")})
    assert offenders == [f".k = {Weird.__module__}.{Weird.__qualname__}"]
    with pytest.raises(DumpError):
        dumps({"k": Weird("v")})
    dumps(safe)


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: collections.OrderedDict(a=1), id="OrderedDict"),
        pytest.param(lambda: collections.defaultdict(int, a=1), id="defaultdict"),
        pytest.param(lambda: collections.Counter("ab"), id="Counter"),
        pytest.param(lambda: collections.namedtuple("Point", "x y")(1, 2), id="namedtuple"),
        pytest.param(lambda: _WeirdList([1, 2]), id="list-subclass"),
        pytest.param(lambda: _WeirdSet({1, 2}), id="set-subclass"),
    ],
)
def test_container_subclasses_are_rebuilt_at_the_boundary_the_hook_calls(factory):
    """Regression: a container subclass produces NO offender, so a gate on
    ``offenders`` returns the original undumpable object and discards the
    rebuilt one.

    This asserts through ``sanitize_serialized_report`` -- the function the
    conftest hook actually calls -- and against execnet's own ``dumps``. The
    earlier version of this test called ``make_execnet_safe`` directly and
    asserted ``offenders == []``, which was true while the shipped path was
    broken: it graded the inner function instead of the boundary. A plain
    ``collections.Counter`` in ``extra=`` on one PASSING test then killed a
    ``-n 2`` session and lost 77 of 85 items with nothing reported.
    """
    payload = {"nodeid": "t::x", "_json_report_extra": {"call": {"log": [{"process": 1, "v": factory()}]}}}
    with pytest.raises(DumpError):
        dumps(payload)
    out = sanitize_serialized_report(payload, nodeid="t::x", stream=io.StringIO())
    assert out is not payload
    dumps(out)


def test_an_unserializable_dict_KEY_is_replaced_and_the_rebuild_is_kept():
    """The ``key_changed`` operand is load-bearing, not defensive padding.

    Without it, a report whose only offender sits in the KEY position returns
    the ORIGINAL, still-undumpable dict while ``offenders`` is non-empty --
    the same class of bug as the container-subclass case above, moved one
    position over. Mutating `changed = changed or key_changed or item_changed`
    to drop `key_changed` left this module at 32 passed before this test.
    """
    payload = {"nodeid": "t::x", "_json_report_extra": {MagicMock(): "v"}}
    with pytest.raises(DumpError):
        dumps(payload)

    safe, offenders, changed = make_execnet_safe(payload)
    assert changed is True
    assert offenders == ["._json_report_extra<key> = unittest.mock.MagicMock"]
    dumps(safe)  # the REBUILT value is what must go on the wire


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: MagicMock(spec=dict), id="MagicMock-spec-dict"),
        pytest.param(lambda: Mock(spec=dict), id="Mock-spec-dict"),
        pytest.param(lambda: Mock(spec_set=dict), id="Mock-spec_set-dict"),
        pytest.param(lambda: NonCallableMock(spec=dict), id="NonCallableMock-spec-dict"),
        pytest.param(lambda: create_autospec(dict), id="create_autospec-dict"),
        pytest.param(lambda: Mock(spec=collections.OrderedDict), id="Mock-spec-OrderedDict"),
        pytest.param(lambda: MagicMock(spec=list), id="MagicMock-spec-list"),
        pytest.param(lambda: Mock(spec=list), id="Mock-spec-list"),
        pytest.param(lambda: MagicMock(spec=set), id="MagicMock-spec-set"),
    ],
)
def test_a_mock_that_only_LOOKS_like_a_container_is_replaced_and_announced(factory, request):
    """A container ``spec`` spoofs ``__class__``, so ``isinstance`` says yes.

    The branches dispatch on ``issubclass(type(value), ...)`` for exactly this
    reason -- the same exact-type semantics the atom branch uses, and the same
    semantics execnet itself uses. Under ``isinstance`` these took two paths,
    both violating this module's "REPLACED, never dropped, and always
    announced" contract: the non-magic mocks raised ``TypeError`` out of the
    walk and killed the worker (measured: one PASSING probe test lost 5373 of
    5757 items at ``-n 4``), while the magic ones returned an EMPTY container
    with no offender and no announcement -- a value destroyed in silence.

    ``create_autospec`` is included because it is the form the stdlib docs
    steer people toward.
    """
    # A distinct nodeid per case: announcements are deduped per
    # (nodeid, offenders), and every Mock variant here yields the same offender
    # string -- a shared nodeid would silence 6 of the 9 and grade nothing.
    nodeid = f"t::{request.node.name}"
    payload = {"nodeid": nodeid, "_json_report_extra": {"cfg": factory()}}
    with pytest.raises(DumpError):
        dumps(payload)

    stream = io.StringIO()
    out = sanitize_serialized_report(payload, nodeid=nodeid, stream=stream)

    dumps(out)  # must cross the wire
    assert "unittest.mock" in stream.getvalue(), (
        f"a destroyed value must be announced, got stderr={stream.getvalue()!r}"
    )
    assert out["_json_report_extra"]["cfg"] != {}, "the mock was replaced by an EMPTY container, not by its repr"


def test_the_walk_cannot_itself_become_the_crash_it_prevents():
    """This code runs in a hookwrapper on every report of every test.

    An exception out of it kills the worker and ends the session green over a
    truncated run -- which is the failure the module exists to prevent, so the
    net must be total: whatever it cannot walk degrades to a repr.

    The realistic trigger is a container that raises ON ITERATION.
    ``sqlalchemy.orm.collections.InstrumentedList`` IS a ``list`` subclass, so
    it passes ``issubclass`` and gets iterated; a DETACHED lazy collection
    raises ``DetachedInstanceError`` when it does. Reports are serialized at
    teardown, after the session is gone. The stand-in below is local because
    constructing a detached ORM collection would cost this unit test a
    database, not because the shape is hypothetical.
    """

    class Detached(list):
        def __iter__(self):
            raise RuntimeError("Parent instance is not bound to a Session")

    safe, offenders, changed = make_execnet_safe({"k": Detached([1, 2])})

    assert changed is True
    assert len(offenders) == 1 and "unwalkable" in offenders[0], offenders
    dumps(safe)  # and the degraded value still crosses the wire


def test_cycles_terminate_instead_of_recursing_forever():
    payload: dict = {"name": "x"}
    payload["self"] = payload
    safe, offenders, _changed = make_execnet_safe(payload)
    assert offenders == [".self = <cycle> builtins.dict"]
    assert safe["name"] == "x"
    dumps(safe)


def test_depth_limit_terminates_a_pathological_structure():
    payload: dict = {}
    node = payload
    for _ in range(60):
        child: dict = {}
        node["n"] = child
        node = child
    safe, offenders, _changed = make_execnet_safe(payload)
    assert any("depth limit" in o for o in offenders)
    dumps(safe)


def test_a_repr_that_raises_does_not_become_a_second_crash():
    class Hostile:
        def __repr__(self):
            raise ValueError("no repr for you")

    safe, offenders, _changed = make_execnet_safe({"k": Hostile()})
    assert offenders == [f".k = {Hostile.__module__}.{Hostile.__qualname__}"]
    assert "unreprable" in safe["k"]
    dumps(safe)


# ---------------------------------------------------------------------------
# 2. Round-trip through execnet's own serializer
# ---------------------------------------------------------------------------


def test_a_realistic_polluted_report_fails_execnet_before_and_passes_after():
    """The load-bearing assertion: measured against execnet, not against a model of it.

    The payload is the real shape observed on this branch — pytest-json-report's
    ``_json_report_extra`` carrying ``dict(record.__dict__)`` for a log record
    whose ``process`` field is a mock because a leaked ``patch("os.getpid")``
    was live when the record was created.
    """
    report = {
        "nodeid": "tests/harness/test_harness_product.py::TestProductEnvContract::test_single_product",
        "outcome": "passed",
        "when": "call",
        "duration": 0.01,
        "sections": [],
        "user_properties": [],
        "_json_report_extra": {
            "call": {"log": [{"name": "src.core.tools.products", "levelname": "INFO", "process": MagicMock()}]}
        },
    }
    with pytest.raises(DumpError):
        dumps(report)
    with open(os.devnull, "w") as devnull:
        dumps(sanitize_serialized_report(report, nodeid=report["nodeid"], stream=devnull))


def test_sanitize_returns_the_same_object_only_when_nothing_had_to_be_rebuilt():
    """The fast path is keyed on "did the walk change anything", not on
    "were there offenders" -- see the subclass regression above."""
    clean = {"nodeid": "t::x", "outcome": "passed", "sections": [], "user_properties": [], "keywords": {"a": 1}}
    assert sanitize_serialized_report(clean) is clean
    dirty = {"nodeid": "t::x", "keywords": collections.OrderedDict(a=1)}
    assert sanitize_serialized_report(dirty, stream=io.StringIO()) is not dirty


def test_an_offender_is_announced_and_never_dropped_silently():
    """Silence is the failure mode being fixed; a quiet mutation would miss the point."""
    import io

    stream = io.StringIO()
    payload = {"nodeid": "t::x", "_json_report_extra": {"call": {"log": [{"process": MagicMock()}]}}}
    out = sanitize_serialized_report(payload, nodeid="t::x", stream=stream)
    announced = stream.getvalue()
    assert "t::x" in announced
    assert "._json_report_extra.call.log[0].process" in announced
    assert "MagicMock" in announced
    assert out["_json_report_extra"]["call"]["log"][0]["process"].startswith("<MagicMock")


# ---------------------------------------------------------------------------
# 3. End to end, with a control
# ---------------------------------------------------------------------------

_POLLUTING_SUITE = """
import collections
import logging
from unittest.mock import patch, MagicMock

log = logging.getLogger("probe")

def test_emits_a_record_while_os_getpid_is_patched():
    # Exactly the production shape: a live patch on os.getpid means
    # logging.LogRecord.__init__ stores a MagicMock in record.process.
    with patch("os.getpid", MagicMock()):
        log.warning("a message")
    assert True

def test_logs_a_container_subclass_via_extra():
    # No mock anywhere -- a plain Counter. execnet dispatches on EXACT type, so a
    # dict SUBCLASS is refused exactly like a mock, and it produces no "offender"
    # for a sanitizer to notice. src/adapters/gam/utils/logging.py:351 is one
    # `defaultdict` away from this shape today.
    log.warning("counted", extra={"counts": collections.Counter("ab")})
    assert True

def test_second():
    assert True

def test_third():
    assert True
"""

_CONFTEST_WITH_HOOK = """
import pytest
from tests._xdist_report_safety import sanitize_serialized_report

@pytest.hookimpl(hookwrapper=True)
def pytest_report_to_serializable(config, report):
    outcome = yield
    data = outcome.get_result()
    safe = sanitize_serialized_report(data, nodeid=getattr(report, "nodeid", "<unknown>"))
    if safe is not data:
        outcome.force_result(safe)
"""


def _run_isolated_suite(tmp_path: Path, *, with_hook: bool) -> tuple[str, dict]:
    (tmp_path / "test_polluting.py").write_text(textwrap.dedent(_POLLUTING_SUITE))
    (tmp_path / "conftest.py").write_text(textwrap.dedent(_CONFTEST_WITH_HOOK) if with_hook else "")
    report_path = tmp_path / "report.json"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "DATABASE_URL": ""}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            "-n",
            "2",
            "-q",
            "--tb=no",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "--json-report",
            f"--json-report-file={report_path}",
            "--json-report-indent=0",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=300,
    )
    summary = json.loads(report_path.read_text())["summary"] if report_path.exists() else {}
    return proc.stdout + proc.stderr, summary


@pytest.mark.timeout(300)
def test_without_the_hook_the_session_is_silently_truncated(tmp_path):
    """The control. Without this, the test below could pass for the wrong reason."""
    output, summary = _run_isolated_suite(tmp_path, with_hook=False)
    assert "INTERNALERROR" in output, output[-3000:]
    assert summary.get("total", 0) < summary.get("collected", 0), summary
    assert not summary.get("failed"), f"and it reports no failures while doing it: {summary}"


@pytest.mark.timeout(300)
def test_with_the_hook_every_collected_item_is_reported(tmp_path):
    output, summary = _run_isolated_suite(tmp_path, with_hook=True)
    assert "INTERNALERROR" not in output, output[-3000:]
    assert summary["collected"] == summary["total"] == 4, summary
    assert summary["passed"] == 4, summary
