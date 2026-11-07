# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# THE REPORTING ROUTE'S half of the webhook correlation-id contract. The
# REGISTRATION route's half lives in local-egress-ssrf-refusal.feature
# (@T-EGRESS-OPID-create-media-buy), and the two are deliberately NOT one
# scenario: the spec gives them different sanctioned shapes.
#
# Spec grounding — AdCP 3.2.1, the version this repo PINS (see
# docs/adcp-spec-version.md). The writing is not vendored; read it with
#   gh api repos/adcontextprotocol/adcp/contents/docs/building/by-layer/L3/webhooks.mdx \
#     --jq '.content' | base64 -d
# § "Reporting webhooks" → "AdCP 3.2 interim reporting correlation":
#   :664 "AdCP 3.2.1 requires `operation_id` in the `media_buy_delivery` webhook
#        envelope, but its `reporting_webhook` registration schema does not define
#        that field (adcp#7885)."
#   :666 "A registration without a buyer-supplied ID cannot enable this interim
#        delivery path. Flag the missing value for the buyer and suppress scheduled
#        sends; do not fabricate an ID or extract one from the URL."
#   :667 "Existing buyers need to update their registration with an ID before
#        reports resume. They can use `get_media_buy_delivery` while arranging
#        support."
#   :669 "The registration's value is independent of any task-status
#        `push_notification_config.operation_id`."
#
# WHY NOT A REJECTED REGISTRATION, the shape the sibling feature grades.
# `core/reporting-webhook.json` does not declare `operation_id` at the 3.2 pin —
# the field rides that schema's `additionalProperties: true` as the interim
# agreement of :656-673 — so a registration omitting it is a document the
# PUBLISHED schema accepts, and refusing a `create_media_buy` over its absence
# would refuse a conformant request. :666's fail-closed MUST NOT is also scoped to
# 3.3 (:665: "The field is standardized as an optional field in AdCP 3.3 ... The
# fail-closed MUST NOT above applies from 3.3; on 3.2 it remains opt-in guidance"),
# and THIS SELLER HAS ADOPTED the interim agreement: its one envelope builder
# requires `operation_id`, so :666 is the rule it follows rather than the 3.3 MUST.
#
# WHAT EACH HALF OF :666 IS CARRIED BY. "Suppress scheduled sends" belongs to
# `delivery_webhook_scheduler`, which decides it before assembling a delivery; that
# runs in a background batch with no request to answer, so it cannot also carry the
# flag. "Flag the missing value for the buyer" therefore rides the read :667 itself
# points the buyer at — `get_media_buy_delivery` — and is what this scenario grades.
#
# AN ADVISORY, NOT A REFUSAL, and that follows from the lane rule rather than
# convenience (docs/design/error-architecture.md § Which lane a failure takes): the
# delivery data IS the answer the buyer asked for, so the response stays a success
# and `errors[]` reports what is degraded. `get-media-buy-delivery-response.json`
# declares the array, which is what makes the lane available here at all.
#
# INVALID_REQUEST is the same code the registration half carries, which is correct
# and not a collapse: the two halves differ in LANE (raised vs advisory), not in
# what the buyer got wrong. The pinned enum calls INVALID_REQUEST "missing required
# fields", and from this seller's side an absent correlation id is exactly that —
# the envelope it feeds requires one. No message/recovery/suggestion is graded
# anywhere here: all three are read-only over CODE_TABLE (ADR-010), so asserting
# them would restate the code.
#
# UNGRADED BY STORYBOARD. Nothing under dist/compliance/ registers a
# reporting_webhook without the interim field — the conformance runner emits only
# schema-valid payloads, and a registration omitting a field its schema does not
# declare is schema-valid. Reconcile upstream in adcp-req once adcp#7885 settles
# (3.3 standardizes the field), then retire this file for the regenerated scenario.
#
# The @T-UC-004 tag is what routes this to the delivery-poll harness
# (storyboard_spec.detect_uc derives the bucket from the tag, and uc004_harness
# falls through to "poll" for a marker set carrying neither `webhook` nor
# `webhook-reliability`) — so the scenario runs on every transport the UC-004
# delivery read runs on, which is the point of grading it here rather than in a
# unit test against the advisory list.
Feature: A reporting stream with no correlation id is suppressed and flagged (local, L3)

  @T-UC-004-reporting-stream-no-operation-id @invariant
  Scenario: a reporting_webhook with no operation_id is flagged on the delivery read
    Given a media buy "mb-nostream" owned by "buyer-001" with status "active"
    And the reporting_webhook on "mb-nostream" carries no operation_id
    When the Buyer Agent requests delivery metrics for media_buy_ids ["mb-nostream"]
    Then the response errors include code "INVALID_REQUEST" for media buy "mb-nostream"
