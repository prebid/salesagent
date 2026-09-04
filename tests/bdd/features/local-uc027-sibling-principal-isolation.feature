# Hand-authored feature — companion to BR-UC-027 (PR #1812 review).
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
# Upstream gap: BR-UC-027 grades cross-tenant REFERENCE_NOT_FOUND but has no
# same-tenant sibling-principal scenario. Pinned AdCP 3.1.1 REFERENCE_NOT_FOUND
# mandates uniform response for "exists but caller lacks access" as for
# "does not exist". Reconcile upstream in adcp-req, then retire this file.

Feature: UC-027 manage async tasks — sibling-principal isolation (local)

  @T-UC-027-local-sibling-get @sibling-principal @get-task @error @invariant
  Scenario: get_task — sibling principal sees same REFERENCE_NOT_FOUND as unknown task_id
    Given an owner principal and a sibling principal in the same tenant
    And the owner has a durable workflow task "task_sibling_get_001"
    When the owner principal invokes get_task for their task
    Then the wire returns the owner's task_id
    When the sibling principal invokes get_task for the owner's task
    And an unknown task_id is requested as the owner for the same tool
    Then the wire error is REFERENCE_NOT_FOUND matching an unknown task_id

  @T-UC-027-local-sibling-complete @sibling-principal @complete-task @error @invariant
  Scenario: complete_task — sibling principal sees same REFERENCE_NOT_FOUND as unknown task_id
    Given an owner principal and a sibling principal in the same tenant
    And the owner has a durable pending workflow task "task_sibling_complete_001"
    When the owner principal invokes complete_task for their pending task
    Then the wire returns the owner's task_id
    When the sibling principal invokes complete_task for the owner's task
    And an unknown task_id is requested as the owner for the same tool
    Then the wire error is REFERENCE_NOT_FOUND matching an unknown task_id
