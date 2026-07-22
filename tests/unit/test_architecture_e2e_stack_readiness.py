"""Architecture guard: E2E shared readiness helper contract.

Pins that ``wait_for_e2e_stack`` is the SSOT for ordered E2E probes
(postgres → creative-agent → adcp_health), that both ``docker_services_e2e``
wait paths call it (verify-only ``if`` and standalone ``else``), that call
sites omit ``required=`` or pass the full ordered set, and that
``wait_for_server_readiness`` delegates rather than owning a second HTTP poll
loop.

CI pre-start / ``compose up --wait`` contracts live in
``test_architecture_ci_suite_coverage.py`` — this module pins the **Python**
helper contract only.
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
_COMPOSE_FILES = ("docker-compose.e2e.yml", "docker-compose.e2e.ports.yml")


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


def _calls_named_in(stmts: list[ast.stmt], name: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for stmt in stmts:
        out.extend(iter_call_expressions(stmt, name=name))
    return out


def _use_existing_if(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.If:
    """Return the top-level ``if use_existing_services`` split in ``docker_services_e2e``."""
    for stmt in func.body:
        if isinstance(stmt, ast.If):
            return stmt
    raise AssertionError("docker_services_e2e must have a top-level if/else for verify-only vs standalone")


def _wait_calls_by_branch(tree: ast.Module) -> tuple[list[ast.Call], list[ast.Call]]:
    func = _function_def(tree, "docker_services_e2e")
    split = _use_existing_if(func)
    then_calls = _calls_named_in(split.body, "wait_for_e2e_stack")
    else_calls = _calls_named_in(split.orelse, "wait_for_e2e_stack")
    return then_calls, else_calls


def _required_kw_uses_full_hard_gate(call: ast.Call) -> bool:
    """True when ``required=`` is omitted (default) or equals the full ordered set.

    Fail closed on ``**kwargs`` (``kw.arg is None``): the unpacked dict can narrow
    ``required`` without a static proof of the full hard gate.
    """
    for kw in call.keywords:
        if kw.arg is None:
            return False
        if kw.arg != "required":
            continue
        if isinstance(kw.value, ast.Name) and kw.value.id == "REQUIRED_E2E_PROBES":
            return True
        if isinstance(kw.value, (ast.Tuple, ast.List)):
            vals = tuple(
                elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
            return vals == _REQUIRED_ORDER
        return False
    return True


def _wait_default_required_is_full_hard_gate(tree: ast.Module) -> bool:
    """True when ``wait_for_e2e_stack``'s kw-default for ``required`` is ``REQUIRED_E2E_PROBES``."""
    func = _function_def(tree, "wait_for_e2e_stack")
    for arg, default in zip(func.args.kwonlyargs, func.args.kw_defaults, strict=True):
        if arg.arg != "required":
            continue
        return isinstance(default, ast.Name) and default.id == "REQUIRED_E2E_PROBES"
    return False


def _assert_calls_use_full_hard_gate(calls: list[ast.Call], *, where: str) -> None:
    assert calls, f"expected wait_for_e2e_stack call(s) in {where}"
    for call in calls:
        assert _required_kw_uses_full_hard_gate(call), (
            f"{where}: wait_for_e2e_stack must omit required= or pass {_REQUIRED_ORDER} "
            "(narrowing the hard gate silently drops creative-agent)"
        )


def _imports_name_from(tree: ast.Module, module: str, name: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == module:
            if any(alias.name == name for alias in node.names):
                return True
    return False


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
    """Pin the shared readiness helper contract (non-vacuous)."""

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
        assert _wait_default_required_is_full_hard_gate(tree), (
            "wait_for_e2e_stack default required= must be REQUIRED_E2E_PROBES "
            "(call sites omit required= and inherit the full hard gate)"
        )

    def test_compose_files_ssot_exported_and_imported(self):
        repo = repo_root()
        helper = _parse_tracked(repo, _HELPER_REL)
        conftest = _parse_tracked(repo, _CONFTEST_REL)
        utils = _parse_tracked(repo, _UTILS_REL)
        files = _assign_tuple_strs(helper, "DEFAULT_E2E_COMPOSE_FILES")
        assert files == _COMPOSE_FILES, f"DEFAULT_E2E_COMPOSE_FILES must be {_COMPOSE_FILES}, got {files}"
        assert _imports_name_from(conftest, "tests.e2e.stack_readiness", "DEFAULT_E2E_COMPOSE_FILES"), (
            "conftest must import DEFAULT_E2E_COMPOSE_FILES from stack_readiness (no duplicate tuple)"
        )
        assert _imports_name_from(utils, "tests.e2e.stack_readiness", "DEFAULT_E2E_COMPOSE_FILES"), (
            "utils must import DEFAULT_E2E_COMPOSE_FILES from stack_readiness"
        )
        # No private duplicate constant in conftest.
        with pytest.raises(AssertionError):
            _assign_tuple_strs(conftest, "_E2E_COMPOSE_FILES")

    def test_docker_services_e2e_calls_helper_on_both_wait_paths(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _CONFTEST_REL)
        then_calls, else_calls = _wait_calls_by_branch(tree)
        assert len(then_calls) >= 1, "docker_services_e2e verify-only branch (if body) must call wait_for_e2e_stack"
        assert len(else_calls) >= 1, "docker_services_e2e standalone branch (else) must call wait_for_e2e_stack"
        # No residual inline /health-only poll loops inside the fixture.
        func = _function_def(tree, "docker_services_e2e")
        assert not _http_poll_loop_in_function(func), (
            "docker_services_e2e must not keep an inline HTTP /health poll loop; use wait_for_e2e_stack only"
        )

    def test_call_sites_use_full_hard_gate(self):
        repo = repo_root()
        conftest = _parse_tracked(repo, _CONFTEST_REL)
        utils = _parse_tracked(repo, _UTILS_REL)
        then_calls, else_calls = _wait_calls_by_branch(conftest)
        _assert_calls_use_full_hard_gate(then_calls, where="docker_services_e2e verify-only")
        _assert_calls_use_full_hard_gate(else_calls, where="docker_services_e2e standalone")
        wrapper = _function_def(utils, "wait_for_server_readiness")
        wrapper_calls = list(iter_call_expressions(wrapper, name="wait_for_e2e_stack"))
        _assert_calls_use_full_hard_gate(wrapper_calls, where="wait_for_server_readiness")

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
        with pytest.raises(AssertionError):
            assert order == _REQUIRED_ORDER, (
                f"REQUIRED_E2E_PROBES must be {_REQUIRED_ORDER}, got {order} — "
                "creative-agent hard gate cannot silently drop"
            )

    def test_both_branches_detector_rejects_double_call_in_then_only(self):
        src = (
            "def docker_services_e2e(request):\n"
            "    if True:\n"
            "        wait_for_e2e_stack(ports={})\n"
            "        wait_for_e2e_stack(ports={})\n"
            "    else:\n"
            "        pass\n"
        )
        then_calls, else_calls = _wait_calls_by_branch(ast.parse(src))
        assert len(then_calls) == 2
        assert len(else_calls) == 0
        with pytest.raises(AssertionError):
            assert len(then_calls) >= 1 and len(else_calls) >= 1, (
                "docker_services_e2e standalone branch (else) must call wait_for_e2e_stack"
            )

    def test_hard_gate_detector_rejects_narrow_required_kw(self):
        src = 'wait_for_e2e_stack(ports={}, required=("adcp_health",))\n'
        call = next(iter_call_expressions(ast.parse(src), name="wait_for_e2e_stack"))
        assert not _required_kw_uses_full_hard_gate(call)
        with pytest.raises(AssertionError):
            _assert_calls_use_full_hard_gate([call], where="known-bad")

    def test_hard_gate_detector_rejects_kwargs_bypass(self):
        # **{"required": (...)} has kw.arg is None — must fail closed.
        src = 'wait_for_e2e_stack(ports={}, **{"required": ("adcp_health",)})\n'
        call = next(iter_call_expressions(ast.parse(src), name="wait_for_e2e_stack"))
        assert not _required_kw_uses_full_hard_gate(call)
        with pytest.raises(AssertionError):
            _assert_calls_use_full_hard_gate([call], where="kwargs-bypass")

    def test_default_required_detector_rejects_narrow_default(self):
        src = (
            "REQUIRED_E2E_PROBES = ('postgres', 'creative-agent', 'adcp_health')\n"
            "def wait_for_e2e_stack(*, ports, required=('adcp_health',)):\n"
            "    pass\n"
        )
        tree = ast.parse(src)
        assert not _wait_default_required_is_full_hard_gate(tree)
        with pytest.raises(AssertionError):
            assert _wait_default_required_is_full_hard_gate(tree), (
                "wait_for_e2e_stack default required= must be REQUIRED_E2E_PROBES"
            )

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
