# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-027 Manage Async Tasks
  As a Buyer or Seller
  I want to list, inspect, and complete asynchronous workflow tasks
  So that I can monitor task progress, review task details, and resolve pending tasks

  # Postconditions verified:
  #   POST-S1: Buyer has a filtered, sorted, paginated list of tasks matching query criteria
  #   POST-S2: Buyer knows the current status, type, AdCP protocol, timestamps, and progress of a specific task
  #   POST-S3: Buyer has completed a pending task and the task status is now completed or failed
  #   POST-S4: Application context from the request is echoed unchanged in the response
  #   POST-S5: Task completion is audit-logged with principal, timestamp, and status transition
  #   POST-S6: Buyer can page through task results using cursor-based pagination
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: When a task is not found, the error references the provided task_id
  #
  # Rules: BR-RULE-203..208 (6 rules, 24 invariants)
  # Extensions: A (Get Task Details), B (Complete Task), C (REFERENCE_NOT_FOUND),
  #   D (TASK_NOT_COMPLETABLE), E (COMPLETION_STATUS_INVALID), F (AUTH_REQUIRED),
  #   G (List-request Input Validation -- filters/sort/pagination),
  #   H (SUMMARY_INCONSISTENT -- response self-check)
  # Error codes: REFERENCE_NOT_FOUND, TASK_NOT_COMPLETABLE, COMPLETION_STATUS_INVALID,
  #   AUTH_REQUIRED, SORT_FIELD_INVALID, SORT_DIRECTION_INVALID,
  #   FILTER_ARRAY_EMPTY, FILTER_TASK_IDS_TOO_MANY, FILTER_DATE_INVALID_FORMAT,
  #   FILTER_VALUE_INVALID, SUMMARY_INCONSISTENT

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer Agent has an authenticated connection via MCP


  @T-UC-027-main-mcp @main-flow @list-tasks @mcp @happy-path @post-s1 @post-s4 @post-s6
  Scenario: List tasks via MCP -- no filters returns all tenant tasks with default sort
    Given the tenant has 15 workflow tasks across multiple domains and statuses
    When the Buyer Agent invokes list_tasks via MCP with no filters
    Then the response contains a tasks array with the tenant's tasks
    And the response contains query_summary with total_matching and returned counts
    And the query_summary includes domain_breakdown and status_breakdown
    And the query_summary shows sort_applied as "created_at" descending
    And the response contains pagination with has_more and cursor
    And the request context is echoed in the response
    # POST-S1: Buyer has filtered, sorted, paginated list of tasks
    # POST-S4: Context echoed unchanged
    # POST-S6: Cursor-based pagination available

  @T-UC-027-main-rest @main-flow @list-tasks @rest @happy-path @post-s1
  Scenario: List tasks via REST/A2A -- returns same schema as MCP path
    Given the tenant has workflow tasks
    When the Buyer Agent sends a tasks_list A2A request with no filters
    Then the response conforms to tasks-list-response.json schema
    And the response contains query_summary, tasks array, and pagination
    # POST-S1: Buyer has paginated list of tasks

  @T-UC-027-main-filtered @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- single status filter returns matching tasks only
    Given the tenant has tasks in statuses: submitted, working, completed, failed
    When the Buyer Agent invokes list_tasks with filter status "submitted"
    Then the response contains only tasks with status "submitted"
    And the query_summary shows filters_applied including "status"
    # POST-S1: Buyer has filtered list

  @T-UC-027-main-multi-filter @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- combined filters apply AND semantics across dimensions
    Given the tenant has tasks in domain "media-buy" with statuses submitted, working, completed
    And the tenant has tasks in domain "signals" with statuses submitted, failed
    When the Buyer Agent invokes list_tasks with filters protocol "media-buy" and statuses ["submitted", "working"]
    Then the response contains only tasks with domain "media-buy" and status "submitted" or "working"
    And the query_summary shows filters_applied including "protocol" and "statuses"
    # POST-S1: Combined filter dimensions with AND, OR within
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-main-date-range @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- date range filter returns tasks within time window
    Given the tenant has tasks created on various dates
    When the Buyer Agent invokes list_tasks with filters created_after "2026-01-01T00:00:00Z" and created_before "2026-01-31T23:59:59Z"
    Then the response contains only tasks created within January 2026
    And the query_summary shows filters_applied including "created_after" and "created_before"
    # POST-S1: Date range filtering

  @T-UC-027-main-task-ids @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- task_ids filter returns specific tasks by ID
    Given the tenant has tasks "task_001", "task_002", "task_003"
    When the Buyer Agent invokes list_tasks with filter task_ids ["task_001", "task_003"]
    Then the response contains exactly tasks "task_001" and "task_003"
    # POST-S1: Specific task ID filtering

  @T-UC-027-main-context-search @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- context_contains filter searches context text
    Given the tenant has tasks with context containing "nike_q1_2025" and others without
    When the Buyer Agent invokes list_tasks with filter context_contains "nike_q1_2025"
    Then the response contains only tasks whose context matches "nike_q1_2025"
    # POST-S1: Context text search

  @T-UC-027-main-sorted @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- explicit sort by updated_at ascending
    Given the tenant has tasks with various updated_at timestamps
    When the Buyer Agent invokes list_tasks with sort field "updated_at" direction "asc"
    Then the response contains tasks ordered by updated_at ascending
    And the query_summary shows sort_applied as "updated_at" ascending
    # POST-S1: Explicit sort order

  @T-UC-027-main-pagination @main-flow @list-tasks @happy-path @post-s6
  Scenario: List tasks -- pagination returns first page with cursor for next
    Given the tenant has 75 workflow tasks
    When the Buyer Agent invokes list_tasks with max_results 20
    Then the response contains exactly 20 tasks
    And the pagination shows has_more as true with a cursor value
    And the query_summary shows total_matching as 75 and returned as 20
    # POST-S6: Cursor-based pagination

  @T-UC-027-main-pagination-next @main-flow @list-tasks @happy-path @post-s6
  Scenario: List tasks -- follow cursor to get next page
    Given the tenant has 75 workflow tasks
    And the Buyer Agent received a cursor from the first page of 20
    When the Buyer Agent invokes list_tasks with cursor from previous response and max_results 20
    Then the response contains the next 20 tasks
    And no tasks overlap with the first page
    # POST-S6: Cursor-based pagination continuation

  @T-UC-027-main-empty @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- no matching tasks returns empty array with zero summary
    Given the tenant has no workflow tasks
    When the Buyer Agent invokes list_tasks
    Then the response contains an empty tasks array
    And the query_summary shows total_matching as 0 and returned as 0
    And the query_summary domain_breakdown and status_breakdown are empty
    # POST-S1: Empty result set handled gracefully
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-main-include-history @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- include_history flag includes conversation history
    Given the tenant has tasks with conversation history
    When the Buyer Agent invokes list_tasks with include_history true
    Then each task in the response includes conversation history
    # POST-S1: History inclusion in list

  @T-UC-027-ext-a-get @extension @ext-a @get-task @happy-path @post-s2 @post-s4
  Scenario: Get task details -- returns full task information
    Given the tenant has a task "task_abc_123" of type "create_media_buy" in status "working"
    When the Buyer Agent invokes get_task with task_id "task_abc_123"
    Then the response contains task_id "task_abc_123"
    And the response contains task_type, protocol, status, created_at, and updated_at
    And the request context is echoed in the response
    # POST-S2: Buyer knows status, type, AdCP protocol, timestamps
    # POST-S4: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-a-progress @extension @ext-a @get-task @happy-path @post-s2
  Scenario: Get task details -- in-progress task includes progress information
    Given the tenant has a task "task_prog_001" in status "working" with progress 60%
    When the Buyer Agent invokes get_task with task_id "task_prog_001"
    Then the response includes progress with percentage, current_step, total_steps
    # POST-S2: Buyer knows progress of specific task

  @T-UC-027-ext-a-failed @extension @ext-a @get-task @happy-path @post-s2
  Scenario: Get task details -- failed task includes error details
    Given the tenant has a task "task_err_001" in status "failed" with error code and message
    When the Buyer Agent invokes get_task with task_id "task_err_001"
    Then the response includes error section with code, message, and details
    # POST-S2: Buyer knows error details for failed task

  @T-UC-027-ext-a-history @extension @ext-a @get-task @happy-path @post-s2
  Scenario: Get task details -- include_history returns conversation history
    Given the tenant has a task "task_hist_001" with conversation history entries
    When the Buyer Agent invokes get_task with task_id "task_hist_001" and include_history true
    Then the response includes conversation history array
    # POST-S2: Buyer knows full conversation history

  @T-UC-027-ext-a-objects @extension @ext-a @get-task @happy-path @post-s2
  Scenario: Get task details -- includes associated object mappings
    Given the tenant has a task "task_obj_001" with associated objects (media_buy, product)
    When the Buyer Agent invokes get_task with task_id "task_obj_001"
    Then the response includes associated_objects with object_type, object_id, and action
    # POST-S2: Buyer knows associated objects

  @T-UC-027-ext-b-completed @extension @ext-b @complete-task @happy-path @post-s3 @post-s5
  Scenario: Complete task -- mark pending task as completed
    Given the tenant has a task "task_pending_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_pending_001" and status "completed"
    Then the response confirms task_id "task_pending_001" with status "completed"
    And the response includes completed_at timestamp in ISO 8601 format
    And the response includes completed_by with the principal identity
    And an audit log entry is written for operation "complete_task"
    # POST-S3: Task is now completed
    # POST-S5: Audit-logged with principal, timestamp, and status transition

  @T-UC-027-ext-b-failed @extension @ext-b @complete-task @happy-path @post-s3 @post-s5
  Scenario: Complete task -- mark in_progress task as failed with error message
    Given the tenant has a task "task_ip_001" in status "in_progress"
    When the Buyer Agent invokes complete_task with task_id "task_ip_001" and status "failed" and error_message "Manual intervention failed"
    Then the response confirms task_id "task_ip_001" with status "failed"
    And the response includes completed_at timestamp
    And an audit log entry is written recording the transition from "in_progress" to "failed"
    # POST-S3: Task is now failed
    # POST-S5: Audit captures status transition

  @T-UC-027-ext-b-requires-approval @extension @ext-b @complete-task @happy-path @post-s3
  Scenario: Complete task -- mark requires_approval task as completed
    Given the tenant has a task "task_ra_001" in status "requires_approval"
    When the Buyer Agent invokes complete_task with task_id "task_ra_001" and status "completed"
    Then the response confirms task_id "task_ra_001" with status "completed"
    And the response includes completed_at timestamp
    # POST-S3: Approval workflow resolved

  @T-UC-027-ext-b-default-status @extension @ext-b @complete-task @happy-path @post-s3
  Scenario: Complete task -- status omitted defaults to completed
    Given the tenant has a task "task_default_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_default_001" and no status parameter
    Then the response confirms task_id "task_default_001" with status "completed"
    And the response includes default response_data with manually_completed and completed_by
    # POST-S3: Default status applied

  @T-UC-027-ext-b-response-data @extension @ext-b @complete-task @happy-path @post-s3
  Scenario: Complete task -- completed with custom response_data
    Given the tenant has a task "task_custom_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_custom_001" and status "completed" and response_data {"approval_notes": "Approved by finance"}
    Then the response confirms task_id "task_custom_001" with status "completed"
    And the stored response_data includes the provided custom data
    # POST-S3: Custom response data stored

  @T-UC-027-ext-b-failed-default-msg @extension @ext-b @complete-task @happy-path @post-s3
  Scenario: Complete task -- failed without error_message uses default
    Given the tenant has a task "task_faildef_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_faildef_001" and status "failed" and no error_message
    Then the response confirms status "failed"
    And the stored error_message is "Task marked as failed manually"
    # POST-S3: Default error message applied

  @T-UC-027-ext-c-get @extension @ext-c @error @post-f1 @post-f2 @post-f3 @post-f4
  Scenario: REFERENCE_NOT_FOUND -- get_task with nonexistent task_id
    Given the tenant has no task with id "task_nonexistent_999"
    When the Buyer Agent invokes get_task with task_id "task_nonexistent_999"
    Then the operation should fail with error code "REFERENCE_NOT_FOUND"
    And the error code should be "REFERENCE_NOT_FOUND"
    And the error message should reference task_id "task_nonexistent_999"
    And the error should include "suggestion" field
    And the suggestion should contain "Verify the task_id"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows REFERENCE_NOT_FOUND
    # POST-F3: Context echoed
    # POST-F4: Error references provided task_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-c-complete @extension @ext-c @error @post-f1 @post-f2 @post-f4
  Scenario: REFERENCE_NOT_FOUND -- complete_task with nonexistent task_id
    Given the tenant has no task with id "task_ghost_001"
    When the Buyer Agent invokes complete_task with task_id "task_ghost_001" and status "completed"
    Then the operation should fail with error code "REFERENCE_NOT_FOUND"
    And the error code should be "REFERENCE_NOT_FOUND"
    And the error message should reference task_id "task_ghost_001"
    And the error should include "suggestion" field
    And the suggestion should contain "Verify the task_id"
    # POST-F1: System state unchanged, no task modified
    # POST-F2: Buyer knows REFERENCE_NOT_FOUND
    # POST-F4: Error references task_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-c-other-tenant @extension @ext-c @error @post-f1 @post-f2 @post-f4
  Scenario: REFERENCE_NOT_FOUND -- task exists but belongs to different tenant
    Given tenant "tenant_a" has a task "task_cross_001"
    And the authenticated buyer belongs to "tenant_b"
    When the Buyer Agent invokes get_task with task_id "task_cross_001"
    Then the operation should fail with error code "REFERENCE_NOT_FOUND"
    And the error code should be "REFERENCE_NOT_FOUND"
    And the error message should reference task_id "task_cross_001"
    And the error should include "suggestion" field
    And the suggestion should contain "belongs to the current tenant"
    # POST-F1: Tenant isolation enforced
    # POST-F2: No leakage of cross-tenant information
    # POST-F4: Error references provided task_id

  @T-UC-027-ext-d-completed @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: TASK_NOT_COMPLETABLE -- task already completed
    Given the tenant has a task "task_done_001" in status "completed"
    When the Buyer Agent invokes complete_task with task_id "task_done_001" and status "completed"
    Then the operation should fail with error code "TASK_NOT_COMPLETABLE"
    And the error code should be "TASK_NOT_COMPLETABLE"
    And the error message should indicate the task is already in status "completed"
    And the error should include "suggestion" field
    And the suggestion should contain "pending, in_progress, or requires_approval"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows task is already terminal
    # POST-F3: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-d-failed @extension @ext-d @error @post-f1 @post-f2
  Scenario: TASK_NOT_COMPLETABLE -- task already failed
    Given the tenant has a task "task_failed_001" in status "failed"
    When the Buyer Agent invokes complete_task with task_id "task_failed_001" and status "completed"
    Then the operation should fail with error code "TASK_NOT_COMPLETABLE"
    And the error code should be "TASK_NOT_COMPLETABLE"
    And the error message should indicate the task is already in status "failed"
    And the error should include "suggestion" field
    And the suggestion should contain "pending, in_progress, or requires_approval"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows task is already terminal

  @T-UC-027-ext-e-non-terminal @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: COMPLETION_STATUS_INVALID -- non-terminal status value
    Given the tenant has a task "task_val_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_val_001" and status "pending"
    Then the operation should fail with error code "COMPLETION_STATUS_INVALID"
    And the error code should be "COMPLETION_STATUS_INVALID"
    And the error message should indicate "pending" is invalid
    And the error should include "suggestion" field
    And the suggestion should contain "'completed' or 'failed'"
    And the request context is echoed in the response
    # POST-F1: System state unchanged (validation before task lookup)
    # POST-F2: Buyer knows the invalid status value
    # POST-F3: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-e-canceled @extension @ext-e @error @post-f1 @post-f2
  Scenario: COMPLETION_STATUS_INVALID -- terminal but disallowed status
    Given the tenant has a task "task_val_002" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_val_002" and status "canceled"
    Then the operation should fail with error code "COMPLETION_STATUS_INVALID"
    And the error code should be "COMPLETION_STATUS_INVALID"
    And the error message should indicate "canceled" is invalid
    And the error should include "suggestion" field
    And the suggestion should contain "'completed' or 'failed'"
    # POST-F1: Canceled not allowed via complete_task
    # POST-F2: Buyer knows valid options
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-e-unknown @extension @ext-e @error @post-f1 @post-f2
  Scenario: COMPLETION_STATUS_INVALID -- arbitrary unrecognized status
    Given the tenant has a task "task_val_003" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_val_003" and status "approved"
    Then the operation should fail with error code "COMPLETION_STATUS_INVALID"
    And the error code should be "COMPLETION_STATUS_INVALID"
    And the error message should indicate "approved" is invalid
    And the error should include "suggestion" field
    And the suggestion should contain "'completed' or 'failed'"
    # POST-F1: Unknown values rejected
    # POST-F2: Clear error guidance

  @T-UC-027-ext-f-list @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: AUTH_REQUIRED -- list_tasks without authentication
    Given the Buyer has no authentication credentials
    When the Buyer Agent invokes list_tasks
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error code should be "AUTH_REQUIRED"
    And the error message should contain "tenant context"
    And the error should include "suggestion" field
    And the suggestion should contain "x-adcp-auth token"
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows auth is required
    # POST-F3: Guidance on required headers
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-f-get @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: AUTH_REQUIRED -- get_task without authentication
    Given the Buyer has no authentication credentials
    When the Buyer Agent invokes get_task with task_id "task_any_001"
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error code should be "AUTH_REQUIRED"
    And the error message should contain "tenant context"
    And the error should include "suggestion" field
    And the suggestion should contain "x-adcp-auth token"
    # POST-F1: System state unchanged
    # POST-F2: Auth required for task queries
    # POST-F3: Recovery guidance
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-f-complete @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: AUTH_REQUIRED -- complete_task without authentication
    Given the Buyer has no authentication credentials
    When the Buyer Agent invokes complete_task with task_id "task_any_002" and status "completed"
    Then the operation should fail with error code "AUTH_REQUIRED"
    And the error code should be "AUTH_REQUIRED"
    And the error message should contain "tenant context"
    And the error should include "suggestion" field
    And the suggestion should contain "x-adcp-auth token"
    # POST-F1: System state unchanged
    # POST-F2: Auth required for task completion
    # POST-F3: Recovery guidance

  @T-UC-027-partition-lifecycle @partition @completion_lifecycle
  Scenario Outline: Task completion lifecycle -- <partition>
    Given the tenant has a task "task_lc_001" in the <task_state> state
    When the Buyer Agent invokes complete_task with task_id "task_lc_001" and status "completed"
    Then <outcome>

    Examples: Valid partitions (completable states)
      | partition                | task_state          | outcome                                                              |
      | pending_task             | pending             | the task transitions to completed                                    |
      | in_progress_task         | in_progress         | the task transitions to completed                                    |
      | requires_approval_task   | requires_approval   | the task transitions to completed                                    |

    Examples: Invalid partitions (terminal states and not found)
      | partition                | task_state     | outcome                                                                                       |
      | already_completed        | completed      | the operation fails with error code "TASK_NOT_COMPLETABLE" and suggestion about completable states |
      | already_failed           | failed         | the operation fails with error code "TASK_NOT_COMPLETABLE" and suggestion about completable states |
      | task_not_found           | nonexistent    | the operation fails with error code "REFERENCE_NOT_FOUND" and suggestion to verify task_id             |

  @T-UC-027-partition-status @partition @completion_status
  Scenario Outline: Completion status validation -- <partition>
    Given the tenant has a task "task_sv_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_sv_001" and status <status_value>
    Then <outcome>

    Examples: Valid partitions
      | partition         | status_value  | outcome                                           |
      | status_completed  | "completed"   | the task transitions to completed                 |
      | status_failed     | "failed"      | the task transitions to failed                    |
      | status_omitted    | (omitted)     | the task transitions to completed (default)       |

    Examples: Invalid partitions
      | partition              | status_value  | outcome                                                                                       |
      | non_terminal_status    | "pending"     | the operation fails with error code "COMPLETION_STATUS_INVALID" and suggestion about valid values  |
      | other_terminal_status  | "canceled"    | the operation fails with error code "COMPLETION_STATUS_INVALID" and suggestion about valid values  |
      | unknown_status         | "approved"    | the operation fails with error code "COMPLETION_STATUS_INVALID" and suggestion about valid values  |

  @T-UC-027-partition-filtering @partition @list_filtering
  Scenario Outline: Task list filtering -- <partition>
    Given the tenant has workflow tasks across multiple domains, statuses, and types
    When the Buyer Agent invokes list_tasks with <filter_config>
    Then <outcome>

    Examples: Valid partitions
      | partition         | filter_config                                                        | outcome                                                   |
      | no_filters        | no filters                                                           | all tenant tasks are returned                             |
      | single_status     | filter status "submitted"                                            | only submitted tasks returned                             |
      | multi_status      | filter statuses ["submitted", "working"]                             | tasks in submitted or working status returned             |
      | date_range        | filter created_after "2026-01-01T00:00:00Z" and created_before "2026-01-31T23:59:59Z" | tasks within date range returned          |
      | task_ids_filter   | filter task_ids ["task_001", "task_002"]                             | specific tasks returned by ID                             |
      | context_search    | filter context_contains "nike_q1_2025"                               | tasks with matching context returned                      |
      | combined_filters  | filter protocol "media-buy" and statuses ["submitted", "failed"]     | media-buy tasks in submitted or failed status returned    |
      | protocol_filter   | filter protocol "media-buy"                                           | only media-buy adcp-protocol tasks returned (v3.1)        |

    Examples: Invalid partitions
      | partition              | filter_config                                       | outcome                                                                                         |
      | empty_multi_value      | filter statuses []                                  | the operation fails with error code "FILTER_ARRAY_EMPTY" and suggestion to provide at least one value   |
      | task_ids_exceeds_max   | filter task_ids with 101 items                      | the operation fails with error code "FILTER_TASK_IDS_TOO_MANY" and suggestion to reduce count          |
      | invalid_date_format    | filter created_after "2025/01/01"                   | the operation fails with error code "FILTER_DATE_INVALID_FORMAT" and suggestion about ISO 8601 format  |
      | invalid_enum_value     | filter status "nonexistent_status"                  | the operation fails with error code "FILTER_VALUE_INVALID" and suggestion to check enum values          |

  @T-UC-027-partition-sort @partition @sort_validation
  Scenario Outline: Task list sort validation -- <partition>
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with <sort_config>
    Then <outcome>

    Examples: Valid partitions
      | partition            | sort_config                                    | outcome                                                    |
      | sort_omitted         | no sort specified                               | tasks sorted by created_at descending (default)            |
      | sort_by_created_at   | sort field "created_at" direction "asc"         | tasks sorted by created_at ascending                       |
      | sort_by_updated_at   | sort field "updated_at" direction "desc"        | tasks sorted by updated_at descending                      |
      | sort_by_status       | sort field "status" direction "asc"             | tasks sorted by status ascending                           |
      | sort_by_task_type    | sort field "task_type" direction "asc"          | tasks sorted by task_type ascending                        |
      | sort_by_protocol     | sort field "protocol" direction "desc"          | tasks sorted by adcp-protocol descending (v3.1)            |

    Examples: Invalid partitions
      | partition                | sort_config                                      | outcome                                                                                      |
      | invalid_sort_field       | sort field "priority" direction "asc"            | the operation fails with error code "SORT_FIELD_INVALID" and suggestion about supported fields    |
      | invalid_sort_direction   | sort field "created_at" direction "ascending"    | the operation fails with error code "SORT_DIRECTION_INVALID" and suggestion about asc or desc     |

  @T-UC-027-partition-audit @partition @audit_logging
  Scenario Outline: Task completion audit logging -- <partition>
    Given the tenant has a task "task_aud_001" in status "pending"
    And <principal_context>
    When the Buyer Agent invokes complete_task with task_id "task_aud_001" and status <target_status>
    Then <outcome>

    Examples: Valid partitions (audit entry written)
      | partition                             | principal_context                      | target_status | outcome                                                                       |
      | successful_completion_with_principal  | the principal identity is "user_123"   | "completed"   | an audit log entry is written with principal "user_123" and status "completed" |
      | successful_failure_marking            | the principal identity is "user_456"   | "failed"      | an audit log entry is written with principal "user_456" and status "failed"    |
      | unknown_principal                     | the principal identity is not available | "completed"  | an audit log entry is written with principal "unknown" and status "completed"  |

    Examples: Invalid partitions (no audit entry)
      | partition                       | principal_context                     | target_status      | outcome                                                  |
      | audit_on_rejected_completion    | the principal identity is "user_789"  | "canceled"         | the operation fails and no audit log entry is written    |

  @T-UC-027-partition-summary @partition @summary_construction
  Scenario Outline: Task query summary construction -- <partition>
    Given <setup>
    When the Buyer Agent invokes list_tasks with <query_config>
    Then <outcome>

    Examples: Valid partitions
      | partition              | setup                                                  | query_config                     | outcome                                                                                |
      | non_empty_results      | the tenant has 42 tasks (30 media-buy, 12 signals)     | no filters                       | query_summary shows total_matching 42 with domain and status breakdowns                |
      | empty_results          | the tenant has no tasks                                | no filters                       | query_summary shows total_matching 0, returned 0, empty breakdowns                     |
      | with_filters_applied   | the tenant has tasks in multiple statuses               | filter status "submitted"        | query_summary includes filters_applied listing "status"                                 |
      | with_sort_applied      | the tenant has tasks                                   | sort field "updated_at" direction "desc" | query_summary includes sort_applied showing updated_at desc                      |
      | single_page_results    | the tenant has 5 tasks                                 | max_results 50                   | query_summary shows total_matching 5 and returned 5                                    |

    Examples: Invalid partitions
      | partition              | setup                                                  | query_config                     | outcome                                                                                                 |
      | returned_exceeds_total | system constructs summary with returned > total        | (system error scenario)          | system error "SUMMARY_INCONSISTENT" indicating returned cannot exceed total_matching with suggestion     |

  @T-UC-027-boundary-lifecycle @boundary @completion_lifecycle
  Scenario Outline: Completion lifecycle boundary -- <boundary_point>
    Given <setup>
    When the Buyer Agent invokes complete_task with task_id "task_bnd_001" and status "completed"
    Then <outcome>

    Examples: Boundary values
      | boundary_point                      | setup                                                         | outcome                                                                                  |
      | task in pending state               | the tenant has a task "task_bnd_001" in status "pending"      | the task transitions to completed                                                        |
      | task in in_progress state           | the tenant has a task "task_bnd_001" in status "in_progress"  | the task transitions to completed                                                        |
      | task in requires_approval state     | the tenant has a task "task_bnd_001" in status "requires_approval" | the task transitions to completed                                                   |
      | task in completed state             | the tenant has a task "task_bnd_001" in status "completed"    | the operation fails with "TASK_NOT_COMPLETABLE" and suggestion about completable states  |
      | task in failed state                | the tenant has a task "task_bnd_001" in status "failed"       | the operation fails with "TASK_NOT_COMPLETABLE" and suggestion about completable states  |
      | task_id does not exist              | the tenant has no task with id "task_bnd_001"                 | the operation fails with "REFERENCE_NOT_FOUND" and suggestion to verify task_id               |

  @T-UC-027-boundary-status @boundary @completion_status
  Scenario Outline: Completion status boundary -- <boundary_point>
    Given the tenant has a task "task_bst_001" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_bst_001" and status <status_value>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                      | status_value  | outcome                                                                                       |
      | status = 'completed'                                                | "completed"   | the task transitions to completed                                                             |
      | status = 'failed'                                                   | "failed"      | the task transitions to failed                                                                |
      | status omitted (default to completed)                               | (omitted)     | the task transitions to completed via default                                                 |
      | status = 'pending' (valid task-status but not completion target)    | "pending"     | the operation fails with "COMPLETION_STATUS_INVALID" and suggestion about valid values        |
      | status = 'canceled' (terminal but not allowed via complete_task)    | "canceled"    | the operation fails with "COMPLETION_STATUS_INVALID" and suggestion about valid values        |
      | status = 'approved' (not a recognized value)                        | "approved"    | the operation fails with "COMPLETION_STATUS_INVALID" and suggestion about valid values        |

  @T-UC-027-boundary-filtering @boundary @list_filtering
  Scenario Outline: List filtering boundary -- <boundary_point>
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with <filter_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                      | filter_config                                          | outcome                                                                                   |
      | filters object absent (no filtering)                | no filters                                             | all tenant tasks returned                                                                 |
      | filters object empty {}                             | empty filters object                                   | all tenant tasks returned                                                                 |
      | single status filter                                | filter status "submitted"                              | only submitted tasks returned                                                             |
      | statuses array with 1 item (minItems boundary)      | filter statuses ["submitted"]                          | only submitted tasks returned                                                             |
      | statuses array with 0 items                         | filter statuses []                                     | operation fails with "FILTER_ARRAY_EMPTY" and suggestion to provide at least one value    |
      | task_ids with 100 items (maxItems boundary)         | filter task_ids with exactly 100 items                 | specific tasks returned by ID                                                             |
      | task_ids with 101 items (exceeds maxItems)          | filter task_ids with 101 items                         | operation fails with "FILTER_TASK_IDS_TOO_MANY" and suggestion to reduce count            |
      | date filter with valid ISO 8601                     | filter created_after "2026-01-01T00:00:00Z"            | tasks created after date returned                                                         |
      | date filter with non-ISO format                     | filter created_after "2025/01/01"                      | operation fails with "FILTER_DATE_INVALID_FORMAT" and suggestion about ISO 8601           |
      | all filter dimensions combined                      | filter protocol "media-buy" statuses ["submitted"] created_after "2026-01-01T00:00:00Z" | matching tasks returned with AND semantics |

  @T-UC-027-boundary-sort @boundary @sort_validation
  Scenario Outline: Sort validation boundary -- <boundary_point>
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with <sort_config>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                         | sort_config                                    | outcome                                                                                   |
      | sort omitted (defaults to created_at desc)             | no sort specified                              | tasks sorted by created_at descending                                                     |
      | sort field = 'created_at'                              | sort field "created_at" direction "asc"        | tasks sorted by created_at ascending                                                      |
      | sort field = 'protocol' (v3.1 last enum value; replaces pre-v3.1 `domain`) | sort field "protocol" direction "desc"     | tasks sorted by adcp-protocol descending                                                  |
      | sort field = 'priority' (not in enum)                  | sort field "priority" direction "asc"          | operation fails with "SORT_FIELD_INVALID" and suggestion about supported fields            |
      | sort direction = 'asc'                                 | sort field "created_at" direction "asc"        | tasks sorted by created_at ascending                                                      |
      | sort direction = 'ascending' (not in enum)             | sort field "created_at" direction "ascending"  | operation fails with "SORT_DIRECTION_INVALID" and suggestion about asc or desc             |

  @T-UC-027-boundary-audit @boundary @audit_logging
  Scenario Outline: Audit logging boundary -- <boundary_point>
    Given the tenant has a task "task_abnd_001" in status "pending"
    And <principal_context>
    When the Buyer Agent invokes complete_task with task_id "task_abnd_001" and status "completed"
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                               | principal_context                        | outcome                                                                      |
      | successful completion with known principal                   | the principal identity is "user_known"   | audit entry written with principal_id "user_known"                           |
      | successful failure-marking with known principal              | the principal identity is "user_mark"    | audit entry written with principal_id "user_mark" for status "failed"        |
      | successful completion with unknown principal (fallback)      | the principal identity is not available  | audit entry written with principal_id "unknown"                              |
      | completion rejected (task not found) — no audit              | the principal identity is "user_noaudit" | operation fails and no audit entry is written                                |

  @T-UC-027-boundary-summary @boundary @summary_construction
  Scenario Outline: Query summary boundary -- <boundary_point>
    Given <setup>
    When the Buyer Agent invokes list_tasks
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                    | setup                                                      | outcome                                                              |
      | total_matching = 0, returned = 0 (empty result set)              | the tenant has no tasks                                    | query_summary shows total_matching 0 and returned 0                  |
      | returned = total_matching (single page, all results)             | the tenant has 5 tasks and max_results >= 5                | query_summary shows returned equals total_matching                   |
      | returned < total_matching (multi-page result set)                | the tenant has 75 tasks and max_results is 20              | query_summary shows returned 20 and total_matching 75                |
      | filters_applied is empty array (no filters)                      | the tenant has tasks and no filters applied                | query_summary shows empty filters_applied                            |
      | returned > total_matching (invariant violation)                   | (system error condition)                                   | system error "SUMMARY_INCONSISTENT" with suggestion about system error |

  @T-UC-027-inv-203-1-holds @invariant @BR-RULE-203
  Scenario: BR-RULE-203 INV-1 holds -- completable task transitions to terminal state
    Given the tenant has a task "task_inv1_001" in status "requires_approval"
    When the Buyer Agent invokes complete_task with task_id "task_inv1_001" and status "completed"
    Then the task transitions to status "completed"
    And the completed_at timestamp is set to current UTC time
    # INV-1: Task in completable state allows completion
    # INV-4: completed_at set on success

  @T-UC-027-inv-203-2-violated @invariant @BR-RULE-203 @error
  Scenario: BR-RULE-203 INV-2 violated -- terminal task cannot be completed again
    Given the tenant has a task "task_inv2_001" in status "completed"
    When the Buyer Agent invokes complete_task with task_id "task_inv2_001" and status "failed"
    Then the operation should fail with error code "TASK_NOT_COMPLETABLE"
    And the error code should be "TASK_NOT_COMPLETABLE"
    And the error should include "suggestion" field
    And the suggestion should contain "pending, in_progress, or requires_approval"
    # INV-2: Terminal state rejects completion
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-203-3-violated @invariant @BR-RULE-203 @error
  Scenario: BR-RULE-203 INV-3 violated -- nonexistent task_id rejected
    Given the tenant has no task with id "task_inv3_001"
    When the Buyer Agent invokes complete_task with task_id "task_inv3_001"
    Then the operation should fail with error code "REFERENCE_NOT_FOUND"
    And the error code should be "REFERENCE_NOT_FOUND"
    And the error should include "suggestion" field
    And the suggestion should contain "Verify the task_id"
    # INV-3: Task not found within tenant scope
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-203-4-holds @invariant @BR-RULE-203
  Scenario: BR-RULE-203 INV-4 holds -- completed_at set on successful completion
    Given the tenant has a task "task_inv4_001" in status "pending" with no completed_at
    When the Buyer Agent invokes complete_task with task_id "task_inv4_001" and status "completed"
    Then the task completed_at is set to a UTC timestamp within the last minute
    # INV-4: completed_at timestamp set on success

  @T-UC-027-inv-204-1-holds @invariant @BR-RULE-204
  Scenario: BR-RULE-204 INV-1 holds -- valid completion status accepted
    Given the tenant has a task "task_inv204_1" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_inv204_1" and status "failed"
    Then the task transitions to status "failed"
    # INV-1: "completed" or "failed" accepted

  @T-UC-027-inv-204-2-holds @invariant @BR-RULE-204
  Scenario: BR-RULE-204 INV-2 holds -- omitted status defaults to completed
    Given the tenant has a task "task_inv204_2" in status "in_progress"
    When the Buyer Agent invokes complete_task with task_id "task_inv204_2" and no status parameter
    Then the task transitions to status "completed"
    # INV-2: Default to "completed"

  @T-UC-027-inv-204-3-violated @invariant @BR-RULE-204 @error
  Scenario: BR-RULE-204 INV-3 violated -- invalid status rejected before task lookup
    Given the tenant has a task "task_inv204_3" in status "pending"
    When the Buyer Agent invokes complete_task with task_id "task_inv204_3" and status "rejected"
    Then the operation should fail with error code "COMPLETION_STATUS_INVALID"
    And the error code should be "COMPLETION_STATUS_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "'completed' or 'failed'"
    # INV-3: Invalid status rejected before task lookup

  @T-UC-027-inv-205-1-holds @invariant @BR-RULE-205
  Scenario: BR-RULE-205 INV-1 holds -- no filters returns all tenant tasks
    Given the tenant has 10 tasks across domains and statuses
    When the Buyer Agent invokes list_tasks with no filters
    Then the query_summary shows total_matching as 10
    # INV-1: No filters = all tasks

  @T-UC-027-inv-205-2-violated @invariant @BR-RULE-205 @error
  Scenario: BR-RULE-205 INV-2 violated -- empty multi-value array rejected
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter protocols []
    Then the operation should fail with error code "FILTER_ARRAY_EMPTY"
    And the error code should be "FILTER_ARRAY_EMPTY"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one value"
    # INV-2: minItems=1 enforced
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-205-3-violated @invariant @BR-RULE-205 @error
  Scenario: BR-RULE-205 INV-3 violated -- task_ids exceeds 100 items
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter task_ids containing 101 items
    Then the operation should fail with error code "FILTER_TASK_IDS_TOO_MANY"
    And the error code should be "FILTER_TASK_IDS_TOO_MANY"
    And the error should include "suggestion" field
    And the suggestion should contain "Reduce the number"
    # INV-3: maxItems=100 enforced
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-205-4-holds @invariant @BR-RULE-205
  Scenario: BR-RULE-205 INV-4 holds -- multiple filters combine with AND semantics
    Given the tenant has 5 media-buy tasks (3 submitted, 2 failed) and 3 signals tasks (all submitted)
    When the Buyer Agent invokes list_tasks with filters protocol "media-buy" and status "submitted"
    Then the query_summary shows total_matching as 3
    And all returned tasks have domain "media-buy" and status "submitted"
    # INV-4: AND across dimensions, OR within multi-value
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-205-5-violated @invariant @BR-RULE-205 @error
  Scenario: BR-RULE-205 INV-5 violated -- invalid date format rejected
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter updated_after "not-a-date"
    Then the operation should fail with error code "FILTER_DATE_INVALID_FORMAT"
    And the error code should be "FILTER_DATE_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "ISO 8601"
    # INV-5: Date format validation

  @T-UC-027-inv-206-1-holds @invariant @BR-RULE-206
  Scenario: BR-RULE-206 INV-1 holds -- omitted sort defaults to created_at desc
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with no sort specified
    Then the query_summary shows sort_applied as field "created_at" direction "desc"
    # INV-1: Default sort

  @T-UC-027-inv-206-2-holds @invariant @BR-RULE-206
  Scenario: BR-RULE-206 INV-2 holds -- valid sort field applied and echoed
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with sort field "task_type" direction "asc"
    Then the response tasks are ordered by task_type ascending
    And the query_summary shows sort_applied as field "task_type" direction "asc"
    # INV-2: Valid sort applied

  @T-UC-027-inv-206-3-violated @invariant @BR-RULE-206 @error
  Scenario: BR-RULE-206 INV-3 violated -- invalid sort field rejected
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with sort field "name" direction "asc"
    Then the operation should fail with error code "SORT_FIELD_INVALID"
    And the error code should be "SORT_FIELD_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "supported sort fields"
    # INV-3: Invalid sort field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-206-4-violated @invariant @BR-RULE-206 @error
  Scenario: BR-RULE-206 INV-4 violated -- invalid sort direction rejected
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with sort field "status" direction "up"
    Then the operation should fail with error code "SORT_DIRECTION_INVALID"
    And the error code should be "SORT_DIRECTION_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "'asc' or 'desc'"
    # INV-4: Invalid sort direction

  @T-UC-027-inv-207-1-holds @invariant @BR-RULE-207
  Scenario: BR-RULE-207 INV-1 holds -- successful completion generates audit entry
    Given the tenant has a task "task_aud_inv1" in status "pending"
    And the principal identity is "user_audit_001"
    When the Buyer Agent invokes complete_task with task_id "task_aud_inv1" and status "completed"
    Then an audit log entry is written with operation "complete_task"
    And the audit entry contains principal_id "user_audit_001"
    And the audit entry contains task_id "task_aud_inv1"
    And the audit entry contains original_status "pending" and new_status "completed"
    # INV-1: Successful completion produces audit entry

  @T-UC-027-inv-207-2-holds @invariant @BR-RULE-207
  Scenario: BR-RULE-207 INV-2 holds -- failed completion does not generate audit entry
    Given the tenant has no task with id "task_aud_inv2_ghost"
    When the Buyer Agent invokes complete_task with task_id "task_aud_inv2_ghost" and status "completed"
    Then the operation should fail with error code "REFERENCE_NOT_FOUND"
    And the error code should be "REFERENCE_NOT_FOUND"
    And no audit log entry is written for task "task_aud_inv2_ghost"
    And the error should include "suggestion" field
    And the suggestion should contain "Verify the task_id"
    # INV-2: Failed completion has no audit entry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-207-3-holds @invariant @BR-RULE-207
  Scenario: BR-RULE-207 INV-3 holds -- known principal recorded in audit
    Given the tenant has a task "task_aud_inv3" in status "in_progress"
    And the principal identity is "user_known_001"
    When the Buyer Agent invokes complete_task with task_id "task_aud_inv3" and status "failed"
    Then the audit entry records principal_id as "user_known_001"
    # INV-3: Known principal attribution

  @T-UC-027-inv-207-4-holds @invariant @BR-RULE-207
  Scenario: BR-RULE-207 INV-4 holds -- unknown principal uses fallback
    Given the tenant has a task "task_aud_inv4" in status "requires_approval"
    And the principal identity cannot be resolved
    When the Buyer Agent invokes complete_task with task_id "task_aud_inv4" and status "completed"
    Then the audit entry records principal_id as "unknown"
    # INV-4: Unknown principal fallback

  @T-UC-027-inv-208-1-holds @invariant @BR-RULE-208
  Scenario: BR-RULE-208 INV-1 holds -- successful query includes complete summary
    Given the tenant has tasks in multiple domains and statuses
    When the Buyer Agent invokes list_tasks with filter protocol "media-buy"
    Then the response includes query_summary with total_matching, returned, domain_breakdown, status_breakdown, filters_applied, and sort_applied
    # INV-1: All summary fields present
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-208-2-holds @invariant @BR-RULE-208
  Scenario: BR-RULE-208 INV-2 holds -- empty result set shows zero summary
    Given the tenant has no tasks matching filter protocol "governance"
    When the Buyer Agent invokes list_tasks with filter protocol "governance"
    Then the query_summary shows total_matching as 0 and returned as 0
    And the domain_breakdown and status_breakdown are empty objects
    # INV-2: Zero counts with empty breakdowns
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-208-3-holds @invariant @BR-RULE-208
  Scenario: BR-RULE-208 INV-3 holds -- returned count matches tasks array length
    Given the tenant has 8 tasks
    When the Buyer Agent invokes list_tasks with no filters
    Then the query_summary returned count equals the length of the tasks array
    # INV-3: returned == len(tasks)

  @T-UC-027-inv-208-4-holds @invariant @BR-RULE-208
  Scenario: BR-RULE-208 INV-4 holds -- returned does not exceed total_matching
    Given the tenant has 60 tasks
    When the Buyer Agent invokes list_tasks with max_results 25
    Then the query_summary shows returned as 25 and total_matching as 60
    And returned is less than or equal to total_matching
    # INV-4: returned <= total_matching

  @T-UC-027-v31-list-filter-protocol @v3-1 @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- v3.1 protocol filter accepts adcp-protocol enum values
    Given the tenant has tasks across multiple adcp-protocol values
    When the Buyer Agent invokes list_tasks with filter protocol "brand"
    Then the response contains only tasks belonging to adcp-protocol "brand"
    And the query_summary filters_applied includes the protocol filter
    # v3.1: filters.protocol replaces legacy filters.domain at the top-level taxonomy
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-list-filter-protocols-multi @v3-1 @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- v3.1 protocols multi-value filter accepts adcp-protocol array
    Given the tenant has tasks across multiple adcp-protocol values
    When the Buyer Agent invokes list_tasks with filter protocols "media-buy,signals,governance"
    Then the response contains only tasks belonging to adcp-protocol values in the requested set
    And the query_summary filters_applied includes the protocols filter
    # v3.1: filters.protocols array (minItems 1) of adcp-protocol values
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-list-sort-protocol @v3-1 @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- v3.1 sort.field protocol orders results by adcp-protocol
    Given the tenant has tasks across multiple adcp-protocol values
    When the Buyer Agent invokes list_tasks with sort field "protocol" and direction "asc"
    Then the returned tasks are ordered by their adcp-protocol value ascending
    And the query_summary sort_applied reports field "protocol" and direction "asc"
    # v3.1: sort.field enum extended with protocol (replacing legacy domain)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-get-include-result-completed @v3-1 @extension-a @get-task @happy-path @post-s2
  Scenario: Get task -- v3.1 include_result true on completed task returns result payload
    Given a completed task exists in the tenant
    When the Buyer Agent invokes get_task with task_id and include_result true
    Then the response status is "completed"
    And the response includes the result field with an async-response-data payload
    # v3.1: include_result=true + status=completed MUST surface result per tasks-get-request schema
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-get-include-result-default-omits @v3-1 @extension-a @get-task @happy-path @post-s2
  Scenario: Get task -- v3.1 default include_result false omits result on completed task
    Given a completed task exists in the tenant
    When the Buyer Agent invokes get_task with task_id and no include_result flag
    Then the response status is "completed"
    And the response does not include the result field
    # v3.1: default include_result=false keeps polling lightweight
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-get-response-protocol-required @v3-1 @extension-a @get-task @happy-path @post-s2
  Scenario: Get task -- v3.1 response includes REQUIRED protocol field referencing adcp-protocol
    Given a task exists in the tenant
    When the Buyer Agent invokes get_task with task_id
    Then the response includes the protocol field with a value from the adcp-protocol enum
    And the protocol field is REQUIRED on the tasks-get-response
    # v3.1: tasks-get-response.protocol REQUIRED ($ref /schemas/enums/adcp-protocol.json)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-get-include-result-failed-omits @v3-1 @extension-a @get-task @happy-path @post-s2
  Scenario: Get task -- v3.1 failed status carries error and omits result regardless of include_result
    Given the tenant has a task in status "failed" with error code and message
    When the Buyer Agent invokes get_task with task_id and include_result true
    Then the response status is "failed"
    And the response includes error section with code, message, and details
    And the response does not include the result field
    # BR-RULE-297 INV-4: failed → error carried, result omitted (mutually exclusive output channels)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-v31-get-include-result-non-terminal-omits @v3-1 @extension-a @get-task @happy-path @post-s2
  Scenario Outline: Get task -- v3.1 include_result true on non-terminal status omits result
    Given the tenant has a task in status "<status>"
    When the Buyer Agent invokes get_task with task_id and include_result true
    Then the response status is "<status>"
    And the response does not include the result field
    # BR-RULE-297 INV-5: non-terminal statuses have no completion payload yet (OQ-7)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

    Examples:
      | status         |
      | submitted      |
      | working        |
      | input-required |
      | auth-required  |

  @T-UC-027-inv-298-2-violated @v3-1 @extension-a @get-task @sad-path
  Scenario: Get task -- v3.1 response with protocol value outside adcp-protocol enum is rejected by schema validation
    Given a task exists in the tenant
    When the seller backend emits a tasks-get-response with protocol value "broadcast"
    Then schema validation rejects the response
    And the rejection identifies protocol as outside the adcp-protocol enum
    # BR-RULE-298 INV-2: PROTOCOL_VALUE_INVALID server invariant
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-298-3-violated @v3-1 @extension-a @get-task @sad-path
  Scenario: Get task -- v3.1 response missing required protocol field is rejected by schema validation
    Given a task exists in the tenant
    When the seller backend emits a tasks-get-response that omits the protocol field
    Then schema validation rejects the response
    And the rejection identifies protocol as a required-field violation
    # BR-RULE-298 INV-3: PROTOCOL_FIELD_MISSING server invariant
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-298-4-violated @v3-1 @extension-a @get-task @sad-path
  Scenario: Get task -- v3.1 response with legacy domain field instead of protocol is treated as protocol absent
    Given a task exists in the tenant
    When the seller backend emits a tasks-get-response carrying pre-v3.1 "domain" instead of "protocol"
    Then schema validation rejects the response
    And the rejection identifies protocol as missing (legacy domain is not declared on v3.1 get-response)
    # BR-RULE-298 INV-4: legacy domain treated as protocol absent
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-298-5-holds @v3-1 @extension-a @get-task @list-tasks @happy-path
  Scenario: List vs Get -- v3.1 same task surfaces narrower list domain and broader get protocol per surface asymmetry
    Given a task with adcp-protocol "governance" exists in the tenant
    When the Buyer Agent invokes list_tasks with no filter
    And the Buyer Agent invokes get_task with the same task_id
    Then the list response tasks[] entry domain is one of "media-buy" or "signals" or absent per the narrowed enum
    And the get response protocol is "governance" from the full adcp-protocol enum
    # BR-RULE-298 INV-5 / OQ-6: list-vs-get surface asymmetry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-208-5-additional-protocols @v3-1 @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- v3.1 domain_breakdown surfaces additional protocol counts via additionalProperties
    Given the tenant has tasks spanning protocols "media-buy", "signals", and "creative"
    When the Buyer Agent invokes list_tasks with no filter
    Then the query_summary domain_breakdown declares the narrow keys media-buy and signals
    And the query_summary domain_breakdown also includes count for protocol "creative" via additionalProperties
    # BR-RULE-208 INV-5: additionalProperties=true allows non-{media-buy,signals} counts (OQ-8)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-inv-205-6-holds @v3-1 @main-flow @list-tasks @happy-path @post-s1
  Scenario: List tasks -- v3.1 protocol filter values must be members of the adcp-protocol enum
    Given the tenant has tasks across multiple adcp-protocol values
    When the Buyer Agent invokes list_tasks with filter protocol "signals"
    Then the response contains only tasks belonging to adcp-protocol "signals"
    And the query_summary filters_applied includes the protocol filter
    # BR-RULE-205 INV-6: protocol/protocols filter members must be from adcp-protocol.json
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-partition-get-response-protocol @v3-1 @partition @get_response_protocol
  Scenario Outline: tasks-get-response protocol identity -- <partition>
    Given a task with the configured protocol exists in the tenant
    When the Buyer Agent invokes get_task with task_id
    Then <outcome>
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

    Examples: Valid partitions
      | partition              | outcome                                                                                                |
      | media_buy              | the response protocol field equals "media-buy"                                                         |
      | signals                | the response protocol field equals "signals"                                                           |
      | governance             | the response protocol field equals "governance"                                                        |
      | creative               | the response protocol field equals "creative"                                                          |
      | brand                  | the response protocol field equals "brand"                                                             |
      | sponsored_intelligence | the response protocol field equals "sponsored-intelligence"                                            |
      | measurement            | the response protocol field equals "measurement"                                                       |

    Examples: Invalid partitions
      | partition         | outcome                                                                                                |
      | legacy_domain     | schema validation rejects the response and the rejection identifies protocol as missing (legacy domain not declared in v3.1)  |
      | unknown_protocol  | schema validation rejects the response with PROTOCOL_VALUE_INVALID and a suggestion to use one of media-buy, signals, governance, creative, brand, sponsored-intelligence, measurement  |
      | protocol_absent   | schema validation rejects the response with PROTOCOL_FIELD_MISSING and a suggestion to emit protocol referencing adcp-protocol.json  |

  @T-UC-027-boundary-get-response-protocol @v3-1 @boundary @get_response_protocol
  Scenario Outline: tasks-get-response protocol identity boundary -- <boundary_point>
    Given a task with the configured protocol exists in the tenant
    When the Buyer Agent invokes get_task with task_id
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                                          | outcome                                                                                  |
      | protocol = 'media-buy' (first enum value)                                               | the response protocol field equals "media-buy"                                           |
      | protocol = 'measurement' (last enum value, v3.1 expansion)                              | the response protocol field equals "measurement"                                         |
      | protocol = 'sponsored-intelligence' (v3.1 expansion beyond pre-v3.1 governance-domain)  | the response protocol field equals "sponsored-intelligence"                              |
      | protocol = 'other' (not in enum)                                                        | schema validation rejects the response with PROTOCOL_VALUE_INVALID                       |
      | protocol absent in response (required-field violation)                                  | schema validation rejects the response with PROTOCOL_FIELD_MISSING                       |
      | response emits legacy `domain` field (pre-v3.1)                                         | schema validation rejects the response with PROTOCOL_FIELD_MISSING (legacy domain ignored under v3.1) |

  @T-UC-027-ext-g-filter-value-invalid @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-007 -- FILTER_VALUE_INVALID rejects out-of-enum filter value
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter status "nonexistent_status"
    Then the operation should fail with error code "FILTER_VALUE_INVALID"
    And the error code should be "FILTER_VALUE_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "enum"
    And the request context is echoed in the response
    # EC-007: schema-level enum membership check at request validation
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending filter field
    # POST-F3: application context echoed on validation failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-g-filter-array-empty @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-008 -- FILTER_ARRAY_EMPTY rejects multi-value array with zero items
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter statuses []
    Then the operation should fail with error code "FILTER_ARRAY_EMPTY"
    And the error code should be "FILTER_ARRAY_EMPTY"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one value"
    And the request context is echoed in the response
    # EC-008: minItems=1 enforced at request validation
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending filter array
    # POST-F3: application context echoed on validation failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-g-filter-date-invalid-format @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-009 -- FILTER_DATE_INVALID_FORMAT rejects non-ISO 8601 date filter
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter created_after "2025/01/01"
    Then the operation should fail with error code "FILTER_DATE_INVALID_FORMAT"
    And the error code should be "FILTER_DATE_INVALID_FORMAT"
    And the error should include "suggestion" field
    And the suggestion should contain "ISO 8601"
    And the request context is echoed in the response
    # EC-009: date-time format validation at request validation
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending date filter
    # POST-F3: application context echoed on validation failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-g-filter-task-ids-too-many @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-010 -- FILTER_TASK_IDS_TOO_MANY rejects task_ids array exceeding 100
    Given the tenant has workflow tasks
    When the Buyer Agent invokes list_tasks with filter task_ids containing 101 items
    Then the operation should fail with error code "FILTER_TASK_IDS_TOO_MANY"
    And the error code should be "FILTER_TASK_IDS_TOO_MANY"
    And the error should include "suggestion" field
    And the suggestion should contain "Reduce the number"
    And the request context is echoed in the response
    # EC-010: maxItems=100 enforced at request validation
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending task_ids array
    # POST-F3: application context echoed on validation failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-g-sort-field-invalid @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-011 -- SORT_FIELD_INVALID rejects sort.field outside v3.1 enum
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with sort field "priority" direction "asc"
    Then the operation should fail with error code "SORT_FIELD_INVALID"
    And the error code should be "SORT_FIELD_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "supported sort fields"
    And the request context is echoed in the response
    # EC-011: v3.1 sort.field enum {created_at, updated_at, status, task_type, protocol}
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending sort.field
    # POST-F3: application context echoed on validation failure
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/core/tasks-list-request.json

  @T-UC-027-ext-g-sort-direction-invalid @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Extension G EC-012 -- SORT_DIRECTION_INVALID rejects sort.direction outside {asc,desc}
    Given the tenant has tasks
    When the Buyer Agent invokes list_tasks with sort field "created_at" direction "ascending"
    Then the operation should fail with error code "SORT_DIRECTION_INVALID"
    And the error code should be "SORT_DIRECTION_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "'asc' or 'desc'"
    And the request context is echoed in the response
    # EC-012: sort-direction enum {asc, desc}
    # POST-F1: no tenant query executed
    # POST-F2: canonical error code identifies offending sort.direction
    # POST-F3: application context echoed on validation failure

  @T-UC-027-ext-h-summary-inconsistent @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Extension H EC-013 -- SUMMARY_INCONSISTENT defensive gate rejects mis-reconciled response
    Given the tenant has workflow tasks
    And the server response construction yields query_summary aggregates that disagree with the tasks array
    When the Buyer Agent invokes list_tasks with no filters
    Then the operation should fail with error code "SUMMARY_INCONSISTENT"
    And the error code should be "SUMMARY_INCONSISTENT"
    And the error message identifies the inconsistent counter
    And the error should include "suggestion" field
    And the request context is echoed in the response
    # EC-013: server self-check at response construction
    # POST-F1: list query is read-only; no client-visible side effect
    # POST-F2: buyer knows response was rejected by internal consistency check
    # POST-F3: application context echoed on validation failure

