#!/usr/bin/env python3
"""
Production Agent Testing Script

Tests all production agents after deploying changes.
Tests both protocol endpoints (MCP/A2A) and web pages (landing/login).

Usage:
    python scripts/test_production_landing_pages.py
    python scripts/test_production_landing_pages.py --verbose
    python scripts/test_production_landing_pages.py --agent accuweather
"""

import argparse
import json
import subprocess
import sys
from typing import Any

import requests
from rich.console import Console
from rich.table import Table

console = Console()

# Production agents to test
PRODUCTION_AGENTS = {
    "accuweather": {
        "url": "https://sales-agent.accuweather.com",
        "type": "custom domain",
        "test_mcp": True,
        "test_a2a": True,
        "expect_landing": True,  # Should show landing page at root
        "expect_login": False,
    },
    "applabs": {
        "url": "https://applabs.sales-agent.scope3.com",
        "type": "subdomain",
        "test_mcp": True,
        "test_a2a": True,
        "expect_landing": True,  # Shows "Pending Configuration" page
        "expect_login": False,
        "pending_config": True,
    },
    "test-agent": {
        "url": "https://test-agent.adcontextprotocol.org",
        "type": "adcontextprotocol domain",
        "test_mcp": True,
        "test_a2a": True,
        "expect_landing": True,  # Shows agent landing page (custom domain with tenant)
        "expect_login": False,
    },
    "admin": {
        "url": "https://admin.sales-agent.scope3.com",
        "type": "admin UI",
        "test_mcp": False,
        "test_a2a": False,
        "expect_landing": False,
        "expect_login": True,  # Should redirect to login
    },
}


class TestResult:
    """Test result with status and details."""

    def __init__(self, passed: bool, message: str, details: dict[str, Any] | None = None):
        self.passed = passed
        self.message = message
        self.details = details or {}


def test_mcp_endpoint(agent_name: str, url: str, verbose: bool = False) -> TestResult:
    """Test MCP endpoint using npx @adcp/client."""
    try:
        # Use npx -y to skip install prompt
        # Timeout is 60s to allow for package download on first run
        cmd = ["npx", "-y", "@adcp/client@latest", url, "--protocol", "mcp", "--json"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if verbose:
            console.print(f"[dim]MCP command: {' '.join(cmd)}[/dim]")
            console.print(f"[dim]Return code: {result.returncode}[/dim]")
            if result.stdout:
                console.print(f"[dim]stdout: {result.stdout[:200]}[/dim]")
            if result.stderr:
                console.print(f"[dim]stderr: {result.stderr[:200]}[/dim]")

        # Check if it succeeded (lists tools or returns valid response)
        if result.returncode == 0:
            # Try to parse JSON response
            try:
                response = json.loads(result.stdout) if result.stdout else {}
                if isinstance(response, dict) and ("tools" in response or "error" not in response):
                    return TestResult(True, "MCP endpoint accessible", {"url": url})
            except json.JSONDecodeError:
                pass

            # Non-JSON but success
            if "error" not in result.stdout.lower():
                return TestResult(True, "MCP endpoint accessible", {"url": url})

        # Check stderr for specific errors
        stderr_lower = result.stderr.lower()
        if "authentication" in stderr_lower or "unauthorized" in stderr_lower:
            return TestResult(False, "MCP endpoint requires auth (expected for some agents)", {"url": url})

        if "econnrefused" in stderr_lower or "enotfound" in stderr_lower:
            return TestResult(False, "MCP endpoint not reachable", {"url": url, "error": result.stderr[:100]})

        return TestResult(
            False, f"MCP endpoint error (code {result.returncode})", {"url": url, "error": result.stderr[:100]}
        )

    except subprocess.TimeoutExpired:
        return TestResult(False, "MCP test timed out", {"url": url})
    except FileNotFoundError:
        return TestResult(False, "npx @adcp/client not found (install Node.js)", {"url": url})
    except Exception as e:
        return TestResult(False, f"MCP test failed: {e}", {"url": url})


def test_a2a_endpoint(agent_name: str, url: str, verbose: bool = False) -> TestResult:
    """Test A2A endpoint using npx @adcp/client."""
    try:
        # Use npx -y to skip install prompt
        # Timeout is 60s to allow for package download on first run
        cmd = ["npx", "-y", "@adcp/client@latest", url, "--protocol", "a2a", "--json"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if verbose:
            console.print(f"[dim]A2A command: {' '.join(cmd)}[/dim]")
            console.print(f"[dim]Return code: {result.returncode}[/dim]")
            if result.stdout:
                console.print(f"[dim]stdout: {result.stdout[:200]}[/dim]")
            if result.stderr:
                console.print(f"[dim]stderr: {result.stderr[:200]}[/dim]")

        # Check if it succeeded
        if result.returncode == 0:
            try:
                response = json.loads(result.stdout) if result.stdout else {}
                if isinstance(response, dict) and ("capabilities" in response or "error" not in response):
                    return TestResult(True, "A2A endpoint accessible", {"url": url})
            except json.JSONDecodeError:
                pass

            if "error" not in result.stdout.lower():
                return TestResult(True, "A2A endpoint accessible", {"url": url})

        # Check stderr for specific errors
        stderr_lower = result.stderr.lower()
        if "authentication" in stderr_lower or "unauthorized" in stderr_lower:
            return TestResult(False, "A2A endpoint requires auth (expected for some agents)", {"url": url})

        if "econnrefused" in stderr_lower or "enotfound" in stderr_lower:
            return TestResult(False, "A2A endpoint not reachable", {"url": url, "error": result.stderr[:100]})

        return TestResult(
            False, f"A2A endpoint error (code {result.returncode})", {"url": url, "error": result.stderr[:100]}
        )

    except subprocess.TimeoutExpired:
        return TestResult(False, "A2A test timed out", {"url": url})
    except FileNotFoundError:
        return TestResult(False, "npx @adcp/client not found (install Node.js)", {"url": url})
    except Exception as e:
        return TestResult(False, f"A2A test failed: {e}", {"url": url})


def test_landing_page(agent_name: str, config: dict[str, Any], verbose: bool = False) -> TestResult:
    """Test that landing page or login redirect works."""
    url = config["url"]

    try:
        # For login checks, don't follow redirects
        if config.get("expect_login"):
            response = requests.get(url, timeout=10, allow_redirects=False)

            if verbose:
                console.print(f"[dim]GET {url}[/dim]")
                console.print(f"[dim]Status: {response.status_code}[/dim]")

            if response.status_code in [301, 302, 303, 307, 308]:
                location = response.headers.get("Location", "")
                if "login" in location.lower():
                    return TestResult(True, "Redirects to login (expected)", {"url": url})
                return TestResult(False, f"Redirects to {location}, expected login", {"url": url})
            return TestResult(False, f"Expected redirect to login, got {response.status_code}", {"url": url})

        # For landing page checks, follow redirects
        if config.get("expect_landing"):
            response = requests.get(url, timeout=10, allow_redirects=True)

            if verbose:
                console.print(f"[dim]GET {url}[/dim]")
                console.print(f"[dim]Status: {response.status_code}[/dim]")
                console.print(f"[dim]Final URL: {response.url}[/dim]")

            if response.status_code != 200:
                return TestResult(False, f"Expected 200, got {response.status_code}", {"url": url})

            html = response.text

            # Special case: Pending configuration
            if config.get("pending_config"):
                if "Pending Configuration" in html or "pending" in html.lower():
                    return TestResult(True, "Shows pending configuration page (expected)", {"url": url})
                # If it shows a real landing page, that's even better!
                if "MCP" in html or "A2A" in html:
                    return TestResult(True, "Shows landing page (configured!)", {"url": url})
                return TestResult(False, "Expected pending config or landing page", {"url": url})

            # Normal landing page checks
            checks = []
            if "MCP" in html or "/mcp" in html:
                checks.append("MCP")
            if "A2A" in html or "agent-to-agent" in html.lower():
                checks.append("A2A")
            if "agent.json" in html or ".well-known" in html:
                checks.append("agent card")

            if len(checks) >= 2:  # At least 2 of the 3 should be present
                return TestResult(True, f"Landing page shows {', '.join(checks)}", {"url": url})

            return TestResult(False, f"Landing page missing content (found: {checks})", {"url": url})

        return TestResult(True, "Page accessible", {"url": url})

    except requests.Timeout:
        return TestResult(False, "Request timed out", {"url": url})
    except requests.RequestException as e:
        return TestResult(False, f"Request failed: {e}", {"url": url})


def run_tests(agents: list[str] | None = None, verbose: bool = False) -> tuple[int, int]:
    """Run all tests and return (passed, total)."""
    console.print("\n[bold cyan]🧪 Production Agent Tests[/bold cyan]\n")

    agents_to_test = agents if agents else list(PRODUCTION_AGENTS.keys())

    results: list[tuple[str, str, TestResult]] = []
    passed = 0
    total = 0

    for agent_name in agents_to_test:
        if agent_name not in PRODUCTION_AGENTS:
            console.print(f"[yellow]⚠️  Unknown agent: {agent_name}[/yellow]")
            continue

        config = PRODUCTION_AGENTS[agent_name]

        console.print(f"[bold]{agent_name}[/bold] ({config['type']}) - {config['url']}")

        # Test landing page / login
        result = test_landing_page(agent_name, config, verbose)
        results.append((agent_name, "Landing/Login", result))
        total += 1
        if result.passed:
            passed += 1

        # Test MCP endpoint
        if config.get("test_mcp"):
            result = test_mcp_endpoint(agent_name, config["url"], verbose)
            results.append((agent_name, "MCP Endpoint", result))
            total += 1
            if result.passed:
                passed += 1

        # Test A2A endpoint
        if config.get("test_a2a"):
            result = test_a2a_endpoint(agent_name, config["url"], verbose)
            results.append((agent_name, "A2A Endpoint", result))
            total += 1
            if result.passed:
                passed += 1

        console.print()

    # Print results table
    table = Table(title="Test Results")
    table.add_column("Agent", style="cyan")
    table.add_column("Test", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Message")

    for agent, test_name, result in results:
        status = "[green]✓ PASS[/green]" if result.passed else "[red]✗ FAIL[/red]"
        table.add_row(agent, test_name, status, result.message)

    console.print(table)
    console.print()

    return passed, total


def main():
    parser = argparse.ArgumentParser(description="Test production agents after deploy")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--agent", "-a", help="Test specific agent only")
    args = parser.parse_args()

    agents = [args.agent] if args.agent else None

    passed, total = run_tests(agents, args.verbose)

    # Summary
    if passed == total:
        console.print(f"[bold green]✓ All tests passed ({passed}/{total})[/bold green]")
        sys.exit(0)
    else:
        console.print(f"[bold red]✗ Some tests failed ({passed}/{total} passed)[/bold red]")
        console.print("\n[yellow]⚠️  Consider rolling back if critical features are broken[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()
