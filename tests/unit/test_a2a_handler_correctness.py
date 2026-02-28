"""Regression tests: A2A handler correctness issues.

Core invariant: A2A handler methods must use collision-free identifiers,
follow the SDK error protocol for unimplemented skills, and not leak
debug instrumentation into production logs.

beads: salesagent-yu73
"""

import pytest
from a2a.utils.errors import ServerError


class TestTaskIdCollisionFree:
    """task_id generation must be collision-free under concurrent access."""

    def _read_handler_source(self) -> str:
        import pathlib

        handler_path = pathlib.Path(__file__).parent.parent.parent / "src" / "a2a_server" / "adcp_a2a_server.py"
        return handler_path.read_text()

    def test_task_ids_are_unique(self):
        """task_id must not use len(self.tasks) — collision-prone under concurrency."""
        source = self._read_handler_source()

        for lineno, line in enumerate(source.splitlines(), 1):
            if "task_id" in line and "len(self.tasks)" in line:
                pytest.fail(f"Line {lineno}: task_id uses len(self.tasks) — must use uuid for collision safety")

    def test_task_id_contains_uuid_pattern(self):
        """task_id generation should reference uuid module."""
        source = self._read_handler_source()

        task_id_lines = [
            line for line in source.splitlines() if "task_id" in line and "=" in line and "uuid" in line.lower()
        ]
        assert task_id_lines, "task_id generation should use uuid module"


class TestNoDebugLogs:
    """Production code must not contain [DEBUG] log lines at INFO level."""

    def test_no_debug_prefix_in_info_logs(self):
        """No logger.info calls should contain [DEBUG] prefix."""
        import ast
        import pathlib

        handler_path = pathlib.Path(__file__).parent.parent.parent / "src" / "a2a_server" / "adcp_a2a_server.py"
        source = handler_path.read_text()
        tree = ast.parse(source)

        debug_info_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for logger.info(...) calls
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "info"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                ):
                    # Check if any string arg contains [DEBUG]
                    for arg in node.args:
                        if isinstance(arg, ast.JoinedStr):
                            # f-string — check values for [DEBUG]
                            for val in arg.values:
                                if isinstance(val, ast.Constant) and "[DEBUG]" in str(val.value):
                                    debug_info_lines.append(node.lineno)
                        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if "[DEBUG]" in arg.value:
                                debug_info_lines.append(node.lineno)

        assert not debug_info_lines, (
            f"Found logger.info('[DEBUG]...') at lines {debug_info_lines}. "
            "Debug logging must use logger.debug(), not logger.info()."
        )


class TestStubSkillsRaiseErrors:
    """Unimplemented skills must raise ServerError, not return success:False dicts."""

    @pytest.mark.asyncio
    async def test_approve_creative_raises_error(self):
        """approve_creative must raise ServerError(UnsupportedOperationError)."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity

        handler = AdCPRequestHandler()
        identity = ResolvedIdentity(principal_id="test", tenant_id="test", tenant={"tenant_id": "test"}, protocol="a2a")
        with pytest.raises(ServerError):
            await handler._handle_approve_creative_skill({}, identity)

    @pytest.mark.asyncio
    async def test_get_media_buy_status_raises_error(self):
        """get_media_buy_status must raise ServerError(UnsupportedOperationError)."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity

        handler = AdCPRequestHandler()
        identity = ResolvedIdentity(principal_id="test", tenant_id="test", tenant={"tenant_id": "test"}, protocol="a2a")
        with pytest.raises(ServerError):
            await handler._handle_get_media_buy_status_skill({}, identity)

    @pytest.mark.asyncio
    async def test_optimize_media_buy_raises_error(self):
        """optimize_media_buy must raise ServerError(UnsupportedOperationError)."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity

        handler = AdCPRequestHandler()
        identity = ResolvedIdentity(principal_id="test", tenant_id="test", tenant={"tenant_id": "test"}, protocol="a2a")
        with pytest.raises(ServerError):
            await handler._handle_optimize_media_buy_skill({}, identity)
