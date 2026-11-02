"""Integration tests against a CONTROLLED reference creative agent.

WHAT IS LEFT HERE, AND WHY IT IS NOT BDD. Three invariants of an OUTBOUND call to a
creative agent: that a trailing slash on ``agent_url`` reaches the same agent, that the
agent tolerates both wire forms of the ``format_id`` identity, and that URL variants
share one cache entry. None is buyer-visible, so no scenario can reach them, and the
creative agent is the one thing the harness may legitimately stand in for — a system we
do not own. ``tests/CLAUDE.md`` § Which kind of test is the rule.

WHAT WAS REMOVED, so it does not come back. Eight tests asserted the reference agent's
CATALOG CONTENTS — that ``display_image`` is present and shaped a certain way, that
``display_html`` / ``display_js`` / ``video_standard`` / ``video_vast`` exist, that the
agent returns at least ten formats. Those graded a third party's data rather than this
seller, which is precisely the drift the guard below exists to refuse; the public agent
dropped four of those very formats. Two more asserted that ``format_id`` carries
``{agent_url, id}`` with the fields populated, which BDD already grades on all four
transports (``tests/bdd/steps/domain/uc005_format_id_shape.py`` and its roundtrip and
third-party siblings) — and graded it by existence (``is not None``), which BDD
authoring rule 4 refuses.

These tests require a deterministic, pinned creative agent — they must NEVER
silently fall back to the live public agent (https://creative.adcontextprotocol.org),
whose catalog drifts (it dropped display_image/html/js/video_standard), which
made this suite red non-deterministically .

CREATIVE_AGENT_URL must point at the pinned reference agent. The authoritative
runner brings it up automatically (run_all_tests.sh → scripts/creative-agent-stack.sh,
mirroring the CI `creative` matrix group, single-sourced pin). To run ad-hoc:
``scripts/creative-agent-stack.sh up`` then export the URL. If it is unset or
points at the public host, the guard below FAILS LOUD with instructions — it
does not hit the internet (set ALLOW_LIVE_CREATIVE_AGENT=1 to deliberately
override for manual prod testing).

NOTE: The conftest test_environment fixture sets ADCP_TESTING=true globally,
which enables mock mode. These tests override it back to false via the
enable_live_mode fixture so they exercise the real MCP code path.
"""

import os

import pytest

from src.core import creative_agent_registry as creative_agent_registry_module
from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import AdCPNotFoundError

# The live creative agent URL — reads env var so CI can point at a containerized agent
CREATIVE_AGENT_URL = os.environ.get("CREATIVE_AGENT_URL", "https://creative.adcontextprotocol.org")
CREATIVE_AGENT_URL_WITH_SLASH = CREATIVE_AGENT_URL.rstrip("/") + "/"


_PUBLIC_CREATIVE_AGENT_HOST = "creative.adcontextprotocol.org"


@pytest.fixture(autouse=True, scope="module")
def _require_controlled_creative_agent():
    """Refuse to run against the live public agent — fail loud, never silently .

    The gating suite must be hermetic: CREATIVE_AGENT_URL must point at the
    pinned reference agent. If it is unset or points at the public host, we
    fail with actionable instructions instead of hitting the internet and
    asserting a third-party catalog that has drifted. ALLOW_LIVE_CREATIVE_AGENT=1
    is an explicit, deliberate override for manual prod testing only.
    """
    if os.environ.get("ALLOW_LIVE_CREATIVE_AGENT") == "1":
        return
    url = os.environ.get("CREATIVE_AGENT_URL", "")
    if not url or _PUBLIC_CREATIVE_AGENT_HOST in url:
        pytest.fail(
            "Controlled creative agent required . CREATIVE_AGENT_URL is "
            f"{url!r} — unset or the live public host. These tests must NOT hit "
            "https://creative.adcontextprotocol.org (its catalog drifts). Bring up the pinned "
            "reference agent: `scripts/creative-agent-stack.sh up` then "
            "`export CREATIVE_AGENT_URL=$(scripts/creative-agent-stack.sh url)`. The authoritative "
            "runner does this automatically. Deliberate prod test: ALLOW_LIVE_CREATIVE_AGENT=1.",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def enable_live_mode(monkeypatch):
    """Override ADCP_TESTING for live creative agent tests.

    The conftest test_environment fixture sets ADCP_TESTING=true globally,
    which enables mock mode. These tests intentionally call the real creative
    agent, so we override it back to false.
    """
    monkeypatch.setenv("ADCP_TESTING", "false")


@pytest.fixture(scope="module")
def registry():
    """Shared registry instance so live tests reuse the format cache.

    A per-test registry defeats caching and can hammer the public creative
    agent hard enough to trigger rate limiting in CI.
    """
    previous_registry = creative_agent_registry_module._registry
    shared_registry = CreativeAgentRegistry()
    shared_registry._format_cache.clear()
    creative_agent_registry_module._registry = shared_registry
    try:
        yield shared_registry
    finally:
        creative_agent_registry_module._registry = previous_registry


class TestURLNormalization:
    """Test URL handling with trailing slashes."""

    @pytest.mark.asyncio
    async def test_fetch_formats_without_trailing_slash(self, registry):
        """Fetch formats using URL without trailing slash."""
        agent = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL,  # No trailing slash
            name="Test Agent",
            enabled=True,
        )

        formats = await registry.get_formats_for_agent(agent)
        assert len(formats) > 0, "Should return formats without trailing slash"

    @pytest.mark.asyncio
    async def test_fetch_formats_with_trailing_slash(self, registry):
        """Fetch formats using URL with trailing slash."""
        agent = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,  # With trailing slash
            name="Test Agent",
            enabled=True,
        )

        formats = await registry.get_formats_for_agent(agent)
        assert len(formats) > 0, "Should return formats with trailing slash"

    @pytest.mark.asyncio
    async def test_get_format_without_trailing_slash(self, registry):
        """Get specific format using URL without trailing slash."""
        fmt = await registry.get_format(CREATIVE_AGENT_URL, "display_image")
        assert fmt is not None, "Should find display_image without trailing slash"

    @pytest.mark.asyncio
    async def test_get_format_with_trailing_slash(self, registry):
        """Get specific format using URL with trailing slash."""
        fmt = await registry.get_format(CREATIVE_AGENT_URL_WITH_SLASH, "display_image")
        assert fmt is not None, "Should find display_image with trailing slash"


class TestPreviewCreativeIdentityForm:
    """Pinned-agent tolerance regression for the format_id federation identity .

    preview_creative sends format_id as the identity OBJECT {agent_url, id}.
    Production is switching that dict to the canonical FormatId serialization
    (model_dump(mode="json")), whose Pydantic AnyUrl emits the trailing-slash
    form for path-less URLs. These tests pin that the pinned reference agent
    accepts BOTH wire forms of the identity's agent_url — so the serialization
    switch cannot regress preview against the authoritative gate agent.
    """

    @staticmethod
    def _manifest() -> dict:
        return {
            "creative_id": "ehdq-identity-form",
            "name": "Identity Form Probe",
            "format_id": "display_300x250",
            "assets": {
                "image": {
                    "asset_type": "image",
                    "url": "https://example.com/banner.png",
                    "width": 300,
                    "height": 250,
                },
            },
        }

    @staticmethod
    def _assert_preview_succeeded(result: dict) -> None:
        assert isinstance(result, dict) and result, f"Expected preview response dict, got {result!r}"
        previews = result.get("previews")
        assert previews, f"Expected non-empty previews, got response: {result!r}"
        renders = previews[0].get("renders")
        assert renders, f"Expected renders in first preview, got: {previews[0]!r}"
        assert renders[0].get("preview_url"), f"Expected preview_url in first render, got: {renders[0]!r}"

    @pytest.mark.asyncio
    async def test_preview_creative_identity_without_trailing_slash(self, registry):
        """preview succeeds with the no-slash identity form (current raw-dict bytes)."""
        result = await registry.preview_creative(CREATIVE_AGENT_URL, "display_300x250", self._manifest())
        self._assert_preview_succeeded(result)

    @pytest.mark.asyncio
    async def test_preview_creative_identity_with_trailing_slash(self, registry):
        """preview succeeds with the trailing-slash identity form (FormatId serialization).

        FormatId.model_dump(mode="json") emits the trailing-slash agent_url for
        path-less URLs; calling through the slash URL variant sends exactly that
        identity form on the wire, pinning the agent's tolerance of it.
        """
        result = await registry.preview_creative(CREATIVE_AGENT_URL_WITH_SLASH, "display_300x250", self._manifest())
        self._assert_preview_succeeded(result)


class TestCacheConsistency:
    """Test cache behavior with URL variations."""

    @pytest.mark.asyncio
    async def test_cache_works_after_first_fetch(self, registry):
        """Verify cache is populated and used on second fetch."""
        # First fetch - should call creative agent
        formats1 = await registry.list_all_formats(tenant_id=None)

        # Check cache is populated
        assert len(registry._format_cache) > 0, "Cache should be populated"

        # Second fetch - should use cache
        formats2 = await registry.list_all_formats(tenant_id=None)

        # Should return same formats
        assert len(formats1) == len(formats2)

    @pytest.mark.asyncio
    async def test_cache_key_matches_default_agent(self, registry):
        """Verify cache key matches DEFAULT_AGENT URL."""
        # Fetch formats (populates cache)
        await registry.list_all_formats(tenant_id=None)

        # DEFAULT_AGENT uses URL without trailing slash
        expected_key = CREATIVE_AGENT_URL

        cache_keys = list(registry._format_cache.keys())
        print(f"Cache keys: {cache_keys}")

        assert expected_key in cache_keys, f"Expected cache key '{expected_key}' not found. Keys: {cache_keys}"

    @pytest.mark.asyncio
    async def test_url_variations_share_single_cache_entry(self, registry):
        """URL with and without trailing slash share the same cache entry."""
        # Fetch with no trailing slash (DEFAULT_AGENT style)
        agent_no_slash = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL,
            name="No Slash",
            enabled=True,
        )
        await registry.get_formats_for_agent(agent_no_slash)
        cache_after_no_slash = len(registry._format_cache)

        # Fetch with trailing slash — should hit cache, not create new entry
        agent_with_slash = CreativeAgent(
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,
            name="With Slash",
            enabled=True,
        )
        await registry.get_formats_for_agent(agent_with_slash)
        cache_after_with_slash = len(registry._format_cache)

        assert cache_after_with_slash == cache_after_no_slash, (
            f"Expected 1 cache entry, got {cache_after_with_slash}. Keys: {list(registry._format_cache.keys())}"
        )


class TestFormatResolverIntegration:
    """Test format_resolver with real creative agent."""

    def test_get_format_without_slash(self):
        """Test format_resolver.get_format with URL without trailing slash."""
        from src.core.format_resolver import get_format

        fmt = get_format(
            format_id="display_image",
            agent_url=CREATIVE_AGENT_URL,
            tenant_id=None,
            product_id=None,
        )

        assert fmt is not None, "Should find display_image via format_resolver"
        assert fmt.format_id.id == "display_image"

    def test_get_format_with_trailing_slash(self):
        """Test format_resolver.get_format with URL with trailing slash."""
        from src.core.format_resolver import get_format

        fmt = get_format(
            format_id="display_image",
            agent_url=CREATIVE_AGENT_URL_WITH_SLASH,
            tenant_id=None,
            product_id=None,
        )

        assert fmt is not None, "Should find display_image with trailing slash"
        assert fmt.format_id.id == "display_image"

    def test_get_format_error_for_nonexistent(self):
        """Test format_resolver raises clear error for nonexistent format."""
        from src.core.format_resolver import get_format

        with pytest.raises(AdCPNotFoundError) as exc_info:
            get_format(
                format_id="nonexistent_format_xyz",
                agent_url=CREATIVE_AGENT_URL,
                tenant_id=None,
                product_id=None,
            )
