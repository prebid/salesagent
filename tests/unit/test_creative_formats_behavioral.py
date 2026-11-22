"""Behavioral tests for UC-005 (creative format discovery) that need no database.

``_list_creative_formats_impl`` lists only the formats the tenant's products declare, read
through ``ProductUoW``, so a test that drives the listing to its answer needs those products
and runs behind the harness: a BR-UC-005 scenario on a2a, mcp, rest and e2e_rest, or
``CreativeFormatsEnv`` (tests/harness/creative_formats.py) in tests/integration/. What stays
here is what is decided before the products are read, or without the listing at all.

WHERE THE FILTER, SORT AND ADVISORY TESTS THAT USED TO LIVE HERE ARE GRADED. They called the
impl with only the creative agent registry mocked, so the product read reached for a real
database, and each grades an obligation something else already grades through production:

* Sort by name: @T-UC-005-inv-031-2-holds (exact ordered list). The unit test named
  "after filtering" sent no filter; sort after a real ``name_search`` is
  tests/integration/test_creative_formats_behavioral.py
  ``TestFormatsSort::test_sort_preserves_after_filtering``.
* asset_types excludes a format with no matching asset: @T-UC-005-inv-049-3-violated, and
  "a match is kept while a non-match is dropped" is @T-UC-005-main-filtered.
* asset_types on repeatable groups (a group with no match excluded; a format matching
  through either its individual asset or its group): the integration twins
  ``TestFormatsAssetTypes::test_group_assets_no_match_excluded`` and
  ``::test_mixed_individual_and_group_assets``.
* A format_ids filter that matches nothing returns nothing:
  @T-UC-005-storyboard-format-id-third-party-agent-out-of-scope (its only reference
  matches no listed format, and the seller's same-id format is not returned), plus
  ``TestFormatsFiltering::test_format_ids_no_match_returns_empty``.
* A render one pixel past min_width or max_width is excluded:
  ``TestFormatsDimensions::test_off_by_one_max_width_excluded`` and
  ``::test_off_by_one_min_width_excluded``.
* An empty ``name_search`` filters nothing: ``TestFormatsEdgeCases::test_empty_name_search_returns_all``.
* Per-agent advisories (UC-005-EXT-C-01/-02/-04): the impl passes the registry's
  ``errors`` to the response unchanged when no ``format_ids`` names the agent, and that is
  graded on the wire by @T-UC-005-peer-catalog-partial-discard (the agent's kept format
  AND its advisory) and @T-UC-005-peer-catalog-all-discarded (no format AND the advisory).
  The unit tests fed AGENT_UNREACHABLE advisories straight in, so they never graded the
  registry's one-advisory-per-failed-agent loop; the real unreachable dial and its
  first-party message are graded by
  tests/integration/test_creative_formats_payload_error_wire_safety.py.
* Successful discovery carries no advisory (UC-005-EXT-C-05): @T-UC-005-inv-049-2-violated
  (a format returned and ``errors`` empty on the wire) and @T-UC-005-empty-catalog.

The referral-lookup warning test is gone with no replacement, because the state it set up
does not occur: the agent lookup failing for the ``creative_agents`` referrals after it
succeeded for the aggregation. ``list_all_formats_with_errors`` calls the same
``_get_tenant_agents`` first, outside any handler, so a lookup that fails takes the request
down before the referrals are built. The referral handler itself is the silent degradation
tracked by FIXME(#1566) and allowlisted in test_architecture_no_silent_loop_failures.py.
"""

from unittest.mock import patch

import pytest

from src.core.schemas import Format, ListCreativeFormatsRequest
from tests.factories import PrincipalFactory

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"
MOCK_TENANT = {"tenant_id": "test-tenant", "name": "Test Tenant"}


class TestBroadstreetTemplateAssetParsing:
    """Regression: Broadstreet templates must parse with real assets.

    The pinned ``Format.assets`` variants carry a Literal ``asset_type`` discriminator,
    so the CLASS is the asset type; the code once passed
    ``ImageFormatAsset(asset_type=AssetContentType(...))`` for every asset and nothing
    constructed.

    BOTH TESTS NOW CALL PRODUCTION. They used to re-run the production loop against
    ``creative_formats._make_asset``, assembling their own ``Format`` -- so an adapter
    that stopped building assets at all would not have reddened either of them. The
    builder moved to ``BroadstreetAdapter.creative_formats`` (salesagent-zthsk) and these
    assert on what it answers.
    """

    def test_all_broadstreet_templates_produce_formats_with_assets(self):
        """Every Broadstreet template must produce a Format with non-empty assets."""
        from src.adapters.broadstreet import BroadstreetAdapter
        from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES

        by_id = {f.format_id.id: f for f in BroadstreetAdapter.creative_formats("test")}

        for tid, tmpl in BROADSTREET_TEMPLATES.items():
            fmt = by_id[f"broadstreet_{tid}"]
            assert fmt.assets, f"Template {tid} must have non-empty assets list"
            assert len(fmt.assets) == len(tmpl.get("required_assets", [])) + len(tmpl.get("optional_assets", [])), (
                f"Template {tid} asset count mismatch"
            )

    def test_asset_type_literals_match_inferred_type(self):
        """Each constructed asset carries the asset_type its id names."""
        from src.adapters.broadstreet import BroadstreetAdapter

        # One format per asset id below, chosen so each id appears in it.
        asset_types = {
            a.asset_id: a.asset_type for f in BroadstreetAdapter.creative_formats("test") for a in (f.assets or [])
        }

        for asset_id, expected_type in [
            ("front_image", "image"),
            ("logo", "image"),
            ("youtube_url", "video"),
            ("click_url", "url"),
            ("headline", "text"),
            ("html", "html"),
        ]:
            assert asset_types[asset_id] == expected_type, (
                f"{asset_id}: asset_type should be '{expected_type}', got '{asset_types[asset_id]}'"
            )


class TestMCPWrapperStringCoercion:
    """Regression: MCP wrapper must handle raw string inputs for enum params.

    The MCP wrapper (list_creative_formats) does type.value and at.value
    to extract enum values. If called directly with raw strings (bypassing
    FastMCP's auto-coercion), this crashes with AttributeError.
    The wrapper must coerce strings to enums before accessing .value.
    """


class TestRegistryCreationFailureRaisesServiceUnavailable:
    """UC-005-EXT-C-03: Registry creation failure raises AdCPServiceUnavailableError.

    Per AdCP 3.0.1 error-handling: operation-level failures (registry init) raise,
    so the boundary translator produces a two-layer envelope. Per-item advisory
    errors (partial-agent failure inside a successful discovery) stay in errors[].

    Reachable without a database: the registry is created before the tenant's products
    are read, so the refusal is decided first.
    """

    def test_registry_creation_failure_raises(self):
        """Covers: UC-005-EXT-C-03

        Given the creative agent registry cannot be initialized,
        when list_creative_formats is called,
        then AdCPServiceUnavailableError is raised so the boundary translator
        builds a spec-compliant two-layer envelope.
        """
        from src.core.exceptions import AdCPServiceUnavailableError
        from src.core.tools.creative_formats import _list_creative_formats_impl

        identity = PrincipalFactory.make_public_identity(tenant=MOCK_TENANT)

        with (
            patch(
                "src.core.creative_agent_registry.get_creative_agent_registry",
                side_effect=RuntimeError("Cannot connect to agent registry"),
            ),
            pytest.raises(AdCPServiceUnavailableError),
        ):
            _list_creative_formats_impl(ListCreativeFormatsRequest(), identity)


class TestReferralCapabilitiesAreCommitments:
    """Advertised creative-agent capabilities must all be backed by real tasks.

    AdCP design principle: capabilities are commitments — the conformance
    runner probes every advertised capability. Advertising `delivery` commits
    the agent to variant-level delivery reporting via a `get_creative_delivery`
    task. This agent does not implement that task, so `delivery` must not
    appear in the referral capabilities.

    The policy is anchored here, hardcoded. That every referral on the wire carries
    exactly ``ADVERTISED_CREATIVE_AGENT_CAPABILITIES`` is graded by
    @T-UC-005-main-referrals ("each referral should include the agent URL and supported
    capabilities") on a2a, mcp, rest and e2e_rest, which compares each referral against the
    constant. Driving the listing here would read the tenant's products.
    """

    def test_referral_capabilities_exclude_delivery_while_unimplemented(self):
        """creative_agents referrals advertise only backed capabilities.

        Guard pair: if get_creative_delivery is ever implemented on the
        registry, the hasattr assertion below fails, prompting re-evaluation
        of the advertised set alongside the exclusion assertion.
        """
        from src.core.creative_agent_registry import CreativeAgentRegistry
        from src.core.tools.creative_formats import ADVERTISED_CREATIVE_AGENT_CAPABILITIES

        assert not hasattr(CreativeAgentRegistry, "get_creative_delivery"), (
            "get_creative_delivery is now implemented — re-evaluate whether "
            "`delivery` should be added back to ADVERTISED_CREATIVE_AGENT_CAPABILITIES"
        )

        cap_values = {c.value for c in ADVERTISED_CREATIVE_AGENT_CAPABILITIES}
        assert "delivery" not in cap_values, (
            f"`delivery` advertised without a get_creative_delivery task (capabilities are commitments): {cap_values}"
        )
        assert cap_values == {"validation", "assembly", "preview"}, (
            f"Advertised capabilities must be exactly the backed set, got {cap_values}"
        )


class TestAgentInventedKeysNeverReachTheBuyer:
    """A creative agent's undeclared keys must not travel through this seller onto the wire.

    ``Format`` is built from a COUNTERPARTY's payload
    (``creative_agent_registry._fetch_formats_raw_mcp`` validates a creative agent's reply
    into it) and then serialized to buyers by ``list_creative_formats``. The GENERATED parent
    sets ``extra="allow"``, which stores an unknown key and then emits it -- so without the
    local class's ``extra="ignore"`` every key an agent invented appeared in this seller's
    response under the pinned ``Format`` name, where no buyer could validate it.

    Measured counterfactual, not a hypothesis: ``LegacyFormat.model_validate`` of the payload
    below retains the undeclared keys and the local class dumps two, which is what the second
    test here pins.

    This is also what makes DELETING an internal field mean what it says.
    ``iab_specification``, ``accepts_3p_tags`` and ``category`` were removed from ``Format``
    as unused; under the parent's "allow" an agent still sending one would have had it
    PROMOTED from a field that could never reach the wire (``exclude=True``) to an extra that
    always does.
    """

    _AGENT_PAYLOAD = {
        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
        "name": "Agent-supplied format",
        # The three deleted internal fields, plus an invented one.
        "iab_specification": "IAB-SOMETHING",
        "accepts_3p_tags": True,
        "category": "standard",
        "agent_invented_key": "should never be echoed",
    }

    def test_undeclared_keys_are_dropped_at_construction(self):
        fmt = Format.model_validate(self._AGENT_PAYLOAD)

        assert sorted(fmt.model_dump(exclude_none=True)) == ["format_id", "name"]

    def test_the_generated_parent_would_have_echoed_them(self):
        """The positive control: proves the assertion above is the local config's doing.

        Without it, a parent that happened to be strict would make the test above pass for a
        reason that has nothing to do with the ``model_config`` it exists to grade.
        """
        from adcp.types.legacy import LegacyFormat

        echoed = LegacyFormat.model_validate(self._AGENT_PAYLOAD).model_dump(exclude_none=True)

        assert "agent_invented_key" in echoed, (
            "the generated parent no longer retains unknown keys, so this seller's "
            "extra='ignore' may have become redundant -- re-read it before deleting"
        )

    def test_the_deleted_internal_fields_are_not_declared(self):
        for name in ("iab_specification", "accepts_3p_tags", "category"):
            assert name not in Format.model_fields
