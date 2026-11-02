"""Unit tests for format resolver override logic and coverage gaps.

salesagent-c4s: format_resolver uses model_dump() dict roundtrip to merge
platform_config overrides, but model_dump() drops exclude=True fields
(like platform_config), causing the base format's platform_config to be
silently lost during merging.

salesagent-uujr: Cover get_format(), _get_product_format_override() edge cases,
and list_available_formats() error paths — 67% → 100%.

Note: Must use src.core.schemas.Format (which has exclude=True on platform_config),
not the adcp library Format (which does not).

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-07
#
# SPEC_BACKED (2 tests):
#   test_search_all_agents_no_match_raises_not_found — AdCP error.json: unknown format_id is an error
#   test_success_returns_formats — AdCP list-creative-formats-response.json: returns formats array
#
# DECISION_BACKED (2 tests):
#   test_base_platform_config_preserved_during_override — bug fix (salesagent-c4s)
#   test_override_merges_into_existing_platform — bug fix (salesagent-c4s)
#
# CHARACTERIZATION (10 tests):
#   test_no_platform_config_override_preserves_base — locks: base preserved when no override
#   test_base_with_none_platform_config — locks: override applies to None base
#   test_product_override_path — locks: resolution order (override → agent → error)
#   test_product_override_none_falls_through_to_agent — locks: fallthrough path
#   test_search_all_agents_no_agent_url — locks: search-all behavior
#   test_not_found_error_includes_agent_url — locks: error message format
#   test_not_found_error_no_agent_url_no_tenant — locks: minimal error format
#   test_no_product_row_returns_none — locks: None for missing DB row
#   test_format_id_not_in_overrides_returns_none — locks: None for missing format_id
#   test_no_format_overrides_key_returns_none — locks: None for missing key
#
# DECISION_BACKED, added 2026-08-25 (7 tests, class TestFindFormat):
#   find_format keys format identity on (agent_url, id, width, height, duration_ms)
#   rather than pydantic equality — salesagent-kyc89, where a class-sensitive `==`
#   matched nothing on A2A and demoted every generative creative to a static one.
#   The parameterized/unparameterized case guards the other direction: class-agnostic
#   must not become lenient about AdCP 2.5 parameters.
#
# SUSPECT (2 tests):
#   test_registry_creation_fails_returns_empty — salesagent-z60b: infrastructure error → []
#   test_format_fetch_fails_returns_empty — salesagent-z60b: connection error → []
# ---
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import AdCPFormatNotFoundError
from src.core.schemas import Format
from tests.helpers.adcp_factories import create_test_format_id


def _make_format(format_id_str: str = "display_300x250", name: str = "Test", **kwargs) -> Format:
    """Create a Format using the internal schema (with exclude=True on platform_config)."""
    fid = create_test_format_id(format_id_str)
    assets = [{"item_type": "individual", "asset_id": "primary", "asset_type": "image", "required": True}]
    return Format(format_id=fid, name=name, type="display", assets=assets, **kwargs)


class TestProductFormatOverrideMerge:
    """Test that _get_product_format_override preserves base platform_config."""

    def test_base_platform_config_preserved_during_override(self):
        """Base format's platform_config must survive override merging.

        This is the core bug: model_dump() drops exclude=True fields,
        so base_platform_config was always {} and only override values survived.
        """
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={"gam": {"width": 300, "height": 250}},
        )

        # Verify our test setup: model_dump drops platform_config
        assert "platform_config" not in base_format.model_dump(), (
            "Test setup error: platform_config should be excluded from model_dump()"
        )
        assert base_format.platform_config == {"gam": {"width": 300, "height": 250}}, (
            "Test setup error: platform_config should be accessible on the model"
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "kevel": {"zone_id": 99},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        # Base GAM config must be preserved
        assert result.platform_config is not None, "platform_config was lost entirely"
        assert "gam" in result.platform_config, "Base format's platform_config was lost during override merge"
        assert result.platform_config["gam"] == {"width": 300, "height": 250}
        # Override config must also be present
        assert "kevel" in result.platform_config
        assert result.platform_config["kevel"] == {"zone_id": 99}

    def test_override_merges_into_existing_platform(self):
        """When override targets same platform as base, values merge with override precedence."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={
                "gam": {"width": 300, "height": 250, "ad_unit_id": "original"},
            },
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "gam": {"creative_template_id": 12345, "width": 1},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        assert result.platform_config is not None
        gam_config = result.platform_config["gam"]
        # Base values preserved
        assert gam_config["height"] == 250
        assert gam_config["ad_unit_id"] == "original"
        # Override values applied
        assert gam_config["creative_template_id"] == 12345
        # Override takes precedence for conflicts
        assert gam_config["width"] == 1

    def test_no_platform_config_override_preserves_base(self):
        """When override has no platform_config key, base format is returned unchanged."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            platform_config={"gam": {"width": 300}},
        )

        format_overrides = {
            "display_300x250": {
                "some_other_key": "value",
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        # platform_config should be preserved from base
        assert result.platform_config == {"gam": {"width": 300}}

    def test_base_with_none_platform_config(self):
        """When base format has no platform_config, override still applies."""
        base_format = _make_format(
            "display_300x250",
            name="Medium Rectangle",
            # No platform_config — defaults to None
        )

        format_overrides = {
            "display_300x250": {
                "platform_config": {
                    "gam": {"creative_template_id": 99999},
                }
            }
        }

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch(
                "src.core.format_resolver.get_format",
                return_value=base_format,
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_result = mock_session.execute.return_value
            mock_result.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("tenant1", "prod1", "display_300x250")

        assert result is not None
        assert result.platform_config == {"gam": {"creative_template_id": 99999}}


# ---------------------------------------------------------------------------
# get_format() — top-level resolution paths
# ---------------------------------------------------------------------------


class TestGetFormat:
    """Tests for get_format() covering product override, all-agents search, and error paths."""

    def test_product_override_path(self):
        """get_format returns product override when product_id and tenant_id are provided."""
        override_format = _make_format("display_300x250", name="Override Format")

        with patch(
            "src.core.format_resolver._get_product_format_override",
            return_value=override_format,
        ):
            from src.core.format_resolver import get_format

            result = get_format(
                "display_300x250",
                agent_url="https://agent.example.com",
                tenant_id="t1",
                product_id="prod_1",
            )

        assert result.name == "Override Format"

    def test_product_override_none_falls_through_to_agent(self):
        """get_format falls through to agent when product override returns None."""
        agent_format = _make_format("display_300x250", name="Agent Format")

        with (
            patch("src.core.format_resolver._get_product_format_override", return_value=None),
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=agent_format),
        ):
            from src.core.format_resolver import get_format

            result = get_format(
                "display_300x250",
                agent_url="https://agent.example.com",
                tenant_id="t1",
                product_id="prod_1",
            )

        assert result.name == "Agent Format"

    def test_search_all_agents_no_agent_url(self):
        """get_format searches all agents when agent_url is None.

        The listing carries a real FormatId, because that is what
        ``CreativeAgentRegistry.list_all_formats`` returns -- ``Format.format_id`` is
        annotated as the library FormatId. The version of this test that shipped with
        the bug put a bare STRING here, with a docstring explaining that a real Format
        would not match; that was a test bent to fit broken code, and it is what kept
        `fmt.format_id == format_id` looking correct while it resolved nothing in
        production (#2093).
        """
        mock_fmt = MagicMock()
        mock_fmt.format_id = create_test_format_id("display_300x250")
        mock_fmt.name = "Found Format"

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            result = get_format("display_300x250", tenant_id="t1")

        assert result.name == "Found Format"

    def test_search_all_agents_no_match_raises_not_found(self):
        """get_format raises AdCPNotFoundError when format not found in any agent."""
        mock_fmt = MagicMock()
        mock_fmt.format_id = create_test_format_id("video_1920x1080")  # different id -> no match

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[mock_fmt]),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPFormatNotFoundError):
                get_format("display_300x250", tenant_id="t1")

    def test_not_found_error_includes_agent_url(self):
        """AdCPNotFoundError message includes agent_url when provided."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=None),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPFormatNotFoundError) as exc_info:
                get_format("display_300x250", agent_url="https://agent.example.com", tenant_id="t1")

            assert exc_info.value.error_code == "REFERENCE_NOT_FOUND"
            assert exc_info.value.recovery == "correctable"
            assert exc_info.value.field == "format_id"
            # The identifiers this test has always been about are still reported --
            # they moved from the interpolated sentence onto ``details``, which is
            # where AdCP 3.1.1 puts request-specific content.
            assert exc_info.value.details.agent_url == "https://agent.example.com"
            assert exc_info.value.details.tenant_id == "t1"
            assert exc_info.value.details.format_id == "display_300x250"

    def test_not_found_error_no_agent_url_no_tenant(self):
        """AdCPNotFoundError message is minimal without agent_url and tenant_id."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch("src.core.format_resolver.run_async_in_sync_context", return_value=[]),
        ):
            from src.core.format_resolver import get_format

            with pytest.raises(AdCPFormatNotFoundError) as exc_info:
                get_format("nonexistent")

            # "Minimal" now means: the unset identifiers are absent from ``details``,
            # and the buyer sentence never carried them in the first place (it is
            # CODE_TABLE's, not interpolated at the raise site).
            assert exc_info.value.details.format_id == "nonexistent"
            assert exc_info.value.details.agent_url is None
            assert exc_info.value.details.tenant_id is None
            assert "from agent" not in exc_info.value.message
            assert "for tenant" not in exc_info.value.message
            assert exc_info.value.recovery == "correctable"


# ---------------------------------------------------------------------------
# _get_product_format_override() — edge case paths
# ---------------------------------------------------------------------------


class TestProductFormatOverrideEdgeCases:
    """Edge cases for _get_product_format_override not covered by merge tests."""

    def test_no_product_row_returns_none(self):
        """Returns None when product doesn't exist in DB."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = None

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "nonexistent", "display_300x250")

        assert result is None

    def test_format_id_not_in_overrides_returns_none(self):
        """Returns None when format_id is not in format_overrides."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = (
                {"format_overrides": {"other_format": {"platform_config": {}}}},
            )

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None

    def test_no_format_overrides_key_returns_none(self):
        """Returns None when implementation_config has no format_overrides key."""
        with patch("src.core.format_resolver.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = ({"some_other_config": "value"},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None

    def test_base_format_lookup_fails_returns_none(self):
        """Returns None when the base format genuinely does not exist.

        The SUSPECT(salesagent-z4zl) marker is REMOVED and its question answered by
        salesagent-w4x1: yes, the override path should propagate — and now does. The
        branch was ``except (AdCPNotFoundError, Exception)``, whose first member is dead
        (``Exception`` already covers it) and whose second swallowed everything,
        including a typed transient. A creative agent answering 429 was reported as
        "no such override".

        It is now ``except AdCPFormatNotFoundError`` returning None, with every other
        typed error re-raised. The mock moved with it: it injected a bare
        ``AdCPNotFoundError``, a shape ``get_format`` never produces — it raises
        ``AdCPFormatNotFoundError`` (format_resolver.py:128) and nothing else. A mock
        carrying a shape production cannot emit was propping up the very swallow this
        change removes.
        """
        format_overrides = {"display_300x250": {"platform_config": {"gam": {"width": 1}}}}

        with (
            patch("src.core.format_resolver.get_db_session") as mock_db,
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.get_format",
                side_effect=AdCPFormatNotFoundError(),
            ),
        ):
            mock_session = mock_db.return_value.__enter__.return_value
            mock_session.execute.return_value.fetchone.return_value = ({"format_overrides": format_overrides},)

            from src.core.format_resolver import _get_product_format_override

            result = _get_product_format_override("t1", "prod1", "display_300x250")

        assert result is None


# ---------------------------------------------------------------------------
# list_available_formats() — error paths
# ---------------------------------------------------------------------------


class TestListAvailableFormats:
    """Tests for list_available_formats() error and success paths."""

    # SUSPECT(salesagent-z60b): infrastructure error silently returns [] — should it propagate?
    def test_registry_creation_fails_returns_empty(self):
        """Returns empty list when get_creative_agent_registry raises."""
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=RuntimeError("Registry initialization failed"),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []

    # SUSPECT(salesagent-z60b): connection error silently returns [] — should it propagate?
    def test_format_fetch_fails_returns_empty(self):
        """Returns empty list when list_all_formats raises."""
        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.run_async_in_sync_context",
                side_effect=RuntimeError("Connection failed"),
            ),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert result == []

    def test_failure_still_degrades_to_empty(self):
        """The degradation stays, for EVERY failure -- typed or not.

        salesagent-w4x1 proposed propagating a typed transient here so a
        rate-limited agent would not read as an empty catalog. That defect is real
        but is not at this site: the only caller is the admin UI
        (src/admin/blueprints/products.py:156), which catches the SDK's
        ``adcp.exceptions.ADCPError`` -- a different class tree from
        ``src.core.exceptions.AdCPSalesAgentError`` -- so propagating would have 500'd an admin
        page rather than informed a buyer. Pinned here so the next attempt reads this
        before repeating it.
        """
        with patch(
            "src.core.creative_agent_registry.get_creative_agent_registry",
            side_effect=RuntimeError("something unanticipated"),
        ):
            from src.core.format_resolver import list_available_formats

            assert list_available_formats(tenant_id="t1") == []

    def test_success_returns_formats(self):
        """Returns formats from registry on success."""
        fmt1 = _make_format("display_300x250", name="Format 1")
        fmt2 = _make_format("display_728x90", name="Format 2")

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_reg,
            patch(
                "src.core.format_resolver.run_async_in_sync_context",
                return_value=[fmt1, fmt2],
            ),
        ):
            from src.core.format_resolver import list_available_formats

            result = list_available_formats(tenant_id="t1")

        assert len(result) == 2
        assert result[0].name == "Format 1"


class TestFindFormat:
    """find_format decides format identity by VALUE, never by pydantic class.

    salesagent-kyc89: ``FormatId`` exists twice — the library type the AdCP
    schemas declare and ``src.core.schemas._base.FormatId``, our field-identical
    subclass. Pydantic v2 equality is class-sensitive, so the ``==`` loop this
    helper replaced matched nothing whenever the two sides were built by
    different code paths, and a generative creative was silently written as a
    plain static asset.
    """

    def test_matches_across_the_two_format_id_classes(self):
        """The subclass and the library type name the same format, so they match."""
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format
        from src.core.schemas import FormatId as OurFormatId

        fmt = _make_format()
        library_reference = LibraryFormatId(agent_url=str(fmt.format_id.agent_url), id=fmt.format_id.id)
        our_reference = OurFormatId(agent_url=str(fmt.format_id.agent_url), id=fmt.format_id.id)

        # The precondition that made the bug invisible: these compare UNEQUAL.
        assert library_reference != our_reference

        assert find_format([fmt], library_reference) is fmt
        assert find_format([fmt], our_reference) is fmt

    def test_trailing_slash_on_agent_url_does_not_split_identity(self):
        """A reference built from a str and one built from AnyUrl name one format."""
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        bare = str(fmt.format_id.agent_url).rstrip("/")
        assert find_format([fmt], LibraryFormatId(agent_url=bare, id=fmt.format_id.id)) is fmt

    def test_a_different_id_does_not_match(self):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        other = LibraryFormatId(agent_url=str(fmt.format_id.agent_url), id="video_640x480")
        assert find_format([fmt], other) is None

    def test_a_different_agent_url_does_not_match(self):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        other = LibraryFormatId(agent_url="https://other-agent.example.com", id=fmt.format_id.id)
        assert find_format([fmt], other) is None

    def test_a_parameterized_reference_resolves_to_the_template_it_parameterizes(self):
        """Identity is (agent_url, id) — the parameters name a variant, not another format.

        This is the graded contract, not a convenience: core/format-id.json requires
        [agent_url, id], the list_formats storyboard matches on
        ``match_keys: [agent_url, id]``, and a template format exists precisely so a
        parameterized reference can resolve to it and read its spec. An identity that
        included width/height would send a 300x250 request away empty-handed — and in
        _processing that means format_obj is None, the generative branch is skipped,
        and the creative is silently written as a static asset. Exactly the failure
        this whole helper exists to prevent.
        """
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        parameterized = LibraryFormatId(
            agent_url=str(fmt.format_id.agent_url), id=fmt.format_id.id, width=300, height=250
        )
        assert find_format([fmt], parameterized) is fmt

    def test_returns_none_for_an_empty_listing(self):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        assert find_format([], LibraryFormatId(agent_url="https://a.example.com", id="display")) is None

    def test_returns_the_first_match_in_listing_order(self):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        first = _make_format(name="First")
        second = _make_format(name="Second")
        reference = LibraryFormatId(agent_url=str(first.format_id.agent_url), id=first.format_id.id)
        assert find_format([first, second], reference) is first


class TestFormatIdentityCanonicalization:
    """agent_url is compared in the spec's canonical form, not a trimmed string.

        adcp/_schemas/3.1/core/format-id.json, agent_url: "Callers comparing two
        `format-id` values MUST canonicalize `agent_url` per the AdCP URL canonicalization
        rules before treating two formats as the same."

    find_format meets that MUST by going through ``format_id_identity`` ->
        ``canonical_agent_url`` -> the SDK's ``canonicalize_target_uri``, rather than
        trimming the string itself. These cases are the ones that survive pydantic's own
        AnyUrl normalization and so actually distinguish the canonical rule from a trim:
        userinfo and a fragment are stripped by the former and kept by the latter.
    """

    @pytest.mark.parametrize(
        ("reference_url", "what_the_trim_kept"),
        [
            ("https://creative.adcontextprotocol.org/#section", "a fragment"),
            ("https://user@creative.adcontextprotocol.org", "userinfo"),
        ],
    )
    def test_a_reference_matches_despite(self, reference_url, what_the_trim_kept):
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        reference = LibraryFormatId(agent_url=reference_url, id=fmt.format_id.id)

        assert find_format([fmt], reference) is fmt, (
            f"{what_the_trim_kept} split two references the spec says name one format"
        )

    def test_a_genuinely_different_host_still_does_not_match(self):
        """Canonicalizing must not blur two agents together."""
        from adcp.types import FormatId as LibraryFormatId

        from src.core.format_resolver import find_format

        fmt = _make_format()
        other_agent = LibraryFormatId(agent_url="https://someone-else.example.com", id=fmt.format_id.id)
        assert find_format([fmt], other_agent) is None
