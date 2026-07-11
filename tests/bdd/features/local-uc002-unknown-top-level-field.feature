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
# Every transport REJECTS, each at its own boundary layer (owner decision
# 2026-07-11, salesagent-cyz0 — shape differences accepted, no remap):
# REST -> INVALID_REQUEST envelope (pydantic extra=forbid via app handler);
# A2A -> VALIDATION_ERROR envelope (its boundary validator, names the field);
# MCP -> FastMCP tool-signature rejection, before the envelope builder can
# run (in dev mode mcp_compat_middleware deliberately does not strip —
# unknowns fail loudly).
Feature: UC-002 create_media_buy — unknown top-level request field (local, Pattern #7 gate)

  @T-UC-002-local-unknown-top-level-field @project-gate
  Scenario: create_media_buy request carries an unknown top-level field
    Given a valid create_media_buy request
    And the request body carries unknown top-level field "nonsense_field"
    When the Buyer Agent sends the create_media_buy request
    Then the unknown top-level field "nonsense_field" is rejected per the transport contract
