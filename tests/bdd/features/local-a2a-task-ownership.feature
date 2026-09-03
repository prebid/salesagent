# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# Upstream gap: no storyboard scenario drives the A2A protocol methods
# `tasks/get` / `tasks/cancel` against a task another principal owns. That is
# the surface the in-memory ownership gate defends (#1702): a task_id is
# unguessable but not secret once known (webhook payloads, logs, support
# channels), so serving or cancelling an in-memory hit for anyone who learned
# the id leaks — and mutates — another buyer's task. The denial must be
# indistinguishable from an unknown id (no existence oracle). Reconcile
# upstream in adcp-req, then retire this file for the regenerated scenarios.
#
# Spec grounding (A2A Protocol v1.0 — this server builds routes with
# enable_v0_3_compat=True, so v0.3 is the natural misreading; v0.3's §3.3.2 is
# "gRPC Streaming" and it has no §13, so the version pin below matters):
# - v1.0 §3.3.2 — servers MUST NOT reveal existence of resources the client is
#   not authorized to access (example: "attempting to access a task created by
#   another user"); MUST return not-found when a resource "does not exist or
#   is not accessible"; SHOULD NOT distinguish the two.
# - v1.0 §13.1 — Get/Cancel MUST verify access, and authorization MUST occur
#   before any query that could leak existence (auth-first ordering).
# This altitude is upstream-ungraded by design: AdCP task-lifecycle places
# transport-native tasks/* outside AdCP polling. AdCP 3.1.1's get_products_async
# storyboard grades the analogue: get_products_task_status_wrong_account is the
# scenario that asserts REFERENCE_NOT_FOUND (verified in
# dist/compliance/3.1.1/domains/media-buy/scenarios/get_products_async.yaml).
# list_products_task_wrong_account is a related scenario in the same file but
# grades an empty task list (no expect_error) — not the REFERENCE_NOT_FOUND
# analogue; do not cite it as one.
#
# @a2a: these are protocol methods, not AdCP skills — there is no MCP or REST
# equivalent to parametrize across. The denial oracle grades the live v0.3
# compat JSON-RPC body captured through create_jsonrpc_routes
# (enable_v0_3_compat=True). Spec TaskNotFoundError code is -32001; today's
# compat path emits -32603 (system InternalError) — pinned here as the verified
# live wire. The live-server strict xfail in
# tests/e2e/test_a2a_endpoints_working.py asserts -32001 and flips when #1670
# closes the flattening. Not a two-layer AdCP envelope.
Feature: A2A in-memory task ownership for tasks/get and tasks/cancel (local)

  Background:
    Given an in-memory A2A task "task-owned-1" owned by the owning principal

  @T-A2A-TASK-OWNERSHIP-owner-get @a2a
  Scenario: The owner retrieves their own task via tasks/get
    When the "owner" calls tasks/get for task "task-owned-1"
    Then the A2A task response should carry task "task-owned-1" in state WORKING

  @T-A2A-TASK-OWNERSHIP-sibling-get @a2a
  Scenario: A sibling principal in the same tenant is denied via tasks/get
    When the "sibling" calls tasks/get for task "task-owned-1"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-owned-1"

  @T-A2A-TASK-OWNERSHIP-other-tenant-get @a2a
  Scenario: A principal in another tenant is denied via tasks/get
    When the "other_tenant" calls tasks/get for task "task-owned-1"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-owned-1"

  @T-A2A-TASK-OWNERSHIP-unknown-get @a2a
  Scenario: An unknown task id is denied in the same shape as an ownership miss
    When the "owner" calls tasks/get for task "task-never-created"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-never-created"

  @T-A2A-TASK-OWNERSHIP-sibling-cancel @a2a
  Scenario: A denied tasks/cancel does not mutate the owner's task
    When the "sibling" calls tasks/cancel for task "task-owned-1"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-owned-1"
    When the "owner" calls tasks/get for task "task-owned-1"
    Then the A2A task response should carry task "task-owned-1" in state WORKING

  @T-A2A-TASK-OWNERSHIP-other-tenant-cancel @a2a
  Scenario: A principal in another tenant is denied via tasks/cancel
    When the "other_tenant" calls tasks/cancel for task "task-owned-1"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-owned-1"
    When the "owner" calls tasks/get for task "task-owned-1"
    Then the A2A task response should carry task "task-owned-1" in state WORKING

  @T-A2A-TASK-OWNERSHIP-owner-cancel @a2a
  Scenario: The owner cancels their own task
    When the "owner" calls tasks/cancel for task "task-owned-1"
    Then the A2A task response should carry task "task-owned-1" in state CANCELED
    When the "owner" calls tasks/get for task "task-owned-1"
    Then the A2A task response should carry task "task-owned-1" in state CANCELED

  @T-A2A-TASK-OWNERSHIP-no-owner-get @a2a
  Scenario: A task present without an owner record is denied as not-found
    Given an in-memory A2A task "task-orphan-1" with no owner record
    When the "owner" calls tasks/get for task "task-orphan-1"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-orphan-1"

  @T-A2A-TASK-OWNERSHIP-no-owner-cancel @a2a
  Scenario: A task present without an owner record is denied and not mutated via tasks/cancel
    Given an in-memory A2A task "task-orphan-2" with no owner record
    When the "owner" calls tasks/cancel for task "task-orphan-2"
    Then the A2A task response should be a JSON-RPC task-not-found error for "task-orphan-2"
    And the stored task "task-orphan-2" should be in state WORKING

  @T-A2A-TASK-OWNERSHIP-unauth-get @a2a
  Scenario: An unauthenticated caller gets an auth failure, not task-not-found
    When an unauthenticated caller calls tasks/get for task "task-owned-1"
    Then the A2A task response should be an authentication failure, not task-not-found
