"""Tests for the version module."""

import re

import pytest

from src.core.version import BuildInfo, get_build_info, get_git_branch, get_git_sha, get_version


def test_get_version_returns_valid_semver():
    """get_version should return a valid semantic version string."""
    version = get_version()
    assert isinstance(version, str)
    assert re.match(r"^\d+\.\d+\.\d+", version), f"Version '{version}' doesn't match semver pattern"


def test_get_version_not_default():
    """get_version should return the actual version, not the fallback."""
    version = get_version()
    # The fallback "0.0.0" is only returned if both importlib.metadata and pyproject.toml fail,
    # which would indicate a broken environment rather than an actual version of 0.0.0.
    assert version != "0.0.0", "Version should not be the fallback value"


def test_get_git_sha_prefers_env_and_truncates(monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "abcdef1234567890")
    assert get_git_sha() == "abcdef1"


def test_get_git_sha_ignores_unknown_sentinel(monkeypatch, tmp_path):
    """`unknown` is the Dockerfile default — treat as unset so we fall back to git."""
    monkeypatch.setenv("APP_GIT_SHA", "unknown")
    sha = get_git_sha()
    # Either git resolves it (developer checkout) or it's None (not in a repo).
    if sha is not None:
        assert len(sha) == 7


def test_get_git_branch_prefers_env(monkeypatch):
    monkeypatch.setenv("APP_GIT_BRANCH", "release/2026-q2")
    assert get_git_branch() == "release/2026-q2"


def test_get_build_info_reads_env(monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "deadbee1234")
    monkeypatch.setenv("APP_GIT_BRANCH", "feature/footer-stamp")
    bi = get_build_info()
    assert bi.git_sha == "deadbee"
    assert bi.git_branch == "feature/footer-stamp"
    assert bi.version  # whatever the project version is


class TestBuildInfoDisplay:
    """Display formatting must hide noise (main, detached HEAD, tag-as-branch)."""

    def test_full_display_with_branch(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="feature/x")
        assert bi.display == "v1.7.0 · abc1234 · feature/x"

    def test_full_display_omits_main(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="main")
        assert bi.display == "v1.7.0 · abc1234"

    def test_full_display_omits_master(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="master")
        assert bi.display == "v1.7.0 · abc1234"

    def test_full_display_omits_detached_head(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="HEAD")
        assert bi.display == "v1.7.0 · abc1234"

    def test_full_display_omits_branch_equal_to_version_tag(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="v1.7.0")
        assert bi.display == "v1.7.0 · abc1234"

    def test_full_display_omits_branch_equal_to_sha(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="abc1234")
        assert bi.display == "v1.7.0 · abc1234"

    def test_display_without_sha(self):
        bi = BuildInfo(version="1.7.0", git_sha=None, git_branch=None)
        assert bi.display == "v1.7.0"

    def test_short_display_drops_branch(self):
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch="feature/x")
        assert bi.display_short == "v1.7.0 · abc1234"

    def test_short_display_without_sha(self):
        bi = BuildInfo(version="1.7.0", git_sha=None, git_branch="feature/x")
        assert bi.display_short == "v1.7.0"

    @pytest.mark.parametrize("branch", ["feature/<script>alert(1)</script>", "weird&name"])
    def test_display_does_not_escape_branch(self, branch):
        """Display returns a plain string; HTML-escaping is the template's job
        (Jinja autoescape). We just verify the branch is included verbatim."""
        bi = BuildInfo(version="1.7.0", git_sha="abc1234", git_branch=branch)
        assert branch in bi.display
