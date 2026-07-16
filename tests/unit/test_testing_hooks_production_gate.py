"""P1 (#1544): proprietary X-* test headers must NOT be honored in production.

The pinned AdCP sandbox guidance
(dist/docs/3.1.0/media-buy/advanced-topics/sandbox.mdx) says sellers MUST NOT
alter behavior based on X-Dry-Run / X-Mock-Time. Those headers are internal tooling, so
``AdCPTestContext.from_headers`` returns None in production (ENVIRONMENT=production),
meaning no external MCP/A2A caller can activate dry-run against a live seller. Outside
production they are still honored for the test/dev tooling.
"""

from unittest.mock import patch

from src.core.testing_hooks import AdCPTestContext

_HEADERS = {"x-dry-run": "true", "x-force-error": "budget_exceeded", "x-simulated-spend": "true"}


def test_test_headers_ignored_in_production():
    """In production, from_headers ignores the proprietary test headers entirely."""
    with patch("src.core.config.is_production", return_value=True):
        assert AdCPTestContext.from_headers(_HEADERS) is None


def test_dry_run_honored_outside_production():
    """Outside production, X-Dry-Run still activates the internal dry-run tooling."""
    with patch("src.core.config.is_production", return_value=False):
        ctx = AdCPTestContext.from_headers(_HEADERS)
        assert ctx is not None
        assert ctx.dry_run is True


def test_force_error_honored_outside_production():
    """Sanity: another test header is also gated on the same environment check."""
    with patch("src.core.config.is_production", return_value=False):
        ctx = AdCPTestContext.from_headers(_HEADERS)
        assert ctx is not None
        assert ctx.force_error == "budget_exceeded"
