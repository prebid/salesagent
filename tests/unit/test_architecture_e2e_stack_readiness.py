"""Architecture guard: E2E shared readiness helper contract.

Pins that ``wait_for_e2e_stack`` is the SSOT for ordered E2E probes
(postgres → creative-agent → adcp_health), that both ``docker_services_e2e``
wait paths call it (via ``_export_and_wait``), that public callers cannot
narrow the hard gate, and that ``wait_for_server_readiness`` delegates to
``wait_for_e2e_stack``.

The inline HTTP poll-loop detector only flags ``.get(...)`` URL arguments that
literally embed ``/health`` (JoinedStr / Constant / BinOp *operands*). Variable-
hoisted URLs are out of scope; delegation is pinned via the call-name check.

CI pre-start / ``compose up --wait`` contracts live in
``test_architecture_ci_suite_coverage.py`` — this module pins the **Python**
helper contract only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.e2e.stack_readiness import REQUIRED_E2E_PROBES
from tests.unit._architecture_helpers import (
    assign_tuple_strs,
    call_names,
    function_def,
    imports_name_from,
    iter_call_expressions,
    iter_git_tracked_files,
    parse_module,
    rel,
    repo_root,
)

_HELPER_REL = "tests/e2e/stack_readiness.py"
_CONFTEST_REL = "tests/e2e/conftest.py"
_UTILS_REL = "tests/e2e/utils.py"
_REQUIRED_ORDER = ("postgres", "creative-agent", "adcp_health")
_COMPOSE_FILES = ("docker-compose.e2e.yml", "docker-compose.e2e.ports.yml")
_FLOOR_RELS = frozenset({_HELPER_REL, _CONFTEST_REL, _UTILS_REL})


def _tracked_rel_paths(repo: Path) -> set[str]:
    return {str(path.relative_to(repo)) for path in iter_git_tracked_files(repo)}


def _parse_tracked(repo: Path, file_path: str) -> ast.Module:
    path = repo / file_path
    assert path.is_file(), f"Expected tracked module missing on disk: {file_path}"
    assert rel(path) == file_path, f"Expected repo-relative path {file_path!r}, got {rel(path)!r}"
    return parse_module(path)


def _e2e_py_rels(repo: Path) -> list[str]:
    """Git-tracked ``tests/e2e/**/*.py`` paths (repo-relative), sorted."""
    out: list[str] = []
    for path in iter_git_tracked_files(repo):
        try:
            file_rel = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        if file_rel.startswith("tests/e2e/") and file_rel.endswith(".py"):
            out.append(file_rel)
    return sorted(out)


def _binop_operand_contains_health(node: ast.AST) -> bool:
    """True when a BinOp tree has ``/health`` in a string Constant operand."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/health" in node.value:
        return True
    if isinstance(node, ast.BinOp):
        return _binop_operand_contains_health(node.left) or _binop_operand_contains_health(node.right)
    return False


def _http_poll_loop_in_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body still owns an HTTP health poll loop (dedupe breach).

    Detects sleep + ``.get(...)`` whose URL arg literally embeds ``/health``
    (f-string JoinedStr, string Constant, or BinOp with ``/health`` in an
    operand). Does not follow variable-hoisted URLs.
    """
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
                if isinstance(arg, ast.BinOp) and _binop_operand_contains_health(arg):
                    has_health_get = True
    return has_sleep and has_health_get


def _string_constant_present(tree: ast.Module, value: str) -> bool:
    """True when any string ``Constant`` node in ``tree`` equals ``value``."""
    return any(isinstance(node, ast.Constant) and node.value == value for node in ast.walk(tree))


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
    """Return ``_export_and_wait`` calls in verify-only (then) and standalone (else)."""
    func = function_def(tree, "docker_services_e2e")
    split = _use_existing_if(func)
    then_calls = _calls_named_in(split.body, "_export_and_wait")
    else_calls = _calls_named_in(split.orelse, "_export_and_wait")
    return then_calls, else_calls


def _call_passes_required_kw(call: ast.Call) -> bool:
    """True when a ``wait_for_e2e_stack`` / ``_wait_for_probes`` call passes ``required=``."""
    return any(kw.arg == "required" for kw in call.keywords)


def _compose_argv_star_followed_by_verb(node: ast.AST, verb: str) -> bool:
    """True for ``[*compose_argv(...), "<verb>", ...]`` list/tuple displays.

    Catches re-inlined ``exec`` / ``down`` tails that bypass ``compose_exec_argv`` /
    ``compose_down_argv``.
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    elts = node.elts
    if len(elts) < 2:
        return False
    first = elts[0]
    if not isinstance(first, ast.Starred):
        return False
    starred = first.value
    if not isinstance(starred, ast.Call):
        return False
    func = starred.func
    if isinstance(func, ast.Name) and func.id != "compose_argv":
        return False
    if isinstance(func, ast.Attribute) and func.attr != "compose_argv":
        return False
    if not (isinstance(func, (ast.Name, ast.Attribute))):
        return False
    second = elts[1]
    return isinstance(second, ast.Constant) and second.value == verb


def _has_reinlined_compose_verb(tree: ast.Module, verb: str) -> bool:
    return any(_compose_argv_star_followed_by_verb(node, verb) for node in ast.walk(tree))


# Shared predicates — production tests and self-tests call the same checkers.
def _assert_required_probe_order(order: tuple[str, ...]) -> None:
    assert order == _REQUIRED_ORDER, (
        f"REQUIRED_E2E_PROBES must be {_REQUIRED_ORDER}, got {order} — creative-agent hard gate cannot silently drop"
    )


def _assert_both_wait_branches(then_calls: list[ast.Call], else_calls: list[ast.Call]) -> None:
    assert len(then_calls) >= 1, "docker_services_e2e verify-only branch (if body) must call _export_and_wait"
    assert len(else_calls) >= 1, "docker_services_e2e standalone branch (else) must call _export_and_wait"


def _assert_no_inline_health_poll(func: ast.FunctionDef | ast.AsyncFunctionDef, *, where: str) -> None:
    assert not _http_poll_loop_in_function(func), (
        f"{where} must not keep an inline HTTP /health poll loop; use wait_for_e2e_stack only"
    )


def _helper_references_log_dump_for_creative_agent(tree: ast.Module) -> bool:
    dump = function_def(tree, "_dump_e2e_compose_logs")
    # Prefer the service tuple constant when present.
    try:
        services = assign_tuple_strs(tree, "_LOG_DUMP_SERVICES")
        if "creative-agent" in services:
            return True
    except AssertionError:
        pass
    source = ast.unparse(dump)
    return "creative-agent" in source and "logs" in source


def _assert_no_public_required_kw(tree: ast.Module, *, where: str) -> None:
    """Public ``wait_for_e2e_stack`` must not accept ``required=``."""
    func = function_def(tree, "wait_for_e2e_stack")
    arg_names = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
    assert "required" not in arg_names, (
        f"{where}: wait_for_e2e_stack must not expose public required= (always REQUIRED_E2E_PROBES)"
    )


@pytest.mark.arch_guard
class TestE2EStackReadinessHelperContract:
    """Pin the shared readiness helper contract (non-vacuous)."""

    def test_tracked_modules_exist(self):
        repo = repo_root()
        tracked = _tracked_rel_paths(repo)
        assert tracked, "git ls-files returned no files — scan would be vacuous"
        for file_rel in _FLOOR_RELS:
            assert file_rel in tracked, f"{file_rel} must be git-tracked for the readiness contract guard"

    def test_helper_exports_wait_for_e2e_stack_and_probe_order(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _HELPER_REL)
        function_def(tree, "wait_for_e2e_stack")
        function_def(tree, "_wait_for_probes")
        order = assign_tuple_strs(tree, "REQUIRED_E2E_PROBES")
        _assert_required_probe_order(order)
        _assert_no_public_required_kw(tree, where=_HELPER_REL)
        wait_fn = function_def(tree, "wait_for_e2e_stack")
        assert "REQUIRED_E2E_PROBES" in ast.unparse(wait_fn), (
            "wait_for_e2e_stack must always pass REQUIRED_E2E_PROBES into _wait_for_probes"
        )

    def test_required_probe_order_matches_runtime(self):
        # Probe-registry coverage is asserted once at module import in stack_readiness
        # (single home). This guard only pins the ordered tuple identity.
        assert tuple(REQUIRED_E2E_PROBES) == _REQUIRED_ORDER

    def test_compose_files_ssot_exported_and_imported(self):
        repo = repo_root()
        helper = _parse_tracked(repo, _HELPER_REL)
        conftest = _parse_tracked(repo, _CONFTEST_REL)
        utils = _parse_tracked(repo, _UTILS_REL)
        files = assign_tuple_strs(helper, "DEFAULT_E2E_COMPOSE_FILES")
        assert files == _COMPOSE_FILES, f"DEFAULT_E2E_COMPOSE_FILES must be {_COMPOSE_FILES}, got {files}"
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "DEFAULT_E2E_COMPOSE_FILES"), (
            "conftest must import DEFAULT_E2E_COMPOSE_FILES from stack_readiness (no duplicate tuple)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "compose_argv"), (
            "conftest must import public compose_argv (not underscore-private)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "compose_available"), (
            "conftest must import public compose_available (not underscore-private)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "use_container_exec"), (
            "conftest must import use_container_exec (topology seam for seed exec)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "e2e_ports"), (
            "conftest must import e2e_ports (ports constructor SSOT)"
        )
        assert imports_name_from(utils, "tests.e2e.stack_readiness", "DEFAULT_E2E_COMPOSE_FILES"), (
            "utils must import DEFAULT_E2E_COMPOSE_FILES from stack_readiness"
        )
        assert imports_name_from(utils, "tests.e2e.stack_readiness", "use_container_exec"), (
            "utils must import use_container_exec (topology seam for media_buy update)"
        )
        assert imports_name_from(utils, "tests.e2e.stack_readiness", "e2e_ports"), (
            "utils must import e2e_ports (ports constructor SSOT)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "e2e_host_default"), (
            "conftest must import e2e_host_default from stack_readiness (host SSOT)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "e2e_db_url_default"), (
            "conftest must import e2e_db_url_default from stack_readiness (DB URL SSOT)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "e2e_db_url_build"), (
            "conftest must import e2e_db_url_build for setdefault seed DSN (no second literal)"
        )
        function_def(helper, "e2e_host_default")
        function_def(helper, "resolve_e2e_db_endpoint")
        function_def(helper, "e2e_db_url_default")
        function_def(helper, "e2e_db_url_build")
        function_def(helper, "_e2e_db_env_url")
        function_def(helper, "in_network")
        function_def(helper, "use_container_exec")
        function_def(helper, "e2e_ports")
        function_def(helper, "health_of")
        endpoint_fn = function_def(helper, "resolve_e2e_db_endpoint")
        url_fn = function_def(helper, "e2e_db_url_default")
        assert "_e2e_db_env_url" in call_names(endpoint_fn), (
            "resolve_e2e_db_endpoint must call _e2e_db_env_url (DB env-chain SSOT)"
        )
        assert "_e2e_db_env_url" in call_names(url_fn), (
            "e2e_db_url_default must call _e2e_db_env_url (DB env-chain SSOT)"
        )
        # No private duplicate constant in conftest.
        with pytest.raises(AssertionError):
            assign_tuple_strs(conftest, "_E2E_COMPOSE_FILES")

    def test_compose_exec_and_down_argv_ssot(self):
        """compose_exec_argv/compose_down_argv own the exec/down verb+service+gate unit.

        Guards the Should-fix regression where ``force_approve`` and the two
        ``down -v`` teardown sites each hand-rolled ``"exec", "-T", "adcp-server"``
        / ``"down", "-v"`` on top of ``compose_argv`` instead of sharing one seam.
        """
        repo = repo_root()
        helper = _parse_tracked(repo, _HELPER_REL)
        conftest = _parse_tracked(repo, _CONFTEST_REL)
        utils = _parse_tracked(repo, _UTILS_REL)
        function_def(helper, "compose_exec_argv")
        function_def(helper, "compose_down_argv")

        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "compose_exec_argv"), (
            f"{_CONFTEST_REL} must import compose_exec_argv from stack_readiness (exec SSOT)"
        )
        assert imports_name_from(conftest, "tests.e2e.stack_readiness", "compose_down_argv"), (
            f"{_CONFTEST_REL} must import compose_down_argv from stack_readiness (down SSOT)"
        )
        assert imports_name_from(utils, "tests.e2e.stack_readiness", "compose_exec_argv"), (
            f"{_UTILS_REL} must import compose_exec_argv from stack_readiness (exec SSOT)"
        )

        for tree, where in ((conftest, _CONFTEST_REL), (utils, _UTILS_REL)):
            assert not _string_constant_present(tree, "-T"), (
                f'{where} must route compose exec through compose_exec_argv (SSOT), not re-inline the "-T" exec flag'
            )
            assert not _has_reinlined_compose_verb(tree, "exec"), (
                f"{where} must not re-inline [*compose_argv(...), 'exec', ...] — use compose_exec_argv"
            )
            assert not _has_reinlined_compose_verb(tree, "down"), (
                f"{where} must not re-inline [*compose_argv(...), 'down', ...] — use compose_down_argv"
            )

    def test_docker_services_e2e_calls_helper_on_both_wait_paths(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _CONFTEST_REL)
        then_calls, else_calls = _wait_calls_by_branch(tree)
        _assert_both_wait_branches(then_calls, else_calls)
        export_fn = function_def(tree, "_export_and_wait")
        assert "wait_for_e2e_stack" in call_names(export_fn), "_export_and_wait must delegate to wait_for_e2e_stack"
        # No residual inline /health-only poll loops inside the fixture.
        func = function_def(tree, "docker_services_e2e")
        _assert_no_inline_health_poll(func, where="docker_services_e2e")

    def test_e2e_tree_has_no_divergent_wait_or_public_required(self):
        """Scan all tracked tests/e2e/**/*.py — floor of three SSOT modules, then full tree."""
        repo = repo_root()
        e2e_rels = _e2e_py_rels(repo)
        assert _FLOOR_RELS <= set(e2e_rels), (
            f"e2e scan floor {_FLOOR_RELS} must be present in tracked tests/e2e/**/*.py, got {e2e_rels[:20]}..."
        )
        assert len(e2e_rels) >= 3, "e2e scan would be vacuous with fewer than three tracked modules"

        for file_rel in e2e_rels:
            tree = _parse_tracked(repo, file_rel)
            for call in iter_call_expressions(tree, name="wait_for_e2e_stack"):
                assert not _call_passes_required_kw(call), (
                    f"{file_rel}: wait_for_e2e_stack must not pass required= (hard gate is always REQUIRED_E2E_PROBES)"
                )
            # Divergent inline /health poll loops are forbidden outside the helper's
            # own probe implementations (those use httpx without sleep+get shape).
            if file_rel == _HELPER_REL:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _assert_no_inline_health_poll(node, where=f"{file_rel}:{node.name}")

    def test_wait_for_server_readiness_delegates_to_helper(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _UTILS_REL)
        func = function_def(tree, "wait_for_server_readiness")
        assert "wait_for_e2e_stack" in call_names(func), (
            "wait_for_server_readiness must call wait_for_e2e_stack (delegation, not a second oracle)"
        )
        assert "e2e_ports" in call_names(func), "wait_for_server_readiness must build ports via e2e_ports"
        arg_names = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
        assert "postgres_port" in arg_names, (
            "wait_for_server_readiness must take explicit postgres_port (no POSTGRES_PORT env side-channel)"
        )
        _assert_no_inline_health_poll(func, where="wait_for_server_readiness")

    def test_failure_path_dumps_creative_agent_logs(self):
        repo = repo_root()
        tree = _parse_tracked(repo, _HELPER_REL)
        assert _helper_references_log_dump_for_creative_agent(tree), (
            "readiness failure path must dump creative-agent compose logs"
        )
        wait_fn = function_def(tree, "_wait_for_probes")
        assert "_dump_e2e_compose_logs" in call_names(wait_fn), (
            "_wait_for_probes must call _dump_e2e_compose_logs on timeout"
        )


@pytest.mark.arch_guard
class TestE2EStackReadinessGuardSelfTest:
    """Mutation-style self-tests so a detector blind spot fails this module."""

    def test_probe_order_detector_rejects_missing_creative_agent(self):
        bad = ast.parse('REQUIRED_E2E_PROBES = ("postgres", "adcp_health")\n')
        order = assign_tuple_strs(bad, "REQUIRED_E2E_PROBES")
        with pytest.raises(AssertionError):
            _assert_required_probe_order(order)

    def test_both_branches_detector_rejects_double_call_in_then_only(self):
        src = (
            "def docker_services_e2e(request):\n"
            "    if True:\n"
            "        _export_and_wait(ports={}, timeout_s=60)\n"
            "        _export_and_wait(ports={}, timeout_s=60)\n"
            "    else:\n"
            "        pass\n"
        )
        then_calls, else_calls = _wait_calls_by_branch(ast.parse(src))
        assert len(then_calls) == 2
        assert len(else_calls) == 0
        with pytest.raises(AssertionError):
            _assert_both_wait_branches(then_calls, else_calls)

    def test_public_required_detector_rejects_kwonly_required(self):
        src = (
            "REQUIRED_E2E_PROBES = ('postgres', 'creative-agent', 'adcp_health')\n"
            "def wait_for_e2e_stack(*, ports, required=REQUIRED_E2E_PROBES):\n"
            "    pass\n"
        )
        with pytest.raises(AssertionError):
            _assert_no_public_required_kw(ast.parse(src), where="known-bad")

    def test_required_kw_call_detector_flags_required_pass(self):
        src = 'wait_for_e2e_stack(ports={}, required=("adcp_health",))\n'
        call = next(iter_call_expressions(ast.parse(src), name="wait_for_e2e_stack"))
        assert _call_passes_required_kw(call)

    def test_required_kw_call_detector_accepts_omitted(self):
        src = "wait_for_e2e_stack(ports={})\n"
        call = next(iter_call_expressions(ast.parse(src), name="wait_for_e2e_stack"))
        assert not _call_passes_required_kw(call)

    def test_inline_health_loop_detector_flags_binop_with_health(self):
        src = (
            "import time\n"
            "import requests\n"
            "def docker_services_e2e():\n"
            "    for _ in range(30):\n"
            "        requests.get('http://localhost:8000' + '/health')\n"
            "        time.sleep(2)\n"
        )
        func = function_def(ast.parse(src), "docker_services_e2e")
        assert _http_poll_loop_in_function(func)

    def test_inline_health_loop_detector_flags_fstring_health(self):
        src = (
            "import time\n"
            "import requests\n"
            "def docker_services_e2e():\n"
            "    u = 'http://localhost:8000'\n"
            "    for _ in range(30):\n"
            '        requests.get(f"{u}/health")\n'
            "        time.sleep(2)\n"
        )
        func = function_def(ast.parse(src), "docker_services_e2e")
        assert _http_poll_loop_in_function(func)

    def test_inline_health_loop_detector_flags_constant_health(self):
        src = (
            "import time\n"
            "import requests\n"
            "def docker_services_e2e():\n"
            "    for _ in range(30):\n"
            "        requests.get('http://localhost:8000/health')\n"
            "        time.sleep(2)\n"
        )
        func = function_def(ast.parse(src), "docker_services_e2e")
        assert _http_poll_loop_in_function(func)

    def test_inline_health_loop_detector_flags_bare_name_sleep(self):
        # Bare ``sleep(...)`` from ``from time import sleep`` — Name arm twin of Attribute.
        src = (
            "from time import sleep\n"
            "import requests\n"
            "def docker_services_e2e():\n"
            "    for _ in range(30):\n"
            "        requests.get('http://localhost:8000/health')\n"
            "        sleep(2)\n"
        )
        func = function_def(ast.parse(src), "docker_services_e2e")
        assert _http_poll_loop_in_function(func)

    def test_inline_health_loop_detector_ignores_binop_without_health(self):
        # sleep + .get(BinOp) must not flag when no operand contains /health.
        src = (
            "import time\n"
            "def docker_services_e2e():\n"
            "    a, b = 'x', 'y'\n"
            "    for _ in range(30):\n"
            "        d.get(a + b)\n"
            "        time.sleep(2)\n"
        )
        func = function_def(ast.parse(src), "docker_services_e2e")
        assert not _http_poll_loop_in_function(func)

    def test_inline_exec_flag_detector_flags_reinlined_dash_t(self):
        src = 'cmd = [*compose_argv(files), "exec", "-T", "adcp-server", "python"]\n'
        tree = ast.parse(src)
        assert _string_constant_present(tree, "-T")
        assert _has_reinlined_compose_verb(tree, "exec")

    def test_inline_exec_flag_detector_ignores_seam_only_module(self):
        src = 'cmd = compose_exec_argv("adcp-server", "python")\n'
        tree = ast.parse(src)
        assert not _string_constant_present(tree, "-T")
        assert not _has_reinlined_compose_verb(tree, "exec")

    def test_inline_down_detector_flags_reinlined_down_v(self):
        src = 'cmd = [*compose_argv((BASE_E2E_COMPOSE_FILE,)), "down", "-v"]\n'
        tree = ast.parse(src)
        assert _has_reinlined_compose_verb(tree, "down")

    def test_inline_down_detector_ignores_seam_only_module(self):
        src = "cmd = compose_down_argv()\n"
        assert not _has_reinlined_compose_verb(ast.parse(src), "down")

    def test_delegate_detector_rejects_poll_only_wrapper(self):
        src = (
            "import time\n"
            "import httpx\n"
            "def wait_for_server_readiness(mcp_url, timeout=60):\n"
            "    for _ in range(timeout):\n"
            "        with httpx.Client() as client:\n"
            "            client.get(mcp_url + '/health')\n"
            "        time.sleep(1)\n"
        )
        func = function_def(ast.parse(src), "wait_for_server_readiness")
        assert _http_poll_loop_in_function(func)
        assert "wait_for_e2e_stack" not in call_names(func)
        with pytest.raises(AssertionError):
            _assert_no_inline_health_poll(func, where="known-bad-wrapper")
