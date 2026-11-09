# Local retention policy: A2A 1.0 §3.3.2 permits TaskNotFound for purged tasks.
# AdCP v3.2.7 L0/a2a-profile-extension.mdx §Submitted AdCP work requires a
# completed A2A receipt with the independent AdCP task_id in its artifact.
# Native receipt retention is ungraded by the AdCP storyboards.
@a2a-task-ownership
Feature: Native A2A receipts finish with the invocation

  @T-LOCAL-a2a-task-purged
  Scenario Outline: Returned receipts cannot be retrieved or canceled
    Given an A2A invocation producing "<outcome>"
    When "owner" invokes the prepared AdCP skill
    Then the native receipt state is "<receipt_state>"
    And the receipt contains the AdCP "<outcome>" result
    When "<caller>" calls "<operation>" for the "existing" task
    Then the task is not found
    When "<caller>" calls "<operation>" for the "missing" task
    Then the refusal matches the inaccessible task

    Examples:
      | outcome   | receipt_state | caller       | operation  |
      | completed | completed     | owner        | GetTask    |
      | completed | completed     | owner        | CancelTask |
      | failed    | failed        | owner        | GetTask    |
      | failed    | failed        | owner        | CancelTask |
      | submitted | completed     | owner        | GetTask    |
      | submitted | completed     | owner        | CancelTask |
      | submitted | completed     | sibling      | GetTask    |
      | submitted | completed     | sibling      | CancelTask |
      | submitted | completed     | other-tenant | GetTask    |
      | submitted | completed     | other-tenant | CancelTask |
      | completed | completed     | anonymous    | GetTask    |
      | completed | completed     | anonymous    | CancelTask |
      | completed | completed     | invalid      | GetTask    |
      | completed | completed     | invalid      | CancelTask |

  @T-LOCAL-a2a-task-adcp-polling
  Scenario: Native cancellation cannot cancel the durable AdCP operation
    Given an A2A invocation producing "submitted"
    When "owner" invokes the prepared AdCP skill
    Then the native receipt state is "completed"
    And the receipt contains the AdCP "submitted" result
    And the AdCP task handle is distinct from the native receipt
    When "owner" calls "CancelTask" for the "existing" task
    Then the task is not found
    When the owner polls the durable AdCP task
    Then the receipt contains the AdCP "input-required" result
    And the durable AdCP task still has the submitted handle
