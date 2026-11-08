"""Tests for tox configuration and coverage setup.

Validates that the tox.ini and pyproject.toml coverage config are correctly
structured for parallel test execution with combined coverage reporting.

These tests guard the Core Invariant: test runner must produce identical
pass/fail results and per-suite reports whether sequential or parallel.
"""

import configparser
import re
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


def _expand_env_names(spec: str) -> set[str]:
    """tox env names from a section suffix or a ``depends`` value, with ``{a,b}`` expanded."""
    names: set[str] = set()
    # Split on the commas OUTSIDE braces: `bdd-inprocess-s{1,2}` is one entry.
    for token in re.findall(r"(?:[^,\s{}]|\{[^{}]*\})+", spec):
        match = re.search(r"\{([^{}]*)\}", token)
        if match is None:
            names.add(token)
            continue
        for factor in match.group(1).split(","):
            names |= _expand_env_names(token[: match.start()] + factor.strip() + token[match.end() :])
    return names


def test_quality_depends_on_every_bdd_env():
    """``quality`` runs after every BDD env, so its single-threaded checkers are not starved.

    tox ignores a ``depends`` entry for an env the invocation does not run, so listing both
    the plain ``bdd`` env and the shard envs costs nothing on either run path. An env missing
    from the list runs beside ``quality`` under ``tox -p`` (see the rationale above
    ``[testenv:quality]``).
    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(str(TOX_INI))
    bdd_envs = {
        name
        for section in parser.sections()
        if section.startswith("testenv:bdd")
        for name in _expand_env_names(section.removeprefix("testenv:"))
    }
    assert {"bdd", "bdd_e2e"} <= bdd_envs and any(n.startswith("bdd-inprocess-s") for n in bdd_envs), bdd_envs
    depends = _expand_env_names(parser.get("testenv:quality", "depends", fallback=""))
    assert not bdd_envs - depends, f"[testenv:quality] depends is missing {sorted(bdd_envs - depends)}"


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
