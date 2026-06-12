"""Guard: create_media_buy transport wrappers must accept the 'account' parameter.

The MCP and A2A wrappers for create_media_buy must expose 'account' in their
function signatures so callers can supply an account reference. Without it, the
harness and callers silently strip the field before dispatch, bypassing
enrich_identity_with_account and preventing ACCOUNT_NOT_FOUND from surfacing.

Regression for salesagent-l9wn.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIA_BUY_CREATE = REPO_ROOT / "src" / "core" / "tools" / "media_buy_create.py"

WRAPPER_NAMES = {"create_media_buy", "create_media_buy_raw"}


def _get_param_names(func_node: ast.AsyncFunctionDef | ast.FunctionDef) -> list[str]:
    args = func_node.args
    return [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]


class TestCreateWrapperAccountParam:
    """create_media_buy transport wrappers include 'account' in their signatures."""

    def _parse_wrappers(self) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
        tree = ast.parse(MEDIA_BUY_CREATE.read_text())
        return {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name in WRAPPER_NAMES
        }

    def test_create_media_buy_has_account_param(self):
        """MCP wrapper create_media_buy must accept 'account' parameter."""
        wrappers = self._parse_wrappers()
        assert "create_media_buy" in wrappers, "create_media_buy not found in media_buy_create.py"
        params = _get_param_names(wrappers["create_media_buy"])
        assert "account" in params, (
            f"create_media_buy is missing 'account' parameter. "
            f"Current params: {params}. "
            "Without it, harness strips account before MCP dispatch."
        )

    def test_create_media_buy_raw_has_account_param(self):
        """A2A wrapper create_media_buy_raw must accept 'account' parameter."""
        wrappers = self._parse_wrappers()
        assert "create_media_buy_raw" in wrappers, "create_media_buy_raw not found in media_buy_create.py"
        params = _get_param_names(wrappers["create_media_buy_raw"])
        assert "account" in params, (
            f"create_media_buy_raw is missing 'account' parameter. "
            f"Current params: {params}. "
            "Without it, harness strips account before A2A dispatch."
        )

    def test_guard_catches_missing_account_param(self):
        """Negative meta-test: guard catches a wrapper that omits 'account'."""
        source = "async def create_media_buy(brand=None, packages=None, ctx=None): pass"
        tree = ast.parse(source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
        params = _get_param_names(node)
        assert "account" not in params, "Negative meta-test setup: account should not be present"

    def test_guard_passes_when_account_present(self):
        """Positive meta-test: guard accepts a wrapper that includes 'account'."""
        source = "async def create_media_buy(brand=None, account=None, ctx=None): pass"
        tree = ast.parse(source)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
        params = _get_param_names(node)
        assert "account" in params, "Positive meta-test: account should be present"
