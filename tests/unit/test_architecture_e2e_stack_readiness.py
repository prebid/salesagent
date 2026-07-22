"""Architecture guard: E2E shared readiness helper contract (#1668).

Pins that ``wait_for_e2e_stack`` is the SSOT for ordered E2E probes
(postgres → creative-agent → adcp_health), that both ``docker_services_e2e``
wait paths call it, and that ``wait_for_server_readiness`` delegates rather
than owning a second HTTP poll loop.

CI pre-start / ``compose up --wait`` contracts live in
``test_architecture_ci_suite_coverage.py`` (#1667) — this module pins the
**Python** helper contract only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions, iter_git_tracked_files, repo_root

_HELPER_REL = "tests/e2e/stack_readiness.py"
_CONFTEST_REL = "tests/e2e/conftest.py"
_UTILS_REL = "tests/e2e/utils.py"
_REQUIRED_ORDER = ("postgres", "creative-agent", "adcp_health")


def _tracked_rel_paths(repo: Path) -> set[str]:
    return {str(path.relative_to(repo)) for path in iter_git_tracked_files(repo)}


def _parse_tracked(repo: Path, rel: str) -> ast.Module:
    path = repo / rel
    assert path.is_file(), f"Expected tracked module missing on disk: {rel}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in iter_call_expressions(tree):
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found at module top level")


def _assign_tuple_strs(tree: ast.Module, name: str) -> tuple[str, ...]:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        value = node.value
        if isinstance(value, (ast.Tuple, ast.List)):
            out: list[str] = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.append(elt.value)
            return tuple(out)
    raise AssertionError(f"Constant sequence {name!r} not found")


def _http_poll_loop_in_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body still owns an HTTP health poll loop (dedupe breach)."""
    has_sleep = False
    has_health_get = False
    for node in iter_call_expressions(func):
        # time.sleep(...) / sleep(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "sleep":
            has_sleep = True
        if isinstance(node.func, ast.Name) and node.func.id == "sleep":
            has_sleep = True
        # client.get(.../health) or requests.get(.../health)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):
                    if any(
                        isinstance(v, ast.Constant) and isinstance(v.value, str) and "/health" in v.value
                        for v in arg.values
                    ):
                        has_health_get = True
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "/health" in arg.value:
                    has_health_get = True
                if isinstance(arg, ast.BinOp):  # url + "/health" style
                    has_health_get = True
    return has_sleep and has_health_get


def _wait_calls_in_docker_services(tree: ast.Module) -> list[ast.Call]:
    func = _function_def(tree, "docker_services_e2e")
    return list(iter_call_expressions(func, name="wait_for_e2e_stack"))


def _helper_references_log_dump_for_creative_agent(tree: ast.Module) -> bool:
    dump = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_dump_e2e_compose_logs":
            dump = node
            break
    assert dump is not None, "_dump_e2e_compose_logs must exist"
    # Prefer the service tuple constant when present.
    try:
        services = _assign_tuple_strs(tree, "_LOG_DUMP_SERVICES")
        if "creative-agent" in services:
            return True
    except AssertionError:
        pass
    source = ast.unparse(dump)
    return "creative-agent" in source and "logs" in source


@pytest.mark.arch_guard
class TestE2EStackReadinessHelperContract:
    """Pin the #1668 shared readiness helper contract (non-vacuous)."""

    def test_tracked_modules_exist(self):
        repo = repo_root()
        tracked = _tracked_rel_paths(repo)
        assert tracked, "git ls-files returned no files — scan would be vacuous"
        for rel in (_HELPER_REL, _CONFTEST_REL, _UTILS_REL):
            assert rel in tracked, f"{rel} must be git-tracked for the readiness contract guard"

    def test_helper_exports_wait_for_e2e_stack_and_probe_order(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _HELPER_REL)
        _function_def(tree, "wait_for_e2e_stack")
        order = _assign_tuple_strs(tree, "REQUIRED_E2E_PROBES")
        assert order == _REQUIRED_ORDER, (
            f"REQUIRED_E2E_PROBES must be {_REQUIRED_ORDER}, got {order} — "
            "creative-agent hard gate cannot silently drop"
        )

    def test_docker_services_e2e_calls_helper_on_both_wait_paths(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _CONFTEST_REL)
        calls = _wait_calls_in_docker_services(tree)
        assert len(calls) >= 2, (
            "docker_services_e2e must call wait_for_e2e_stack on both verify-only "
            f"and standalone wait paths (found {len(calls)} call(s))"
        )
        # No residual inline /health-only poll loops inside the fixture.
        func = _function_def(tree, "docker_services_e2e")
        assert not _http_poll_loop_in_function(func), (
            "docker_services_e2e must not keep an inline HTTP /health poll loop; use wait_for_e2e_stack only"
        )

    def test_wait_for_server_readiness_delegates_to_helper(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _UTILS_REL)
        func = _function_def(tree, "wait_for_server_readiness")
        assert "wait_for_e2e_stack" in _call_names(func), (
            "wait_for_server_readiness must call wait_for_e2e_stack (no second readiness oracle)"
        )
        assert not _http_poll_loop_in_function(func), (
            "wait_for_server_readiness must not own an HTTP /health poll loop body"
        )

    def test_failure_path_dumps_creative_agent_logs(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _HELPER_REL)
        assert _helper_references_log_dump_for_creative_agent(tree), (
            "readiness failure path must dump creative-agent compose logs"
        )
        wait_fn = _function_def(tree, "wait_for_e2e_stack")
        assert "_dump_e2e_compose_logs" in _call_names(wait_fn), (
            "wait_for_e2e_stack must call _dump_e2e_compose_logs on timeout"
        )


@pytest.mark.arch_guard
class TestE2EStackReadinessGuardSelfTest:
    """Mutation-style self-tests so a detector blind spot fails this module."""

    def test_probe_order_detector_rejects_missing_creative_agent(self):
        bad = ast.parse('REQUIRED_E2E_PROBES = ("postgres", "adcp_health")\n')
        order = _assign_tuple_strs(bad, "REQUIRED_E2E_PROBES")
        assert order != _REQUIRED_ORDER

    def test_inline_health_loop_detector_flags_known_bad(self):
        # Concatenate '/hea'+'lth' so this fixture source never contains a
        # literal "/health" token that live scanners could match in unit tests.
        src = (
            "import time\n"
            "import requests\n"
            "def docker_services_e2e():\n"
            "    for _ in range(30):\n"
            "        requests.get('http://localhost:8000' + '/hea' + 'lth')\n"
            "        time.sleep(2)\n"
        )
        func = _function_def(ast.parse(src), "docker_services_e2e")
        assert _http_poll_loop_in_function(func)

    def test_delegate_detector_rejects_poll_only_wrapper(self):
        src = (
            "import time\n"
            "import httpx\n"
            "def wait_for_server_readiness(mcp_url, timeout=60):\n"
            "    for _ in range(timeout):\n"
            "        with httpx.Client() as client:\n"
            "            client.get(mcp_url + '/hea' + 'lth')\n"
            "        time.sleep(1)\n"
        )
        func = _function_def(ast.parse(src), "wait_for_server_readiness")
        assert _http_poll_loop_in_function(func)
        assert "wait_for_e2e_stack" not in _call_names(func)
