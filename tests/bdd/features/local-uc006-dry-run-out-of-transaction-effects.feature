# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: BR-UC-006-sync-creatives.feature carries NO dry_run scenario at
# all (its only "preview" scenarios are agent-render ones), and every one of its
# ai-powered rows (:506, :782, :1014) drives the LIVE branch. So the intersection
# that matters here — dry_run ON an ai-powered tenant — is graded by nothing,
# upstream or locally. The conformance storyboard cannot close it either:
# `dry_run` appears nowhere in dist/compliance/3.1.1.
#
# What the pinned schema mandates: dry_run is "preview changes without applying
# them. Returns what would be created/updated/deleted". An AI review is not a
# preview of anything — the job opens its own transaction, COMMITS a review
# verdict onto the creative row, and then sends Slack and the push webhook
# (src/admin/blueprints/creatives.py). None of that is reachable by a rollback,
# so a preview that submits one has applied a change it only promised to show.
#
# Why the pair: the live scenario is the NON-VACUITY CONTROL. Without it, the
# preview scenario's "no submission" assertion would also pass against a wrong
# patch target, a renamed executor, or an ai-powered branch that stopped firing
# — i.e. it would grade nothing. The two scenarios differ in exactly one input.
#
# Reconcile upstream in adcp-req (a dry_run × approval_mode partition), then
# retire this file in favour of the regenerated one.
#
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/creative/sync-creatives-request.json pointer=/properties/dry_run
Feature: UC-006 sync_creatives — a dry_run preview fires no effect the transaction cannot undo (local)

  @T-UC-006-local-dryrun-ai-review-live @dry-run @creative-approval @invariant
  Scenario Outline: a live sync on an ai-powered tenant submits the AI review and calls the agent (control)
    Given the Buyer is authenticated
    And the tenant has approval_mode "ai-powered"
    And the tenant has a slack_webhook_url configured
    And a <creative_state> creative on a <format_kind> format served by a creative agent
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And every creative result has action "<expected_action>"
    And the AI review submissions name exactly the synced creative
    And the creative agent is called to build or preview the creative

    Examples:
      | partition_boundary                              | creative_state | format_kind | expected_action |
      | new_generative new creative, build_creative     | new            | generative  | created         |
      | new_static new creative, preview_creative       | new            | static      | created         |
      | existing_generative update branch, build_creative  | existing       | generative  | updated         |
      | existing_static update branch, preview_creative    | existing       | static      | updated         |

  @T-UC-006-local-dryrun-ai-review-preview @dry-run @creative-approval @invariant
  Scenario Outline: a dry_run preview on an ai-powered tenant fires no effect the transaction cannot undo
    Given the Buyer is authenticated
    And the tenant has approval_mode "ai-powered"
    And the tenant has a slack_webhook_url configured
    And a <creative_state> creative on a <format_kind> format served by a creative agent
    When the Buyer Agent previews the creative with dry_run true
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And every creative result has action "<expected_action>"
    And no AI review is submitted
    And no creative agent request is made
    And no Slack notification should be sent
    And no creative is persisted for the tenant
    # "no creative agent request is made" covers the four outbound
    # build_creative/preview_creative calls, which NOTHING else in the lane can
    # grade: they exist to make a preview differ from a live run in side effects,
    # and the dry_run parity oracle compares preview against live -- so un-gating
    # them reads as MORE parity to that oracle, never less. Only an
    # effect-observation assertion sees them, and the live scenario's mirror Then
    # is what keeps this one non-vacuous.
    #
    # The outline exists because production has FOUR such call sites, partitioned
    # on two independent dimensions (new vs existing creative x generative vs
    # agent-served-static format), and any single payload reaches exactly one of
    # them. A single scenario grades one gate and leaves three un-gated sites
    # invisible -- which is precisely what a first version of this file did.
    #
    # The three AI-review Thens are the three things that job does that a preview's
    # rollback cannot reach: it COMMITS `status` + `data["ai_review"]` through its
    # own AdminCreativeUoW, it sends Slack, and it fires the push webhook. Asserting
    # the submit never happened is what covers all three at their single source; the
    # Slack and persistence Thens pin the two halves that are separately observable.

    Examples:
      | partition_boundary                              | creative_state | format_kind | expected_action |
      | new_generative new creative, build_creative     | new            | generative  | created         |
      | new_static new creative, preview_creative       | new            | static      | created         |
      | existing_generative update branch, build_creative  | existing       | generative  | updated         |
      | existing_static update branch, preview_creative    | existing       | static      | updated         |

  # --- the workflow-step WRITE PATH (GH #2002) ---
  #
  # A second escaping seam in the same file, and the one the AI-review pair
  # above cannot reach: `if creatives_needing_approval and not dry_run` in
  # _sync.py gates _create_sync_workflow_steps, which opens its OWN WorkflowUoW
  # and so commits independently of the creatives transaction. The gate is why a
  # preview never executes that write AT ALL — and a write a preview never
  # executes is a write whose defects (FK ordering, the Pydantic/JSON boundary
  # on `format`, tenant scoping, schema drift) are invisible until a live run.
  #
  # dry_run's mandate is "preview changes without applying them. Returns what
  # would be created/updated/deleted"
  # (creative/sync-creatives-request.json#/properties/dry_run @ v3.1.1). "Without
  # applying them" is a claim about what SURVIVES the request, not about which
  # code runs: the correct shape is one transaction that executes the write on
  # both branches and discards it on the preview branch.
  #
  # Why the pair, again: the live scenario is the non-vacuity control, and the
  # two differ in exactly one input (dry_run). The preview branch's "zero rows"
  # assertion is worth nothing on its own — it also holds for a preview that
  # skipped the write, which is precisely today's behaviour — so the live branch
  # must first prove that THIS payload, on THIS tenant, does produce steps,
  # mappings and a context. The response-level Thens are bound by both branches on
  # the same field, so a preview that stopped reporting the creative reddens
  # here rather than passing "zero rows" for the wrong reason.

  @T-UC-006-local-dryrun-workflow-step-write-live @dry-run @creative-approval @invariant
  Scenario: a live sync on a require-human tenant commits the workflow-step write path (control)
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent syncs the creative
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And every creative result has action "created"
    And the committed workflow rows name exactly the synced creatives

  @T-UC-006-local-dryrun-workflow-step-write-preview @dry-run @creative-approval @invariant
  Scenario: a dry_run preview leaves no workflow step, mapping or context behind
    Given the Buyer is authenticated
    And the tenant has approval_mode "require-human"
    And the tenant has a slack_webhook_url configured
    And a creative with a known format_id
    When the Buyer Agent previews the creative with dry_run true
    Then the response is compliant with the sync_creatives success spec
    And the response is the success variant carrying a creatives array
    And every creative result has action "created"
    And no workflow step, mapping or context row is committed for the tenant
    And no Slack notification should be sent
    And no creative is persisted for the tenant
