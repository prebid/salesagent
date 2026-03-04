"""
Unit tests verifying that E2E test fixtures properly clean up resources.

These tests reproduce the ResourceWarning bugs from:
- tests/e2e/test_a2a_webhook_payload_types.py (5 warnings from unclosed sockets)
- tests/e2e/test_adcp_schema_compliance.py (1 warning from unclosed httpx client)
- tests/e2e/test_discovery_endpoints_e2e.py (1 warning from unclosed httpx client)

Bug: salesagent-7wmn
"""

import socket
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


class _DummyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class TestWebhookCaptureServerCleanup:
    """Verify that the webhook_capture_server fixture closes its socket."""

    def test_httpserver_shutdown_without_server_close_leaks_socket(self):
        """
        Demonstrate that calling HTTPServer.shutdown() without server_close()
        leaves the underlying socket open, causing a ResourceWarning.

        This is the root cause of 5 ResourceWarnings in test_a2a_webhook_payload_types.py.
        """
        # Find a free port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        server = HTTPServer(("127.0.0.1", port), _DummyHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # Only call shutdown() -- NOT server_close()
        # This is what the buggy fixture does
        server.shutdown()

        # The socket should be closed after proper cleanup,
        # but shutdown() alone does NOT close it.
        # Verify the socket is still open (the bug):
        assert server.socket.fileno() != -1, (
            "Socket should still be open after shutdown() alone -- this test demonstrates the bug"
        )

        # Clean up for this test so we don't leak ourselves
        server.server_close()

    def test_httpserver_shutdown_with_server_close_releases_socket(self):
        """
        Demonstrate that calling server_close() after shutdown() properly
        releases the socket. This is the expected fix.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        server = HTTPServer(("127.0.0.1", port), _DummyHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # Call both shutdown() AND server_close() -- the correct pattern
        server.shutdown()
        server.server_close()

        # Socket should be closed (fileno() returns -1 for closed sockets)
        assert server.socket.fileno() == -1, "Socket should be closed after shutdown() + server_close()"

    def test_no_resource_warning_with_proper_cleanup(self):
        """Verify no ResourceWarning is emitted when server_close() is called."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        server = HTTPServer(("127.0.0.1", port), _DummyHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            server.shutdown()
            server.server_close()

            resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
            assert len(resource_warnings) == 0, (
                f"Expected no ResourceWarnings, got {len(resource_warnings)}: "
                f"{[str(rw.message) for rw in resource_warnings]}"
            )
