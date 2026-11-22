# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# NOT a protocol obligation, and deliberately not dressed as one. AdCP 3.1.1 is
# silent on WHEN a seller may run a background review; what it is not silent
# about is what sync_creatives reports. The sibling file
# local-uc006-dry-run-out-of-transaction-effects.feature grades the preview branch
# (an effect a rollback cannot reach must not fire at all); this file grades the
# LIVE branch of the same seam, where the failure is ordering rather than
# occurrence. Per the project's source hierarchy, where the schema is silent the
# invariant is production's own — stated here so it is graded rather than
# assumed.
#
# The invariant: an effect that leaves the sync's transaction runs only AFTER
# that transaction commits. The AI-review job is the concrete instance —
# _processing.py hands it to a background executor and it opens its own session,
# so committed rows are the only state it can ever read. Today the sync
# ``flush()``es and submits from INSIDE the still-open transaction, which is not
# a commit: on the create branch the job's session finds no row, and on the update
# branch it finds the row as it was BEFORE this sync touched it.
#
# Why the outline is create x update and not the four-cell partition its sibling
# uses: the AI-review submit is on the approval-mode branch, which does not fork
# on format kind, so generative vs agent-served-static reaches the same submit.
# What DOES fork it is whether the creative already exists — the two branches have
# separate submit sites and separate wrong answers (missing row vs stale row).
#
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/creative/sync-creatives-response.json
Feature: UC-006 sync_creatives — an effect that leaves the transaction runs only after that transaction commits (local)

  @T-UC-006-local-post-commit-ai-review-ordering @creative-approval @invariant
  Scenario Outline: the AI review job cannot observe a creative the sync has not committed
    Given the Buyer is authenticated
    And the tenant has approval_mode "ai-powered"
    And the tenant has a slack_webhook_url configured
    And a <creative_state> creative on a static format served by a creative agent
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And every creative result has action "<expected_action>"
    And the AI review submissions name exactly the synced creative
    And each AI review submission observes the creative exactly as the sync committed it
    # The last Then compares two reads of the same row: what an INDEPENDENT
    # connection could see at submit time, and what the request left committed.
    # Equality is the whole invariant — the job reads committed state and nothing
    # else, so any difference is state the job would have missed or misread.
    #
    # "the AI review submissions name exactly the synced creative" is the
    # non-vacuity control: without it, an ai-powered branch that stopped
    # submitting, a renamed executor or a wrong patch target would leave nothing
    # observed and the comparison would hold over an empty set.

    Examples:
      | partition_boundary                             | creative_state | expected_action |
      | create branch, no row exists until the commit     | new            | created         |
      | update branch, the row exists but is pre-update   | existing       | updated         |

  # --- the workflow-step seam of the same invariant (GH #2002) ---
  #
  # The AI-review outline above grades an effect that must not overtake the
  # CREATIVE it reviews. These two grade the other escaping effect on this path
  # and the writes it names: the Slack approval notification and the workflow
  # steps a human opens from it.
  #
  # The creatives, their assignments and the workflow steps are one transaction, and
  # the notification is an after_commit effect of it. So it fires only for a sync that
  # stood, and it names rows a human can already open.
  #
  # Graded at the notification itself, over an INDEPENDENT connection, because after
  # the request every ordering looks alike. A flush without a commit is invisible to
  # that connection, so neither assertion can pass vacuously: an empty read reddens them.

  @T-UC-006-local-post-commit-workflow-step-ordering @creative-approval @invariant
  Scenario: the approval notification names workflow steps and an assignment that are already committed
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    And an assignment to a package that exists in the tenant
    And the effects escaping the sync transaction are observed as they fire
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And a Slack notification should be sent immediately
    And the workflow steps the request committed were already visible when Slack was notified
    And the assignment the request made was already committed when Slack was notified
    # "a Slack notification should be sent immediately" is the non-vacuity control:
    # an unsent notification would leave both observations unset.

  @T-UC-006-local-post-commit-no-orphan-pending-creative @creative-approval @invariant
  Scenario: a strict-mode assignment failure leaves no creative, workflow step or notification behind
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    And validation_mode is "strict"
    And assignments to a non-existent package
    When the Buyer Agent syncs the creative
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code PACKAGE_NOT_FOUND
    And no creative, workflow step or Slack notification came of the request
    # The buyer-facing half is graded on the real wire bytes, not on the
    # harness's reconstructed exception, and deliberately NOT through the
    # existing "the operation should fail with an assignment error" step: that
    # step xfails the whole scenario on the package-not-found branch to excuse a
    # spec-code gap in OTHER features, which would swallow the assertion below
    # before it ever ran. PACKAGE_NOT_FOUND / correctable is what
    # AdCPPackageNotFoundError declares (src/core/exceptions.py) and what
    # _assignments.py raises.
    #
    # Cross-reference GH #1987, a creative left at pending_review with nothing pointing
    # at it. sync-creatives-request.json validation_mode: "'strict' fails entire sync on
    # any validation error", so the creative, its step and the message all go with the
    # refusal and there is nothing to orphan. The buyer's error is asserted first so this
    # scenario cannot be satisfied by swallowing it.
