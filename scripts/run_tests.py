#!/usr/bin/env python3
"""Comprehensive test runner for AdCP Sales Agent.

This script runs all tests in the correct order with proper setup.
Tests are grouped by category and dependencies are handled.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path to import from project
sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
DEFAULT_DATABASE_URL = os.environ.get(
    "DEFAULT_DATABASE_URL",
    f"postgresql://adcp_user:secure_password_change_me@localhost:{DEFAULT_POSTGRES_PORT}/adcp_test",
)

# Test categories - now using directory structure
TEST_CATEGORIES = {
    "unit": {"description": "Unit tests with minimal dependencies", "path": "tests/unit", "markers": "-m unit"},
    "integration": {
        "description": "Integration tests requiring database",
        "path": "tests/integration",
        "markers": "-m integration",
    },
    "e2e": {"description": "End-to-end tests with full system", "path": "tests/e2e", "markers": "-m e2e"},
    "ui": {
        "description": "UI tests for Admin interface",
        "path": "tests/ui",
        "markers": '-m "not skip_ci"',  # UI tests might be skipped in CI
    },
    "ai": {
        "description": "AI-related tests (requires GEMINI_API_KEY)",
        "path": "tests/integration",
        "markers": "-m ai",
    },
    "all": {"description": "All tests", "path": "tests", "markers": ""},
}


def run_command(cmd, env=None, check=True):
    """Run a command and return success status."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)

    if result.returncode != 0 and check:
        print(f"Error: {result.stderr}")
        return False

    print(result.stdout)
    if result.stderr and result.returncode != 0:
        print(f"Errors: {result.stderr}")

    return result.returncode == 0


def setup_test_environment():
    """Set up test environment variables."""
    env = os.environ.copy()

    # Default test database (PostgreSQL only)
    database_url = env.get("DATABASE_URL")
    if not database_url:
        env["DATABASE_URL"] = DEFAULT_DATABASE_URL
        print(f"📊 Using PostgreSQL database: {env['DATABASE_URL']}")
    else:
        if database_url.startswith("sqlite"):
            raise SystemExit("❌ SQLite is not supported. Set DATABASE_URL to a PostgreSQL connection string.")
        print(f"📊 Using database: {database_url}")

    env.setdefault("DB_TYPE", "postgresql")

    # Set testing mode
    env["TESTING"] = "true"
    env["ADCP_TESTING"] = "true"

    # Dummy values for required env vars if not set
    if "GEMINI_API_KEY" not in env:
        print("⚠️  Warning: GEMINI_API_KEY not set - AI tests will be limited")
        env["GEMINI_API_KEY"] = "test_key_for_mocking"

    # OAuth test credentials
    if "GOOGLE_CLIENT_ID" not in env:
        env["GOOGLE_CLIENT_ID"] = "test_client_id"
    if "GOOGLE_CLIENT_SECRET" not in env:
        env["GOOGLE_CLIENT_SECRET"] = "test_client_secret"
    if "SUPER_ADMIN_EMAILS" not in env:
        env["SUPER_ADMIN_EMAILS"] = "test@example.com"

    return env


def run_tests(categories, verbose=False, failfast=False, coverage=False, specific_test=None):
    """Run tests for specified categories."""
    env = setup_test_environment()

    # Build pytest command
    pytest_opts = []

    if verbose:
        pytest_opts.append("-v")
    if failfast:
        pytest_opts.append("-x")
    if coverage:
        pytest_opts.extend(["--cov=.", "--cov-report=html", "--cov-report=term-missing:skip-covered"])

    # If specific test provided, run only that
    if specific_test:
        cmd = f"pytest {specific_test} {' '.join(pytest_opts)}"
        success = run_command(cmd, env=env, check=False)
        return 0 if success else 1

    # Run tests by category
    failed_categories = []
    passed_categories = []

    for category in categories:
        if category not in TEST_CATEGORIES:
            print(f"❌ Unknown test category: {category}")
            print(f"Available categories: {', '.join(TEST_CATEGORIES.keys())}")
            continue

        print(f"\n{'=' * 60}")
        print(f"Running {category} tests: {TEST_CATEGORIES[category]['description']}")
        print("=" * 60)

        test_path = TEST_CATEGORIES[category]["path"]
        markers = TEST_CATEGORIES[category]["markers"]

        # Check if test path exists
        if not os.path.exists(test_path):
            print(f"⚠️  Test path not found: {test_path}")
            continue

        # Build command
        cmd_parts = ["pytest", test_path]
        if markers:
            cmd_parts.append(markers)
        cmd_parts.extend(pytest_opts)

        # Special handling for AI tests without API key
        if category == "ai" and env.get("GEMINI_API_KEY") == "test_key_for_mocking":
            cmd_parts.append('-k "not test_ai_integration"')

        cmd = " ".join(cmd_parts)

        if run_command(cmd, env=env, check=False):
            passed_categories.append(category)
        else:
            failed_categories.append(category)
            if failfast:
                break

    # Summary
    print(f"\n{'=' * 60}")
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"✅ Passed: {len(passed_categories)} categories")
    print(f"❌ Failed: {len(failed_categories)} categories")

    if failed_categories:
        print("\nFailed categories:")
        for category in failed_categories:
            print(f"  - {category}")

    if coverage and os.path.exists("htmlcov/index.html"):
        print("\n📊 Coverage report generated: htmlcov/index.html")

    return 1 if failed_categories else 0


def list_categories():
    """List all available test categories."""
    print("Available test categories:")
    print("-" * 40)
    for name, info in TEST_CATEGORIES.items():
        print(f"  {name:12} - {info['description']}")
    print("-" * 40)
    print("\nUsage: python run_tests.py [category1] [category2] ...")
    print("       python run_tests.py all  # Run all tests")


def main():
    parser = argparse.ArgumentParser(description="Run AdCP Sales Agent tests")
    parser.add_argument("categories", nargs="*", default=["unit"], help="Test categories to run (default: unit)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-x", "--failfast", action="store_true", help="Stop on first failure")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--list", action="store_true", help="List available test categories")
    parser.add_argument("--test", help="Run specific test file or test function")

    args = parser.parse_args()

    if args.list:
        list_categories()
        return 0

    # Default to 'unit' if no categories specified
    categories = args.categories if args.categories else ["unit"]

    return run_tests(
        categories, verbose=args.verbose, failfast=args.failfast, coverage=args.coverage, specific_test=args.test
    )


if __name__ == "__main__":
    sys.exit(main())
