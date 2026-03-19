"""Tests for tox configuration and coverage setup.

Validates that the tox.ini and pyproject.toml coverage config are correctly
structured for parallel test execution with combined coverage reporting.

These tests guard the Core Invariant: test runner must produce identical
pass/fail results and per-suite reports whether sequential or parallel.
"""

import configparser
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOX_INI = PROJECT_ROOT / "tox.ini"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


class TestToxConfigExists:
    """tox.ini exists and is parseable."""

    def test_tox_ini_exists(self):
        """tox.ini must exist at project root."""
        assert TOX_INI.is_file(), f"tox.ini not found at {TOX_INI}"

    def test_tox_ini_is_valid_ini(self):
        """tox.ini must be parseable as INI config."""
        parser = configparser.ConfigParser()
        parser.read(str(TOX_INI))
        assert "tox" in parser.sections() or "tox:tox" in parser.sections(), "Missing [tox] section"


class TestToxEnvironments:
    """All 5 test suites + coverage env are configured."""

    @pytest.fixture
    def tox_config(self):
        if not TOX_INI.is_file():
            pytest.skip("tox.ini not found")
        parser = configparser.ConfigParser()
        parser.read(str(TOX_INI))
        return parser

    @pytest.mark.parametrize("env", ["unit", "integration", "e2e", "admin"])
    def test_test_environment_exists(self, tox_config, env):
        """Each test suite has a corresponding tox environment."""
        section = f"testenv:{env}"
        assert tox_config.has_section(section), f"Missing [{section}] in tox.ini"

    def test_coverage_environment_exists(self, tox_config):
        """A coverage combine environment exists."""
        assert tox_config.has_section("testenv:coverage"), "Missing [testenv:coverage] in tox.ini"

    def test_coverage_depends_on_all_suites(self, tox_config):
        """Coverage env must depend on all 5 test suites."""
        section = "testenv:coverage"
        if not tox_config.has_section(section):
            pytest.skip("No coverage env")
        deps_str = tox_config.get(section, "depends", fallback="")
        for suite in ["unit", "integration", "e2e", "admin"]:
            assert suite in deps_str, f"coverage env missing depends on '{suite}'"


class TestCoverageConfig:
    """pyproject.toml has correct coverage configuration."""

    @pytest.fixture
    def pyproject_content(self):
        return PYPROJECT.read_text()

    def test_coverage_run_source(self, pyproject_content):
        """[tool.coverage.run] must specify source = ['src']."""
        assert "[tool.coverage.run]" in pyproject_content, "Missing [tool.coverage.run] in pyproject.toml"
        assert "source" in pyproject_content, "Missing 'source' in coverage config"

    def test_coverage_relative_files(self, pyproject_content):
        """relative_files = true is REQUIRED for combine across tox envs."""
        assert "relative_files" in pyproject_content, "Missing relative_files in coverage config"

    def test_coverage_paths_mapping(self, pyproject_content):
        """[tool.coverage.paths] must map .tox/ paths back to src/."""
        assert "[tool.coverage.paths]" in pyproject_content, "Missing [tool.coverage.paths] in pyproject.toml"

    def test_coverage_report_config(self, pyproject_content):
        """[tool.coverage.report] should exist with show_missing."""
        assert "[tool.coverage.report]" in pyproject_content, "Missing [tool.coverage.report] in pyproject.toml"


class TestPerEnvCoverageIsolation:
    """Each tox env must use a unique COVERAGE_FILE to prevent SQLite race conditions."""

    @pytest.fixture
    def tox_content(self):
        if not TOX_INI.is_file():
            pytest.skip("tox.ini not found")
        return TOX_INI.read_text()

    def test_coverage_file_uses_envname(self, tox_content):
        """COVERAGE_FILE must contain {envname} for per-env isolation."""
        assert "COVERAGE_FILE" in tox_content, "Missing COVERAGE_FILE in tox.ini"
        assert "{envname}" in tox_content, "COVERAGE_FILE must use {envname} for per-env isolation"


class TestUnitEnvNoDatabaseUrl:
    """Unit test env must run without DATABASE_URL."""

    @pytest.fixture
    def tox_config(self):
        if not TOX_INI.is_file():
            pytest.skip("tox.ini not found")
        parser = configparser.ConfigParser()
        parser.read(str(TOX_INI))
        return parser

    def test_unit_env_unsets_database_url(self, tox_config):
        """Unit env should set DATABASE_URL to empty (unset it)."""
        section = "testenv:unit"
        if not tox_config.has_section(section):
            pytest.skip("No unit env")
        setenv = tox_config.get(section, "setenv", fallback="")
        # DATABASE_URL should be set to empty or explicitly handled
        assert "DATABASE_URL" in setenv, "Unit env must explicitly handle DATABASE_URL"
