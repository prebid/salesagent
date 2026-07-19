# Hand-authored feature — not compiled from adcp-req.
#
# Companion coverage for the release-resolution boundaries in the pinned AdCP
# published 3.1.0 Versioning & Governance prose used by the pinned 3.1.1 patch
# release (`dist/docs/3.1.0/building/...`).
# The generated BR-UC-010 feature covers the VERSION_UNSUPPORTED payload shape,
# but its derived schema scenarios do not exercise all normative resolution
# branches. These cases are ungraded by the pinned compliance storyboards, so
# this local companion locks them on the real MCP, A2A, and REST wire instead of
# appending repo-specific scenarios to a generated file.
#
# Companion files survive `python scripts/compile_bdd.py --merge`; see
# BR-UC-006-creatives-invariants.feature and BR-UC-011-account-validation.feature.

Feature: BR-UC-010 Version Negotiation (hand-authored companion)
  As a Buyer Agent
  I want unsupported release pins rejected consistently
  So that I can re-pin to a release the Seller Agent actually serves

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-010-v31-version-unsupported-cross-major @v31 @extension @ext-f @error @post-f4 @boundary @partition
  Scenario Outline: A release pin from a different major is unsupported - <pin>
    Given the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "<pin>"
    Then the response should be a VERSION_UNSUPPORTED error
    And the error details should include supported_versions as a non-empty array
    And the Buyer Agent must select the next adcp_version from supported_versions
    # Versioning & Governance, "Server resolves": a different major returns
    # VERSION_UNSUPPORTED with authoritative supported_versions in error data.

    Examples:
      | pin |
      | 4.0 |
      | 2.0 |

  @T-UC-010-v31-version-unsupported-sub-min @v31 @extension @ext-f @error @post-f4 @boundary
  Scenario: A stable same-major pin below every supported release is unsupported
    Given the seller speaks adcp release-precision versions "3.1", "3.2"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "3.0"
    Then the response should be a VERSION_UNSUPPORTED error
    And the error details should include supported_versions as a non-empty array
    And the Buyer Agent must select the next adcp_version from supported_versions
    # Versioning & Governance, "Server resolves": when no server release is less
    # than or equal to the buyer's same-major pin, the pin is a sub-min failure.

  @T-UC-010-v31-version-unsupported-prerelease @v31 @extension @ext-f @error @post-f4 @boundary
  Scenario: An unmatched prerelease pin is not range-resolved to a stable release
    Given the seller speaks adcp release-precision versions "3.0", "3.1"
    When the Buyer Agent calls get_adcp_capabilities MCP tool with adcp_version "3.1-beta"
    Then the response should be a VERSION_UNSUPPORTED error
    And the error details should include supported_versions as a non-empty array
    And the Buyer Agent must select the next adcp_version from supported_versions
    # Versioning & Governance, "Pre-release pins": prereleases match exactly and
    # MUST NOT downshift to a stable release when the prerelease is not advertised.
