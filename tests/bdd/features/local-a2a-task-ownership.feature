# Local native A2A behavior, ungraded by the AdCP 3.1.1 storyboard.
# Authority: A2A 1.0 specification §3.3.2 (authentication and inaccessible tasks),
# §13.1 (authenticated task access). These operations have no MCP/AdCP REST equivalent.
# https://a2a-protocol.org/v1.0.0/specification/#332-error-handling
# Tenant/principal ownership is this seller's access policy for its transient handles.
# Durable storage and terminal-task cancellation semantics are separate work.
@a2a-task-ownership
Feature: Native A2A task handles belong to their creator

  @T-LOCAL-a2a-task-owner-read
  Scenario Outline: The owner can retrieve every retained outcome
    Given a "<state>" task created by "owner"
    When "owner" calls "GetTask" for the "existing" task
    Then the task state is "<state>"

    Examples:
      | state     |
      | completed |
      | submitted |
      | failed    |

  @T-LOCAL-a2a-task-owner-cancel
  Scenario: The owner can cancel a submitted task
    Given a "submitted" task created by "owner"
    When "owner" calls "CancelTask" for the "existing" task
    Then the task state is "canceled"
    When "owner" calls "GetTask" for the "existing" task
    Then the task state is "canceled"

  @T-LOCAL-a2a-task-signed-owner
  Scenario: A caller presenting a signature and bearer owns the submitted task
    Given a "submitted" task created by "signed-owner"
    When "signed-owner" calls "GetTask" for the "existing" task
    Then the task state is "submitted"
    When "sibling" calls "CancelTask" for the "existing" task
    Then the task is not found
    And the owner's task is unchanged
    When "signed-owner" calls "CancelTask" for the "existing" task
    Then the task state is "canceled"

  @T-LOCAL-a2a-task-signature-precheck
  Scenario Outline: Task access preserves signature prechecks
    Given a "submitted" task created by "signed-owner"
    When "malformed-signature" calls "<operation>" for the "existing" task
    Then task access fails with "request_signature_header_malformed"
    And the owner's task is unchanged

    Examples:
      | operation  |
      | GetTask    |
      | CancelTask |

  @T-LOCAL-a2a-task-foreign-owner
  Scenario Outline: Foreign tasks are indistinguishable from absent tasks
    Given a "submitted" task created by "owner"
    When "<caller>" calls "<operation>" for the "existing" task
    Then the task is not found
    And the owner's task is unchanged
    When "<caller>" calls "<operation>" for the "missing" task
    Then the refusal matches the inaccessible task

    Examples:
      | caller       | operation  |
      | sibling      | GetTask    |
      | sibling      | CancelTask |
      | other-tenant | GetTask    |
      | other-tenant | CancelTask |

  @T-LOCAL-a2a-task-authentication
  Scenario Outline: Authentication precedes task lookup
    Given a "submitted" task created by "owner"
    When "<caller>" calls "<operation>" for the "<target>" task
    Then task access fails with "<code>"
    And the owner's task is unchanged

    Examples:
      | caller    | operation  | target   | code         |
      | anonymous | GetTask    | existing | AUTH_MISSING |
      | anonymous | GetTask    | missing  | AUTH_MISSING |
      | anonymous | CancelTask | existing | AUTH_MISSING |
      | anonymous | CancelTask | missing  | AUTH_MISSING |
      | invalid   | GetTask    | existing | AUTH_INVALID |
      | invalid   | GetTask    | missing  | AUTH_INVALID |
      | invalid   | CancelTask | existing | AUTH_INVALID |
      | invalid   | CancelTask | missing  | AUTH_INVALID |

  @T-LOCAL-a2a-task-anonymous-owner
  Scenario Outline: An anonymous discovery task cannot be claimed by a later caller
    Given a "completed" task created by "anonymous"
    When "owner" calls "<operation>" for the "existing" task
    Then the task is not found
    When "owner" calls "<operation>" for the "missing" task
    Then the refusal matches the inaccessible task

    Examples:
      | operation  |
      | GetTask    |
      | CancelTask |
