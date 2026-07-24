# Hand-authored feature — not compiled from adcp-req.
# AdCP 3.1.1 authority:
# - dist/schemas/3.1.1/protocol/get-adcp-capabilities-request.json (`protocols` enum + minItems: 1)
# - dist/compliance/3.1.1/universal/capability-discovery.yaml#get_capabilities_filtered
# The supported-only request is the published graded case. Invalid/empty inputs
# are schema-grounded. A valid protocol the seller does not implement is NOT an
# error: the graded step scopes the filter to "only the requested domain
# details", and the response schema describes each supported_protocols value as
# committing the agent to that protocol's compliance storyboard — so the field
# is this agent's declaration, never a projection of the buyer's question.

@UC-010 @capability-filter @adcp-3.1.1
Feature: UC-010 AdCP 3.1.1 capability protocol filtering
  Background:
    Given a tenant is resolvable from the request context
    And the tenant has full capabilities configured

  @T-UC-010-local-capability-filter-v311 @graded
  Scenario: The published media_buy filter is honored on the wire
    When the Buyer queries capabilities with protocols ["media_buy"] and context {"correlation_id": "capability_discovery--get_capabilities_filtered"}
    Then supported_protocols on the wire should equal ["media_buy"]
    And only requested protocol sections should be present on the wire
    And the response context should equal {"correlation_id": "capability_discovery--get_capabilities_filtered"}

  @T-UC-010-local-capability-filter-invalid-v311 @schema-grounded
  Scenario: An unknown protocol enum is rejected on the wire
    When the Buyer queries capabilities with protocols ["marketing"]
    Then the protocols filter should fail with a correctable VALIDATION_ERROR

  @T-UC-010-local-capability-filter-empty-v311 @schema-grounded
  Scenario: An explicitly empty protocols array is rejected on the wire
    When the Buyer queries capabilities with protocols []
    Then the protocols filter should fail with a correctable VALIDATION_ERROR

  @T-UC-010-local-capability-filter-unsupported-v311 @ungraded
  Scenario: A valid protocol the seller does not implement still declares the true set
    When the Buyer queries capabilities with protocols ["signals"]
    Then supported_protocols on the wire should equal ["media_buy"]
    And no protocol detail sections should be present on the wire
