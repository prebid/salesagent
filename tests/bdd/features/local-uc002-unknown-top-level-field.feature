# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# adcp v3.1.1 create-media-buy request schema sets top-level
# additionalProperties: true — the SPEC allows unknown request fields, so
# production's extra=ignore is the spec-compliant contract. The dev/CI
# extra=forbid -> INVALID_REQUEST envelope graded here is the INTERNAL
# Pattern #7 drift gate (a project gate, NOT a spec obligation) — GH #1442.
# The generated BR-UC-002 unknown-field partitions grade NESTED objects
# (targeting_overlay) only; this file grades the TOP-LEVEL request body.
# Every transport REJECTS (the internal Pattern #7 dev-forbid gate) and names
# the offending field in the spec-canonical place. AdCP 3.1.1 core/error.json
# names the field in the `field` property (JSONPath-lite), NOT the free-form
# `message` — and all transports emit it identically (field="nonsense_field" on
# MCP, A2A, REST). The only accepted per-transport difference is the boundary
# `code` (owner decision 2026-07-11, salesagent-cyz0 — no remap): REST ->
# INVALID_REQUEST (pydantic extra=forbid handler); A2A boundary validator and
# MCP mcp_compat_middleware (#1534) -> VALIDATION_ERROR. Recovery=correctable on
# all. Message prose is not asserted (spec leaves it free-form).

Feature: UC-002 create_media_buy — unknown top-level request field (local, Pattern #7 gate)

  @T-UC-002-local-unknown-top-level-field @project-gate
  Scenario: create_media_buy request carries an unknown top-level field
    Given a valid create_media_buy request
    And the request body carries unknown top-level field "nonsense_field"
    When the Buyer Agent sends the create_media_buy request
    Then the unknown top-level field "nonsense_field" is rejected per the transport contract
