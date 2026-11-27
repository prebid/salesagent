"""Both fixture paths must share the complete ordered gate."""

import ast
import inspect
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_git_tracked_files, parse_module

ROOT = Path(__file__).resolve().parents[2]


def _health_poll_lines(tree: ast.Module) -> list[int]:
    """Find literal /health polls with sleep, including keyword and f-string URLs.

    This crosses the loop's expressions (which may be nested in try/with/if).
    Indirect URL variables and arbitrary retry abstractions are not resolved.
    """
    lines = []
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.While)):
            continue
        calls = [n for n in ast.walk(loop) if isinstance(n, ast.Call)]
        sleeps = any(
            (isinstance(n.func, ast.Attribute) and n.func.attr == "sleep")
            or (isinstance(n.func, ast.Name) and n.func.id == "sleep")
            for n in calls
        )
        gets = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == "get"]
        if sleeps and any(
            isinstance(n, ast.Constant) and isinstance(n.value, str) and "/health" in n.value
            for call in gets
            for arg in [*call.args, *(kw.value for kw in call.keywords)]
            for n in ast.walk(arg)
        ):
            lines.append(loop.lineno)
    return lines


def test_fixture_waits_once_after_both_startup_paths():
    tree = parse_module(ROOT / "tests/e2e/conftest.py")
    fixture = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "docker_services_e2e")
    # A top-level call after the startup if/else reaches reuse AND standalone.
    waits = [
        n
        for n in fixture.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "wait_for_e2e_stack"
    ]
    assert len(waits) == 1, "Both startup branches must converge on one shared readiness call"
    startup = next(
        n
        for n in fixture.body
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "use_existing_services"
    )
    assert startup.end_lineno < waits[0].lineno
    assert waits[0].lineno < min(n.lineno for n in ast.walk(fixture) if isinstance(n, ast.Yield))


def test_no_tracked_e2e_caller_can_reduce_the_gate():
    from tests.e2e import stack_readiness

    assert set(inspect.signature(stack_readiness.wait_for_e2e_stack).parameters) == {
        "ports",
        "compose_files",
        "host",
        "timeout_s",
        "poll_interval_s",
    }
    assert [name for name, _probe in stack_readiness._PROBES] == ["postgres", "creative-agent", "adcp_health"]
    paths = [p for p in iter_git_tracked_files(ROOT) if p.is_relative_to(ROOT / "tests/e2e") and p.suffix == ".py"]
    assert len(paths) > 3, "The guard must scan the E2E tree, including future callers"
    for path in paths:
        tree = parse_module(path)
        if path.name != "stack_readiness.py":
            assert not _health_poll_lines(tree), f"{path} introduces an independent readiness loop"


@pytest.mark.parametrize("url", ['"http://proxy/health"', 'f"http://{host}/health"', 'url="http://proxy/health"'])
@pytest.mark.parametrize("sleep", ["time.sleep(2)", "sleep(2)"])
def test_health_poll_detector_rejects_relocated_loops(url, sleep):
    tree = ast.parse(f"def another_helper():\n    while True:\n        client.get({url})\n        {sleep}\n")
    assert _health_poll_lines(tree) == [2]


def test_health_poll_detector_allows_other_polling_and_single_health_requests():
    tree = ast.parse(
        'client.get("http://proxy/health")\nwhile True:\n    client.get("http://proxy/status")\n    time.sleep(2)'
    )
    assert _health_poll_lines(tree) == []
