# Hand-authored feature — not compiled from adcp-req
# Cross-cutting inbound version negotiation (salesagent-lfeo0, epic salesagent-i3a8d)
#
# WHY THIS IS NOT IN BR-UC-010. Negotiation used to live inside
# _get_adcp_capabilities_impl, and so did its only BDD coverage: UC-010's
# @T-UC-010-v31-version-unsupported family calls get_adcp_capabilities and nothing else.
# The coverage mirrored the bug. Every other tool ignored the buyer's pin, and no scenario
# could see it, because the one tool under test was the one tool that worked.
#
# The negotiation now runs at the boundary (src/core/tools/_boundary.py invoke()), which
# makes it a property of EVERY request rather than of one tool. So it is graded here on a
# tool that is not capabilities. UC-010 keeps its own scenarios: they grade the
# capabilities-side declaration (adcp.supported_versions in the body, build_version
# advisory), which is a different obligation from refusing a pin.
#
# get_products is the tool the pinned storyboard grades this on:
# compliance/universal/error-compliance.yaml, scenario version_negotiation, steps
# unsupported_major_version / unsupported_release_version / supported_major_version.
#
# NOTHING HERE IS CONFIGURED BY A PATCH. Every scenario runs against the release set this
# build actually advertises, so each one is true of the deployed seller and runs unchanged
# in-process and end-to-end. UC-010 has a Given that monkeypatches
# SUPPORTED_ADCP_VERSIONS; it is e2e_unsupported by its own declaration, and a scenario
# that cannot cross a real process boundary is not grading production. This feature does
# not use it.
#
# NOT graded here: recovery. The pinned enums/error-code.json puts VERSION_UNSUPPORTED at
# `correctable` in every published bundle through 3.2.0-rc.1, while the storyboard step's
# narrative `expected:` block says `fatal` — a value that is not in the recovery enum at
# all (core/error.json types it [transient, correctable, terminal]). The storyboard's own
# graded validations never check recovery. Reported upstream as
# adcontextprotocol/adcp#7375; this seller derives recovery from the pin and says so.
#
# NOT graded here, and deliberately: the rule that two INDIVIDUALLY-SUPPORTED pins naming
# different majors are refused (adcp_version "3.1" under adcp_major_version 4). The rule
# is in src/core/version_negotiation.py._is_unsupported, and it is unreachable while this
# seller speaks a single release, because a supported release and a supported major then
# always agree. Reaching it needs a seller advertising two majors, and there is no
# production surface that configures that — the advertised set is derived from the SDK pin
# at import. Grading it would mean patching a production constant, which is what the
# paragraph above rejects. It becomes writable, with a real Given, the day a second
# release is served.

@protocol @version-negotiation
Feature: Inbound AdCP version negotiation applies to every tool
  As a buyer agent,
  I want the seller to refuse a version pin it cannot serve, on whichever tool I call,
  so that I discover the mismatch on my first request rather than being served a
  response shaped by a release I did not ask for.

  # The catalog is seeded for EVERY scenario, refusals included. A seller with no products
  # answers an empty list, so a refusal graded against an empty catalog cannot tell
  # "negotiation refused this" from "there was nothing to return" — and the accept
  # scenarios would be satisfied by a response that never reached the tool.
  Background:
    Given a tenant is configured for product discovery
    And an inventory profile with only domain "example.com"
    And a product linked to that inventory profile with pricing

  # Storyboard: error_compliance::unsupported_major_version. adcp_major_version 99 is the
  # maximum LEGAL value (the pinned request schema types it Ge(1), Le(99)), chosen by the
  # spec so that a rejection is negotiation and never schema validation.
  @T-PROTOCOL-001-unsupported-major
  Scenario: A major this seller cannot serve is refused on a tool that is not capabilities
    When the buyer requests products pinning adcp_major_version 99
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  # Storyboard: error_compliance::unsupported_release_version. The release-precision
  # sibling. 3.1 promotes adcp_version to the primary wire field, so a seller validating
  # only the integer major would pass the scenario above and still refuse nothing a real
  # 3.1 buyer sends.
  @T-PROTOCOL-001-unsupported-release
  Scenario: A release this seller cannot serve is refused
    When the buyer requests products pinning adcp_version "99.0"
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  # Storyboard: error_compliance::supported_major_version. The guard against
  # over-rejecting — negotiation refuses a MISMATCHED pin, and declaring a supported one
  # explicitly is an ordinary request. Without this, "refuse everything" would pass the two
  # scenarios above. The pinned major is read off the seller's own advertisement rather
  # than typed here, so this cannot drift from what the build speaks.
  @T-PROTOCOL-001-supported-major
  Scenario: Explicitly pinning the major this seller advertises is an ordinary request
    When the buyer requests products pinning the seller's advertised major
    Then the response arrives
    And the buyer receives the seller's products

  # Each pin the buyer SENDS is a constraint; they are not alternatives. Read as
  # alternatives, this exact request was accepted and answered with products, because the
  # major check returned before the release was ever judged.
  @T-PROTOCOL-001-pins-are-independent
  Scenario: A supported major does not excuse an unsupported release
    When the buyer requests products pinning adcp_version "99.0" and the seller's advertised major
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  @T-PROTOCOL-001-pins-are-independent-mirror
  Scenario: A supported release does not excuse an unsupported major
    When the buyer requests products pinning the seller's advertised release and adcp_major_version 99
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  # The pin is a PRECONDITION, so nothing the request names may be looked up before it is
  # judged. Naming an account is itself a claim that needs a credential (#1417), which put
  # authentication and the account lookup ahead of a negotiation that was sitting beside the
  # outbound version stamp — and a buyer who pinned a release this seller cannot serve was
  # answered about their account instead. The account named here does not exist, so the
  # refusal can only read VERSION_UNSUPPORTED if the pin is judged before the account is
  # resolved. That is also the shape the storyboard sends: error_compliance's
  # unsupported_major_version and unsupported_release_version both carry an account.
  @T-PROTOCOL-001-pin-precedes-account
  Scenario: A request naming an account is refused on its pin, not on the account
    When the buyer requests products naming an unknown account and pinning adcp_major_version 99
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  # The release-precision sibling, for the same reason the two scenarios above are separate:
  # a seller validating only the integer major refuses nothing a real 3.1 buyer sends.
  @T-PROTOCOL-001-pin-precedes-account-release
  Scenario: A request naming an account is refused on its unsupported release
    When the buyer requests products naming an unknown account and pinning adcp_version "99.0"
    Then the error is compliant with the AdCP error spec
    And the response contains error code VERSION_UNSUPPORTED

  # error-details/version-unsupported.json declares supported_majors as a SHOULD-emit
  # integer array through 3.x. It was declared on this seller's details model and never
  # populated, so a buyer refused for a MAJOR was told only which RELEASES exist — not the
  # field they actually sent.
  @T-PROTOCOL-001-details-name-the-majors
  Scenario: The refusal names the majors a buyer rejected on a major can retry with
    When the buyer requests products pinning adcp_major_version 99
    Then the response contains error code VERSION_UNSUPPORTED
    And the error details should carry the majors this seller advertises
    And the error details should echo the rejected adcp_major_version 99

  # A request carrying no pin constrains nothing, which is what lets a buyer discover the
  # seller's set without first guessing it. The suggestion the pin ships for this code says
  # exactly that: "call get_adcp_capabilities without a version pin to discover
  # supported_versions".
  @T-PROTOCOL-001-no-pin
  Scenario: A request with no version pin is served normally
    When the buyer requests products
    Then the response arrives
    And the buyer receives the seller's products
