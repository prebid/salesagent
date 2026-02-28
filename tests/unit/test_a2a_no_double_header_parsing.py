"""Test that A2A middleware doesn't duplicate header parsing from auth_context_middleware.

Task salesagent-z9sz: Both auth_context_middleware and a2a_auth_middleware parse the
same Authorization/x-adcp-auth headers. a2a_auth_middleware should read from
request.state.auth_context instead of re-parsing.
"""

import ast


class TestA2AMiddlewareNoDoubleHeaderParsing:
    """Verify a2a_auth_middleware reads from request.state, not re-parsing headers."""

    def test_a2a_middleware_does_not_reparse_headers(self):
        """a2a_auth_middleware should not iterate over request.headers to find tokens.

        The header parsing loop (for key, value in request.headers.items()) should
        only appear in auth_context_middleware, not in a2a_auth_middleware.
        """
        with open("src/app.py") as f:
            source = f.read()

        tree = ast.parse(source)

        # Find the a2a_auth_middleware function
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "a2a_auth_middleware":
                    func_node = node
                    break

        assert func_node is not None, "a2a_auth_middleware function not found"

        func_source = ast.get_source_segment(source, func_node)
        assert func_source is not None

        # The duplication pattern: iterating over request.headers to find auth tokens
        assert "request.headers.items()" not in func_source, (
            "a2a_auth_middleware re-parses request.headers instead of reading "
            "from request.state.auth_context (set by auth_context_middleware)"
        )

    def test_a2a_middleware_reads_from_auth_context(self):
        """a2a_auth_middleware should reference request.state.auth_context."""
        with open("src/app.py") as f:
            source = f.read()

        tree = ast.parse(source)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "a2a_auth_middleware":
                    func_node = node
                    break

        assert func_node is not None
        func_source = ast.get_source_segment(source, func_node)
        assert func_source is not None

        assert "auth_context" in func_source, (
            "a2a_auth_middleware does not reference auth_context — should read token from request.state.auth_context"
        )
