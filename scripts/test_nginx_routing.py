#!/usr/bin/env python3
"""
Test nginx routing behavior against expected routing table.

This script validates that nginx routes requests correctly based on:
1. Domain type (main, tenant subdomain, external)
2. Request path
3. Headers (Apx-Incoming-Host, authentication)

Can run against:
- Production (requires deployment)
- Local docker-compose (simulates Approximated headers)

Usage:
    python scripts/test_nginx_routing.py --env production
    python scripts/test_nginx_routing.py --env local
    python scripts/test_nginx_routing.py --env production --verbose
"""

import argparse
import sys
from dataclasses import dataclass

import requests


@dataclass
class TestCase:
    """A single routing test case."""

    name: str
    domain: str  # The domain user visits
    path: str
    headers: dict
    expected_status: int
    expected_content: str | None = None  # Substring that should be in response
    expected_redirect: str | None = None
    description: str = ""
    via_approximated: bool = False  # Whether this request goes through Approximated


class NginxRoutingTester:
    """Test nginx routing behavior."""

    def __init__(self, base_url: str, verbose: bool = False):
        self.base_url = base_url
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.errors = []

    def simulate_approximated_request(self, domain: str, path: str, extra_headers: dict = None) -> dict:
        """Simulate how Approximated forwards requests to our nginx."""
        headers = {
            "Host": "sales-agent.scope3.com",  # Approximated always rewrites to this
            "Apx-Incoming-Host": domain,  # Original domain user visited
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def run_test(self, test: TestCase) -> bool:
        """Run a single test case."""
        print(f"\n{'='*80}")
        print(f"TEST: {test.name}")
        print(f"Domain: {test.domain}{test.path}")
        if test.description:
            print(f"Description: {test.description}")
        print(f"{'='*80}")

        # Set headers based on routing path
        if test.via_approximated:
            # External domain via Approximated: Host rewritten + Apx-Incoming-Host set
            headers = self.simulate_approximated_request(test.domain, test.path, test.headers)
        else:
            # Direct request: Host header matches actual domain
            headers = {"Host": test.domain}
            if test.headers:
                headers.update(test.headers)

        try:
            url = f"{self.base_url}{test.path}"
            if self.verbose:
                print(f"Request: {url}")
                print(f"Headers: {headers}")

            response = requests.get(url, headers=headers, allow_redirects=False, timeout=10)

            if self.verbose:
                print(f"Response Status: {response.status_code}")
                print(f"Response Headers: {dict(response.headers)}")

            # Check status code
            if response.status_code != test.expected_status:
                self._fail(
                    test,
                    f"Expected status {test.expected_status}, got {response.status_code}",
                    response,
                )
                return False

            # Check redirect
            if test.expected_redirect:
                location = response.headers.get("Location", "")
                if test.expected_redirect not in location:
                    self._fail(
                        test,
                        f"Expected redirect to contain '{test.expected_redirect}', got '{location}'",
                        response,
                    )
                    return False

            # Check content
            if test.expected_content:
                if test.expected_content not in response.text:
                    self._fail(
                        test,
                        f"Expected content to contain '{test.expected_content}'",
                        response,
                    )
                    return False

            self._pass(test)
            return True

        except requests.RequestException as e:
            self._error(test, str(e))
            return False

    def _pass(self, test: TestCase):
        """Mark test as passed."""
        self.passed += 1
        print(f"✅ PASS: {test.name}")

    def _fail(self, test: TestCase, reason: str, response: requests.Response):
        """Mark test as failed."""
        self.failed += 1
        error_msg = f"❌ FAIL: {test.name}\n   Reason: {reason}"
        if self.verbose:
            error_msg += f"\n   Response body: {response.text[:500]}"
        print(error_msg)
        self.errors.append(error_msg)

    def _error(self, test: TestCase, error: str):
        """Mark test as error."""
        self.failed += 1
        error_msg = f"⚠️  ERROR: {test.name}\n   Error: {error}"
        print(error_msg)
        self.errors.append(error_msg)

    def print_summary(self):
        """Print test summary."""
        print(f"\n{'='*80}")
        print("TEST SUMMARY")
        print(f"{'='*80}")
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")
        print(f"Total:  {self.passed + self.failed}")

        if self.errors:
            print(f"\n{'='*80}")
            print("FAILURES")
            print(f"{'='*80}")
            for error in self.errors:
                print(error)

        print(f"\n{'='*80}")
        if self.failed == 0:
            print("✅ ALL TESTS PASSED")
        else:
            print(f"❌ {self.failed} TESTS FAILED")
        print(f"{'='*80}")


def get_test_cases() -> list[TestCase]:
    """Define all test cases based on routing guide."""
    return [
        # ============================================================
        # MAIN DOMAIN: sales-agent.scope3.com
        # ============================================================
        TestCase(
            name="Main domain root → signup page",
            domain="sales-agent.scope3.com",
            path="/",
            headers={},
            expected_status=200,
            expected_content="Sign up",  # Should contain signup UI
            description="Main domain root should show signup page (not redirect)",
        ),
        TestCase(
            name="Main domain /signup → OAuth or signup form",
            domain="sales-agent.scope3.com",
            path="/signup",
            headers={},
            expected_status=200,
            expected_content=None,  # Could be OAuth redirect or form
            description="Signup endpoint should be accessible",
        ),
        TestCase(
            name="Main domain /login → login page",
            domain="sales-agent.scope3.com",
            path="/login",
            headers={},
            expected_status=200,
            expected_content="Login",
            description="Login page should be accessible",
        ),
        TestCase(
            name="Main domain /health → healthy",
            domain="sales-agent.scope3.com",
            path="/health",
            headers={},
            expected_status=200,
            expected_content="healthy",
            description="Health check should return 200",
        ),
        TestCase(
            name="Main domain /mcp/ → MCP server response",
            domain="sales-agent.scope3.com",
            path="/mcp/",
            headers={},
            expected_status=200,  # MCP server responds (will error without proper client headers)
            description="MCP endpoint exists but requires proper client (SSE headers)",
        ),
        TestCase(
            name="Main domain /a2a/ → A2A server response",
            domain="sales-agent.scope3.com",
            path="/a2a/",
            headers={},
            expected_status=200,  # A2A server responds (will error without proper JSON-RPC)
            description="A2A endpoint exists but requires proper client (JSON-RPC)",
        ),
        # ============================================================
        # TENANT SUBDOMAIN: <tenant>.sales-agent.scope3.com
        # ============================================================
        TestCase(
            name="Tenant subdomain root → landing page",
            domain="wonderstruck.sales-agent.scope3.com",
            path="/",
            headers={},
            expected_status=200,
            expected_content=None,  # Could vary by tenant
            description="Tenant subdomain root should show landing page",
        ),
        TestCase(
            name="Tenant subdomain /health → healthy",
            domain="wonderstruck.sales-agent.scope3.com",
            path="/health",
            headers={},
            expected_status=200,
            expected_content="healthy",
            description="Health check should work on tenant subdomain",
        ),
        TestCase(
            name="Tenant subdomain /mcp/ → MCP server response",
            domain="wonderstruck.sales-agent.scope3.com",
            path="/mcp/",
            headers={},
            expected_status=200,  # MCP server responds (requires SSE client)
            description="MCP endpoint accessible, requires proper SSE client headers",
        ),
        TestCase(
            name="Tenant subdomain /a2a/ → A2A server response",
            domain="wonderstruck.sales-agent.scope3.com",
            path="/a2a/",
            headers={},
            expected_status=200,  # A2A server responds (requires JSON-RPC)
            description="A2A endpoint accessible, requires proper JSON-RPC format",
        ),
        TestCase(
            name="Tenant subdomain /.well-known/agent.json → agent card",
            domain="wonderstruck.sales-agent.scope3.com",
            path="/.well-known/agent.json",
            headers={},
            expected_status=200,
            expected_content='"name"',  # Should contain JSON with name field
            description="Agent discovery endpoint should return agent card",
        ),
        # ============================================================
        # EXTERNAL DOMAIN: test-agent.adcontextprotocol.org
        # (Via Approximated - Host rewritten + Apx-Incoming-Host set)
        # ============================================================
        TestCase(
            name="External domain root → landing page",
            domain="test-agent.adcontextprotocol.org",
            path="/",
            headers={},
            expected_status=200,
            expected_content=None,  # Landing page content
            description="External domain should show tenant landing page",
            via_approximated=True,
        ),
        TestCase(
            name="External domain /mcp/ → MCP server response",
            domain="test-agent.adcontextprotocol.org",
            path="/mcp/",
            headers={},
            expected_status=200,  # MCP server responds (requires SSE client)
            description="MCP endpoint works via Approximated, requires proper SSE client",
            via_approximated=True,
        ),
        TestCase(
            name="External domain /a2a/ → A2A server response",
            domain="test-agent.adcontextprotocol.org",
            path="/a2a/",
            headers={},
            expected_status=200,  # A2A server responds (requires JSON-RPC)
            description="A2A endpoint works via Approximated, requires proper JSON-RPC",
            via_approximated=True,
        ),
        TestCase(
            name="External domain /.well-known/agent.json → agent card",
            domain="test-agent.adcontextprotocol.org",
            path="/.well-known/agent.json",
            headers={},
            expected_status=200,
            expected_content='"name"',  # Should contain JSON with name field
            description="Agent discovery works via Approximated",
            via_approximated=True,
        ),
        TestCase(
            name="External domain /admin/* → redirect to subdomain",
            domain="test-agent.adcontextprotocol.org",
            path="/admin/products",
            headers={},
            expected_status=302,
            expected_redirect=".sales-agent.scope3.com/admin/products",
            description="Admin UI redirects to tenant subdomain (OAuth compatibility)",
            via_approximated=True,
        ),
    ]


def main():
    parser = argparse.ArgumentParser(description="Test nginx routing behavior")
    parser.add_argument(
        "--env",
        choices=["production", "local"],
        default="production",
        help="Environment to test (default: production)",
    )
    parser.add_argument(
        "--base-url",
        help="Override base URL (e.g., http://localhost:8001)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output with request/response details",
    )
    parser.add_argument(
        "--filter",
        help="Run only tests whose name contains this string",
    )

    args = parser.parse_args()

    # Determine base URL
    if args.base_url:
        base_url = args.base_url
    elif args.env == "production":
        base_url = "https://sales-agent.scope3.com"
    else:  # local
        base_url = "http://localhost:8001"

    print(f"Testing nginx routing against: {base_url}")
    print(f"Environment: {args.env}")
    if args.filter:
        print(f"Filter: {args.filter}")
    print()

    # Get test cases
    test_cases = get_test_cases()

    # Filter if requested
    if args.filter:
        test_cases = [t for t in test_cases if args.filter.lower() in t.name.lower()]
        print(f"Running {len(test_cases)} filtered test(s)\n")

    # Run tests
    tester = NginxRoutingTester(base_url, verbose=args.verbose)
    for test in test_cases:
        tester.run_test(test)

    # Print summary
    tester.print_summary()

    # Exit with appropriate code
    sys.exit(0 if tester.failed == 0 else 1)


if __name__ == "__main__":
    main()
