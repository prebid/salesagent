# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# THE REPORTING CADENCE half of the reporting-webhook registration contract. The
# correlation-id half lives in local-reporting-stream-correlation.feature and in
# local-egress-ssrf-refusal.feature's @T-EGRESS-OPID-create-media-buy; this one
# grades a DIFFERENT field of the same document and takes a different lane.
#
# Spec grounding — AdCP 3.2.1, the version this repo PINS (see
# docs/adcp-spec-version.md).
#
# THE OBLIGATION is in the pinned SCHEMA, not in the written spec, and that is
# worth stating because the grounding gate asks for the prose first.
# `core/reporting-webhook.json` makes `reporting_frequency` REQUIRED alongside
# `url` and `authentication`, enumerates [hourly, daily, monthly], and describes
# it as:
#   "Frequency for automated reporting delivery. Must be supported by all
#    products in the media buy."
# `core/reporting-capabilities.json` makes `available_reporting_frequencies`
# REQUIRED, and `core/product.json` makes `reporting_capabilities` required, so
# every product states its cadences and the buyer's value has to lie in the
# INTERSECTION of what this buy's products advertise.
#
# The WRITTEN spec says nothing further, measured rather than assumed:
# L3/webhooks.mdx mentions `reporting_frequency` once, at :680, inside an example
# payload; media-products.mdx and reporting-core-implementation-guide.mdx do not
# mention it at all; and a repo-wide code search for the schema's own phrase
# returns exactly one file, the schema. So the pin obliges the buyer to pick a
# supported value and the seller to DECLARE its support honestly, and leaves the
# error path for an unsupported value unspecified. Refusing is this seller's
# choice among the shapes the pin permits, and it is recorded on
# salesagent-8qela.171.
#
# UNSUPPORTED_FEATURE, NOT a schema error, and the pin is explicit about the
# difference. L3/error-handling.mdx :191 forbids returning VALIDATION_ERROR with
# `keyword: "enum"` when a seller narrows a public enum to its own subset —
# "the public-spec replay would accept the value; the seller rejects on private
# state" — and names POLICY_VIOLATION or UNSUPPORTED_FEATURE instead. The pinned
# enum classifies UNSUPPORTED_FEATURE `correctable` with "check
# get_adcp_capabilities and remove unsupported fields", which is exactly the
# lever a buyer has here: read `available_reporting_frequencies` and resubmit.
# No specific code exists for a reporting cadence — the pinned 120 carry
# UNSUPPORTED_GRANULARITY, but that governs `time_granularity` on the delivery
# READ against `windowed_pull_granularities`, a different field.
#
# WHY A REFUSAL AND NOT A SKIP. The scheduler used to hold a skip for a non-daily
# cadence and it was DEAD: it read `reporting_webhook["frequency"]` while the
# stored document carries `reporting_frequency`, so a non-daily registration was
# DELIVERED DAILY rather than refused or honoured. Repairing that key would have
# turned an existing buyer's daily reports into silence — a
# delivered-to-never-delivered change this repo requires an owner sign-off for —
# and would have told the buyer nothing, in a background batch with no request to
# answer. The refusal moves the decision to the only moment the buyer can act on
# it. The dead skip is deleted rather than repaired, because a refusal at
# registration makes it unreachable.
#
# THE ACCEPTED CASE IS GRADED TOO, deliberately. A feature that only asserted the
# two refusals would pass if this seller refused EVERY cadence, including the one
# it advertises — the refusal's own population has to be bounded by the scenario
# that shows daily still works.
#
# WHAT THE REFUSAL SCENARIOS ASSERT, and what they must NOT. They assert the
# refusal's own markers: the code, the field, and that the error NAMES the cadence
# the products do advertise. They must not assert merely that no delivery
# happened — that sentinel is also produced by the correlation-id suppression
# (salesagent-8qela.172), so a scenario satisfied by either would grade nothing.
#
# UNGRADED BY STORYBOARD. Nothing under dist/compliance/ registers a
# reporting_webhook at a cadence the seller does not advertise: the conformance
# runner emits schema-valid payloads, and hourly IS schema-valid — the refusal
# comes from the product's declared capability rather than from the schema. So
# this file is the only grading, which is why it covers all three cadences.
#
# The @reporting_cadence tag routes this to the create env via its own ENV_ROUTES
# row, pinned in EXPECTED_WIRED_ROUTES in the same change: these scenarios
# dispatch a real create_media_buy and need the full create dependency chain, and
# without the row they would auto-xfail at fixture setup and grade nothing while
# reading green (salesagent-8qela.152 is that hazard met in the other direction).
Feature: A reporting cadence the products do not advertise is refused at registration (local, L3)

  @T-UC-004-reporting-cadence-accepted @reporting_cadence @invariant
  Scenario: a daily reporting_webhook is accepted
    When the buyer creates a media buy registering a reporting_webhook at "daily"
    Then the response should succeed

  @T-UC-004-reporting-cadence-hourly-refused @reporting_cadence @invariant
  Scenario: an hourly reporting_webhook is refused, naming the cadence on offer
    When the buyer creates a media buy registering a reporting_webhook at "hourly"
    Then the response arrives
    And the response contains error code UNSUPPORTED_FEATURE
    And the response error field is reporting_webhook.reporting_frequency
    And the refusal names "daily" as an available reporting cadence

  @T-UC-004-reporting-cadence-monthly-refused @reporting_cadence @invariant
  Scenario: a monthly reporting_webhook is refused, naming the cadence on offer
    When the buyer creates a media buy registering a reporting_webhook at "monthly"
    Then the response arrives
    And the response contains error code UNSUPPORTED_FEATURE
    And the response error field is reporting_webhook.reporting_frequency
    And the refusal names "daily" as an available reporting cadence
