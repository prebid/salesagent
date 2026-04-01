# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-024 Content Compliance
  As a Buyer or Seller
  I want to calibrate content evaluation models, retrieve media buy artifacts, and validate content delivery against brand safety standards
  So that advertising delivery contexts meet agreed brand safety and suitability standards throughout the campaign lifecycle

  # Postconditions verified:
  #   POST-S1: Seller has received a calibration verdict (pass/fail) with detailed explanation for a submitted artifact
  #   POST-S2: Buyer has received content artifacts from a media buy, sampled according to the specified rate and method
  #   POST-S3: Buyer has received per-record validation verdicts (pass/fail) with optional feature breakdowns
  #   POST-S4: Buyer can detect drift between Seller's local verdicts and Verification Agent's independent assessment
  #   POST-S5: Application context from the request is echoed unchanged in the response
  #   POST-S6: Artifact sampling metadata is reported alongside artifact results
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer or Seller knows what failed and the specific error code
  #   POST-F3: Application context is still echoed when possible
  #
  # Rules: BR-RULE-179..188 (10 rules, ~37 invariants)
  # Extensions: A (Calibrate Content), B (Validate Content Delivery),
  #   C (STANDARDS_NOT_FOUND), D (MEDIA_BUY_NOT_FOUND), E (RECORDS_REQUIRED),
  #   F (RECORDS_LIMIT_EXCEEDED), G (ARTIFACT_REQUIRED), H (SAMPLING_RATE_INVALID),
  #   I (PAGINATION_INVALID)
  # Error codes: STANDARDS_NOT_FOUND, MEDIA_BUY_NOT_FOUND, RECORDS_REQUIRED,
  #   RECORDS_LIMIT_EXCEEDED, ARTIFACT_REQUIRED, SAMPLING_RATE_INVALID,
  #   SAMPLING_METHOD_INVALID, PAGINATION_INVALID, PAGINATION_CURSOR_INVALID,
  #   FEATURE_IDS_EMPTY, INCLUDE_PASSED_INVALID_TYPE, VERDICT_REQUIRED,
  #   VERDICT_INVALID, CONFIDENCE_OUT_OF_RANGE, FEATURE_STATUS_INVALID,
  #   LOCAL_VERDICT_INVALID, SUMMARY_INCOMPLETE, SUMMARY_COUNTS_MISMATCH,
  #   CONTEXT_NOT_FOUND

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id


  @T-UC-024-main-mcp @main-flow @get-artifacts @mcp @post-s2 @post-s5 @post-s6
  Scenario: Get media buy artifacts via MCP -- success with default sampling and pagination
    Given a media buy "mb-001" exists for the authenticated buyer
    And the media buy has 500 delivered impressions with content artifacts
    When the Buyer Agent invokes get_media_buy_artifacts via MCP with media_buy_id "mb-001" and context "ctx-abc"
    Then the response contains media_buy_id "mb-001"
    And the response contains an artifacts array with sampled delivery records
    And each artifact record includes record_id and artifact with property_id, artifact_id, and assets
    And the response includes sampling_info with total_deliveries, sampled_count, effective_rate, and method
    And the response includes pagination with default max_results of 1000
    And the response echoes the request context "ctx-abc"
    # POST-S2: Buyer received sampled artifacts
    # POST-S5: Context echoed
    # POST-S6: Sampling metadata reported

  @T-UC-024-main-rest @main-flow @get-artifacts @rest @post-s2 @post-s5 @post-s6
  Scenario: Get media buy artifacts via REST/A2A -- success with default sampling and pagination
    Given a media buy "mb-002" exists for the authenticated buyer
    And the media buy has 2000 delivered impressions with content artifacts
    When the Buyer Agent sends get_media_buy_artifacts A2A task with media_buy_id "mb-002" and context "ctx-def"
    Then the response contains media_buy_id "mb-002"
    And the response contains an artifacts array with sampled delivery records
    And the response includes sampling_info with total_deliveries, sampled_count, effective_rate, and method
    And the response echoes the request context "ctx-def"
    # POST-S2: Buyer received sampled artifacts
    # POST-S5: Context echoed
    # POST-S6: Sampling metadata reported

  @T-UC-024-main-drift @main-flow @get-artifacts @drift-detection @post-s4
  Scenario: Get media buy artifacts -- Seller includes local_verdict for drift detection
    Given a media buy "mb-003" exists for the authenticated buyer
    And the Seller operates a local content evaluation model
    And the media buy has delivery records with local verdicts attached
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-003"
    Then the response contains artifacts with local_verdict field on each record
    And local_verdict values are each one of "pass", "fail", or "unevaluated"
    # POST-S4: Buyer can detect drift between local and independent verdicts

  @T-UC-024-main-no-local @main-flow @get-artifacts @drift-detection @post-s4
  Scenario: Get media buy artifacts -- local_verdict absent when Seller has no local model
    Given a media buy "mb-004" exists for the authenticated buyer
    And the Seller does not operate a local content evaluation model
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-004"
    Then the response contains artifacts without local_verdict field
    # POST-S4: No local model, no drift detection data

  @T-UC-024-main-filters @main-flow @get-artifacts @filtering
  Scenario: Get media buy artifacts -- with account, package_ids, and time_range filters
    Given a media buy "mb-005" exists for the authenticated buyer
    And the media buy has artifacts across multiple packages and time ranges
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-005", account "acct-1", package_ids ["pkg-1"], and time_range from "2026-01-01T00:00:00Z" to "2026-01-31T23:59:59Z"
    Then the response contains only artifacts matching the specified filters
    And the sampling_info reflects the filtered result set

  @T-UC-024-main-pagination-cursor @main-flow @get-artifacts @pagination
  Scenario: Get media buy artifacts -- cursor-based pagination across multiple pages
    Given a media buy "mb-006" exists with 5000 delivery artifacts
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-006" and pagination max_results 1000
    Then the response contains up to 1000 artifacts
    And the response includes a next_cursor for fetching the next page
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-006" and pagination cursor from the previous response
    Then the response contains the next page of artifacts

  @T-UC-024-ext-a-pass @extension @ext-a @calibration @happy-path @post-s1 @post-s5
  Scenario: Calibrate content -- artifact passes with full verdict details
    Given a content standard "std-001" exists
    And a valid artifact with property_id "prop-1", artifact_id "art-1", and text asset "Safe branded content"
    When the Seller invokes calibrate_content with standards_id "std-001" and the artifact with context "ctx-cal"
    Then the response contains verdict "pass"
    And the response optionally includes confidence between 0 and 1
    And the response optionally includes explanation text
    And the response optionally includes features array with per-feature breakdown
    And the response echoes the request context "ctx-cal"
    # POST-S1: Seller received pass verdict with explanation
    # POST-S5: Context echoed

  @T-UC-024-ext-a-fail @extension @ext-a @calibration @happy-path @post-s1
  Scenario: Calibrate content -- artifact fails with per-feature breakdown
    Given a content standard "std-002" exists
    And a valid artifact with property_id "prop-2", artifact_id "art-2", and text asset "Controversial content"
    When the Seller invokes calibrate_content with standards_id "std-002" and the artifact
    Then the response contains verdict "fail"
    And the response includes features array with at least one feature having status "failed"
    And each feature in the breakdown has feature_id and status
    # POST-S1: Seller received fail verdict with feature breakdown

  @T-UC-024-ext-a-dialogue @extension @ext-a @calibration @dialogue @happy-path @post-s1
  Scenario: Calibrate content -- multi-turn dialogue via protocol-layer context
    Given a content standard "std-003" exists
    And the Seller has an active calibration conversation with contextId "conv-001"
    When the Seller submits a follow-up artifact in the same conversation using contextId "conv-001"
    Then the Verification Agent evaluates the artifact within the accumulated dialogue context
    And the response contains a verdict with optional explanation
    # POST-S1: Seller received verdict in multi-turn dialogue

  @T-UC-024-ext-a-question @extension @ext-a @calibration @dialogue @happy-path
  Scenario: Calibrate content -- follow-up question within same conversation
    Given a content standard "std-004" exists
    And the Seller received a previous calibration verdict in conversation "conv-002"
    When the Seller submits a follow-up question about the verdict using contextId "conv-002"
    Then the Verification Agent responds within the same conversation context
    And the protocol layer correlates the exchange via contextId

  @T-UC-024-ext-a-async @extension @ext-a @calibration @dialogue @async
  Scenario: Calibrate content -- async pause for human review
    Given a content standard "std-005" exists
    And the Seller has submitted an artifact for calibration in conversation "conv-003"
    When neither party has responded yet
    Then the conversation remains open and supports async pause for human review

  @T-UC-024-ext-b-success @extension @ext-b @validation @happy-path @post-s3 @post-s5
  Scenario: Validate content delivery -- batch with mixed pass/fail results
    Given a content standard "std-010" exists
    And 100 delivery records each with valid record_id and artifact
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-010" and 100 records with context "ctx-val"
    Then the response contains summary with total_records 100
    And summary passed_records + failed_records equals total_records
    And the response contains results array with per-record record_id and verdict
    And the response echoes the request context "ctx-val"
    # POST-S3: Buyer received per-record verdicts
    # POST-S5: Context echoed

  @T-UC-024-ext-b-features @extension @ext-b @validation @feature-filtering @happy-path @post-s3
  Scenario: Validate content delivery -- with feature_ids filter for selective evaluation
    Given a content standard "std-011" exists with features "brand_safety" and "brand_suitability"
    And 10 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-011", records, and feature_ids ["brand_safety"]
    Then the response contains results with per-record verdicts
    And each result's features array only includes the requested "brand_safety" feature
    And each feature has feature_id and status
    # POST-S3: Buyer received feature-filtered verdicts

  @T-UC-024-ext-b-include-passed-false @extension @ext-b @validation @result-filtering @happy-path @post-s3
  Scenario: Validate content delivery -- include_passed=false filters results to failures only
    Given a content standard "std-012" exists
    And 50 delivery records where 45 pass and 5 fail
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-012", records, and include_passed false
    Then the response results array contains only the 5 failing records
    And the response summary shows total_records 50, passed_records 45, failed_records 5
    # POST-S3: Buyer received only failing records in results
    # Summary still counts ALL records (BR-RULE-187 INV-3)

  @T-UC-024-ext-b-drift @extension @ext-b @validation @drift-detection @happy-path @post-s4
  Scenario: Validate content delivery -- drift detection by comparing verdicts against local_verdict
    Given a content standard "std-013" exists
    And artifacts previously retrieved from get_media_buy_artifacts include local_verdict on each record
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-013" and those records
    Then the Buyer can compare each record's validation verdict against the Seller's local_verdict
    And any discrepancy indicates model drift
    # POST-S4: Buyer can detect drift

  @T-UC-024-ext-b-all-features @extension @ext-b @validation @feature-filtering @happy-path
  Scenario: Validate content delivery -- feature_ids omitted evaluates all features
    Given a content standard "std-014" exists with features "brand_safety", "brand_suitability", and "competitor_adjacency"
    And 5 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-014" and records without feature_ids
    Then all features defined in the content standard are evaluated
    And results include feature breakdown covering all three features

  @T-UC-024-ext-b-include-passed-default @extension @ext-b @validation @result-filtering @happy-path
  Scenario: Validate content delivery -- include_passed defaults to true showing all records
    Given a content standard "std-015" exists
    And 20 delivery records where 15 pass and 5 fail
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-015" and records without include_passed
    Then the response results array contains all 20 records (both passing and failing)
    And the response summary shows total_records 20, passed_records 15, failed_records 5

  @T-UC-024-ext-c-calibrate @extension @ext-c @error @standards-not-found
  Scenario: Calibrate content -- STANDARDS_NOT_FOUND when standards_id does not exist
    Given no content standard exists with standards_id "nonexistent-std"
    And a valid artifact with required fields
    When the Seller invokes calibrate_content with standards_id "nonexistent-std" and the artifact
    Then the operation should fail
    And the error code should be "STANDARDS_NOT_FOUND"
    And the error message should contain "nonexistent-std"
    And the error should include "suggestion" field
    And the suggestion should contain "content standard"
    # POST-F1: System state unchanged
    # POST-F2: Error explains standards_id not found
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-c-validate @extension @ext-c @error @standards-not-found
  Scenario: Validate content delivery -- STANDARDS_NOT_FOUND when standards_id does not exist
    Given no content standard exists with standards_id "missing-std"
    And 5 valid delivery records
    When the Buyer Agent invokes validate_content_delivery with standards_id "missing-std" and records
    Then the operation should fail
    And the error code should be "STANDARDS_NOT_FOUND"
    And the error message should contain "missing-std"
    And the error should include "suggestion" field
    And the suggestion should contain "content standard"
    # POST-F1: System state unchanged
    # POST-F2: Error explains standards_id not found
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-d-mcp @extension @ext-d @error @media-buy-not-found
  Scenario: Get media buy artifacts via MCP -- MEDIA_BUY_NOT_FOUND
    Given no media buy exists with media_buy_id "nonexistent-mb"
    When the Buyer Agent invokes get_media_buy_artifacts via MCP with media_buy_id "nonexistent-mb"
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error message should contain "nonexistent-mb"
    And the error should include "suggestion" field
    And the suggestion should contain "media buy"
    # POST-F1: System state unchanged
    # POST-F2: Error explains media_buy_id not found
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-d-rest @extension @ext-d @error @media-buy-not-found
  Scenario: Get media buy artifacts via REST -- MEDIA_BUY_NOT_FOUND
    Given no media buy exists with media_buy_id "missing-mb"
    When the Buyer Agent sends get_media_buy_artifacts A2A task with media_buy_id "missing-mb"
    Then the operation should fail
    And the error code should be "MEDIA_BUY_NOT_FOUND"
    And the error message should contain "missing-mb"
    And the error should include "suggestion" field
    And the suggestion should contain "media buy"
    # POST-F1: System state unchanged
    # POST-F2: Error explains media_buy_id not found
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-e-empty @extension @ext-e @error @records-required
  Scenario: Validate content delivery -- RECORDS_REQUIRED when records array is empty
    Given a content standard "std-020" exists
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-020" and an empty records array
    Then the operation should fail
    And the error code should be "RECORDS_REQUIRED"
    And the error message should contain "records"
    And the error should include "suggestion" field
    And the suggestion should contain "at least 1 delivery record"
    # POST-F1: System state unchanged
    # POST-F2: Error explains records required
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-e-absent @extension @ext-e @error @records-required
  Scenario: Validate content delivery -- RECORDS_REQUIRED when records field is absent
    Given a content standard "std-021" exists
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-021" and no records field
    Then the operation should fail
    And the error code should be "RECORDS_REQUIRED"
    And the error message should contain "records"
    And the error should include "suggestion" field
    And the suggestion should contain "at least 1 delivery record"
    # POST-F1: System state unchanged
    # POST-F2: Error explains records required
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-f @extension @ext-f @error @records-limit-exceeded
  Scenario: Validate content delivery -- RECORDS_LIMIT_EXCEEDED when records exceed 10,000
    Given a content standard "std-030" exists
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-030" and 10001 records
    Then the operation should fail
    And the error code should be "RECORDS_LIMIT_EXCEEDED"
    And the error message should contain "10,000"
    And the error should include "suggestion" field
    And the suggestion should contain "split the batch"
    # POST-F1: System state unchanged
    # POST-F2: Error explains limit exceeded
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-g-calibrate @extension @ext-g @error @artifact-required
  Scenario: Calibrate content -- ARTIFACT_REQUIRED when artifact missing property_id
    Given a content standard "std-040" exists
    And an artifact missing property_id but with artifact_id and assets
    When the Seller invokes calibrate_content with standards_id "std-040" and the incomplete artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "property_id"
    And the error should include "suggestion" field
    And the suggestion should contain "property_id"
    # POST-F1: System state unchanged
    # POST-F2: Error identifies missing field
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-g-validate @extension @ext-g @error @artifact-required
  Scenario: Validate content delivery -- ARTIFACT_REQUIRED when record artifact missing assets
    Given a content standard "std-041" exists
    And a delivery record where the artifact is missing the assets array
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-041" and the record
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "assets"
    And the error should include "suggestion" field
    And the suggestion should contain "property_id, artifact_id, and at least one asset"
    # POST-F1: System state unchanged
    # POST-F2: Error identifies missing field
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-h-below @extension @ext-h @error @sampling-rate-invalid
  Scenario: Get media buy artifacts -- SAMPLING_RATE_INVALID when rate is below 0
    Given a media buy "mb-050" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-050" and sampling rate -0.1
    Then the operation should fail
    And the error code should be "SAMPLING_RATE_INVALID"
    And the error message should contain "0"
    And the error message should contain "1"
    And the error should include "suggestion" field
    And the suggestion should contain "between 0"
    # POST-F1: System state unchanged
    # POST-F2: Error explains rate range
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-h-above @extension @ext-h @error @sampling-rate-invalid
  Scenario: Get media buy artifacts -- SAMPLING_RATE_INVALID when rate is above 1
    Given a media buy "mb-051" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-051" and sampling rate 1.5
    Then the operation should fail
    And the error code should be "SAMPLING_RATE_INVALID"
    And the error message should contain "0"
    And the error message should contain "1"
    And the error should include "suggestion" field
    And the suggestion should contain "between 0"
    # POST-F1: System state unchanged
    # POST-F2: Error explains rate range
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-i-zero @extension @ext-i @error @pagination-invalid
  Scenario: Get media buy artifacts -- PAGINATION_INVALID when max_results is 0
    Given a media buy "mb-060" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-060" and pagination max_results 0
    Then the operation should fail
    And the error code should be "PAGINATION_INVALID"
    And the error message should contain "1"
    And the error message should contain "10,000"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 10,000"
    # POST-F1: System state unchanged
    # POST-F2: Error explains range
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-i-exceeds @extension @ext-i @error @pagination-invalid
  Scenario: Get media buy artifacts -- PAGINATION_INVALID when max_results exceeds 10,000
    Given a media buy "mb-061" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-061" and pagination max_results 10001
    Then the operation should fail
    And the error code should be "PAGINATION_INVALID"
    And the error message should contain "10,000"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 10,000"
    # POST-F1: System state unchanged
    # POST-F2: Error explains range
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-i-negative @extension @ext-i @error @pagination-invalid
  Scenario: Get media buy artifacts -- PAGINATION_INVALID when max_results is negative
    Given a media buy "mb-062" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-062" and pagination max_results -1
    Then the operation should fail
    And the error code should be "PAGINATION_INVALID"
    And the error message should contain "1"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 10,000"
    # POST-F1: System state unchanged
    # POST-F2: Error explains range
    # POST-F3: Context echoed when possible

  @T-UC-024-ext-i-cursor @extension @ext-i @error @pagination-cursor-invalid
  Scenario: Get media buy artifacts -- PAGINATION_CURSOR_INVALID when cursor is expired
    Given a media buy "mb-063" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-063" and pagination cursor "expired_xyz"
    Then the operation should fail
    And the error code should be "PAGINATION_CURSOR_INVALID"
    And the error message should contain "cursor"
    And the error should include "suggestion" field
    And the suggestion should contain "omit cursor to start from the beginning"
    # POST-F1: System state unchanged
    # POST-F2: Error identifies invalid cursor
    # POST-F3: Context echoed when possible

  @T-UC-024-inv-179-holds @invariant @BR-RULE-179
  Scenario: BR-RULE-179 INV-1..3 holds -- complete artifact with all required fields accepted
    Given a content standard "std-100" exists
    And an artifact with property_id "prop-10", artifact_id "art-10", and assets containing a text asset with content "Valid article text"
    When the Seller invokes calibrate_content with standards_id "std-100" and the artifact
    Then the operation succeeds with a verdict

  @T-UC-024-inv-179-text-holds @invariant @BR-RULE-179
  Scenario: BR-RULE-179 INV-4 holds -- text asset with content field accepted
    Given a content standard "std-101" exists
    And an artifact with a text asset that includes both "type" and "content" fields
    When the Seller invokes calibrate_content with standards_id "std-101" and the artifact
    Then the operation succeeds with a verdict

  @T-UC-024-inv-179-media-holds @invariant @BR-RULE-179
  Scenario: BR-RULE-179 INV-5 holds -- image/video/audio asset with url field accepted
    Given a content standard "std-102" exists
    And an artifact with an image asset that includes "type" and "url" fields
    When the Seller invokes calibrate_content with standards_id "std-102" and the artifact
    Then the operation succeeds with a verdict

  @T-UC-024-inv-179-violated-property @invariant @BR-RULE-179 @error
  Scenario: BR-RULE-179 INV-1 violated -- artifact missing property_id rejected
    Given a content standard "std-103" exists
    And an artifact without property_id
    When the Seller invokes calibrate_content with standards_id "std-103" and the artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "property_id"
    And the error should include "suggestion" field
    And the suggestion should contain "property_id, artifact_id, and at least one asset"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-179-violated-artid @invariant @BR-RULE-179 @error
  Scenario: BR-RULE-179 INV-2 violated -- artifact missing artifact_id rejected
    Given a content standard "std-104" exists
    And an artifact without artifact_id
    When the Seller invokes calibrate_content with standards_id "std-104" and the artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "artifact_id"
    And the error should include "suggestion" field
    And the suggestion should contain "property_id, artifact_id, and at least one asset"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-179-violated-assets @invariant @BR-RULE-179 @error
  Scenario: BR-RULE-179 INV-3 violated -- artifact with empty assets rejected
    Given a content standard "std-105" exists
    And an artifact with property_id and artifact_id but empty assets array
    When the Seller invokes calibrate_content with standards_id "std-105" and the artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "assets"
    And the error should include "suggestion" field
    And the suggestion should contain "at least one asset"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-179-violated-text @invariant @BR-RULE-179 @error
  Scenario: BR-RULE-179 INV-4 violated -- text asset missing content rejected
    Given a content standard "std-106" exists
    And an artifact with a text asset that has type "text" but no content field
    When the Seller invokes calibrate_content with standards_id "std-106" and the artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "content"
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-179-violated-url @invariant @BR-RULE-179 @error
  Scenario: BR-RULE-179 INV-5 violated -- image asset missing url rejected
    Given a content standard "std-107" exists
    And an artifact with an image asset that has type "image" but no url field
    When the Seller invokes calibrate_content with standards_id "std-107" and the artifact
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error message should contain "url"
    And the error should include "suggestion" field
    And the suggestion should contain "required fields"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-180-holds @invariant @BR-RULE-180
  Scenario: BR-RULE-180 INV-1..2 holds -- records array within 1-10,000 range accepted
    Given a content standard "std-110" exists
    And 100 delivery records each with record_id and valid artifact
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-110" and 100 records
    Then the operation succeeds with summary and results

  @T-UC-024-inv-180-violated-empty @invariant @BR-RULE-180 @error
  Scenario: BR-RULE-180 INV-1 violated -- empty records array rejected
    Given a content standard "std-111" exists
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-111" and 0 records
    Then the operation should fail
    And the error code should be "RECORDS_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "at least 1"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-180-violated-exceeds @invariant @BR-RULE-180 @error
  Scenario: BR-RULE-180 INV-2 violated -- records exceeding 10,000 rejected
    Given a content standard "std-112" exists
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-112" and 10001 records
    Then the operation should fail
    And the error code should be "RECORDS_LIMIT_EXCEEDED"
    And the error should include "suggestion" field
    And the suggestion should contain "split the batch"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-180-violated-noid @invariant @BR-RULE-180 @error
  Scenario: BR-RULE-180 INV-3 violated -- record missing record_id rejected
    Given a content standard "std-113" exists
    And a delivery record without record_id but with valid artifact
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-113" and the record
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "record_id"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-180-violated-noart @invariant @BR-RULE-180 @error
  Scenario: BR-RULE-180 INV-4 violated -- record missing artifact rejected
    Given a content standard "std-114" exists
    And a delivery record with record_id but without artifact
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-114" and the record
    Then the operation should fail
    And the error code should be "ARTIFACT_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "artifact"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-181-binary @invariant @BR-RULE-181
  Scenario: BR-RULE-181 INV-1 holds -- calibration verdict is exactly pass or fail
    Given a content standard "std-120" exists
    And a valid artifact for calibration
    When the Seller invokes calibrate_content with standards_id "std-120" and the artifact
    Then the response verdict is either "pass" or "fail" with no other values

  @T-UC-024-inv-181-confidence @invariant @BR-RULE-181
  Scenario: BR-RULE-181 INV-2 holds -- confidence score is between 0 and 1
    Given a content standard "std-121" exists
    And a valid artifact for calibration
    When the Seller invokes calibrate_content with standards_id "std-121" and the artifact
    And the response includes confidence
    Then the confidence value is between 0 and 1 inclusive

  @T-UC-024-inv-181-features @invariant @BR-RULE-181
  Scenario: BR-RULE-181 INV-3 holds -- per-feature breakdown has feature_id and status
    Given a content standard "std-122" exists
    And a valid artifact for calibration
    When the Seller invokes calibrate_content with standards_id "std-122" and the artifact
    And the response includes features array
    Then each feature has feature_id and status from {passed, failed, warning, unevaluated}

  @T-UC-024-inv-181-unevaluated @invariant @BR-RULE-181
  Scenario: BR-RULE-181 INV-4 holds -- unevaluated feature was not assessed
    Given a content standard "std-123" exists with features including "competitor_adjacency"
    And a valid artifact for calibration
    When the Seller invokes calibrate_content with feature_ids that exclude "competitor_adjacency"
    Then the "competitor_adjacency" feature shows status "unevaluated" in the breakdown

  @T-UC-024-inv-182-followup @invariant @BR-RULE-182
  Scenario: BR-RULE-182 INV-1 holds -- follow-up in same conversation correlated via contextId
    Given a content standard "std-130" exists
    And a calibration conversation is active with contextId "conv-100"
    When the Seller submits a follow-up artifact using contextId "conv-100"
    Then the protocol layer correlates the exchange within the same conversation

  @T-UC-024-inv-182-different @invariant @BR-RULE-182
  Scenario: BR-RULE-182 INV-2 holds -- different artifact in same conversation evaluated in accumulated context
    Given a content standard "std-131" exists
    And a calibration conversation is active with contextId "conv-101" from a previous artifact evaluation
    When the Seller submits a different artifact using contextId "conv-101"
    Then the Verification Agent evaluates it within the accumulated dialogue context

  @T-UC-024-inv-182-async @invariant @BR-RULE-182
  Scenario: BR-RULE-182 INV-3 holds -- conversation stays open during async pause
    Given a calibration conversation is active with contextId "conv-102"
    When neither party responds within the session timeout
    Then the conversation remains open and supports async pause for human review

  @T-UC-024-inv-182-violated @invariant @BR-RULE-182 @error
  Scenario: BR-RULE-182 violated -- invalid contextId rejected
    Given a content standard "std-132" exists
    When the Seller submits a calibration follow-up using contextId "nonexistent-conv"
    Then the operation should fail
    And the error code should be "CONTEXT_NOT_FOUND"
    And the error message should contain "nonexistent-conv"
    And the error should include "suggestion" field
    And the suggestion should contain "new calibration conversation"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-183-default @invariant @BR-RULE-183
  Scenario: BR-RULE-183 INV-2 holds -- sampling omitted defaults to media buy rate
    Given a media buy "mb-130" exists with an agreed sampling rate of 0.25
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-130" without sampling parameter
    Then the response sampling_info reflects the media buy's default rate
    And sampling_info.effective_rate is approximately 0.25

  @T-UC-024-inv-183-method @invariant @BR-RULE-183
  Scenario: BR-RULE-183 INV-3 holds -- valid sampling method accepted
    Given a media buy "mb-131" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with sampling method "stratified"
    Then the response sampling_info.method reflects "stratified"

  @T-UC-024-inv-183-info @invariant @BR-RULE-183
  Scenario: BR-RULE-183 INV-4 holds -- sampling_info included in success response
    Given a media buy "mb-132" exists with delivery artifacts
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-132"
    Then the response includes sampling_info with total_deliveries, sampled_count, effective_rate, and method

  @T-UC-024-inv-183-violated @invariant @BR-RULE-183 @error
  Scenario: BR-RULE-183 INV-1 violated -- sampling rate outside 0-1 rejected
    Given a media buy "mb-133" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with sampling rate 2.0
    Then the operation should fail
    And the error code should be "SAMPLING_RATE_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "between 0"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-183-method-invalid @invariant @BR-RULE-183 @error
  Scenario: BR-RULE-183 INV-3 violated -- invalid sampling method rejected
    Given a media buy "mb-134" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with sampling method "alphabetical"
    Then the operation should fail
    And the error code should be "SAMPLING_METHOD_INVALID"
    And the error message should contain "alphabetical"
    And the error should include "suggestion" field
    And the suggestion should contain "supported sampling methods"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-184-default @invariant @BR-RULE-184
  Scenario: BR-RULE-184 INV-2 holds -- pagination omitted defaults to max_results 1000
    Given a media buy "mb-140" exists with 3000 delivery artifacts
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-140" without pagination
    Then the response returns up to 1000 artifacts (the default page size)

  @T-UC-024-inv-184-cursor @invariant @BR-RULE-184
  Scenario: BR-RULE-184 INV-3 holds -- next_cursor provided when more results exist
    Given a media buy "mb-141" exists with 5000 delivery artifacts
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-141" and max_results 1000
    Then the response includes pagination.next_cursor for the next page

  @T-UC-024-inv-184-violated @invariant @BR-RULE-184 @error
  Scenario: BR-RULE-184 INV-1 violated -- max_results outside 1-10,000 rejected
    Given a media buy "mb-142" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with pagination max_results 0
    Then the operation should fail
    And the error code should be "PAGINATION_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 10,000"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-184-cursor-violated @invariant @BR-RULE-184 @error
  Scenario: BR-RULE-184 INV-4 violated -- invalid cursor rejected
    Given a media buy "mb-143" exists for the authenticated buyer
    When the Buyer Agent invokes get_media_buy_artifacts with pagination cursor "invalid_state_abc"
    Then the operation should fail
    And the error code should be "PAGINATION_CURSOR_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "omit cursor"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-185-present @invariant @BR-RULE-185
  Scenario: BR-RULE-185 INV-1 holds -- local_verdict present when Seller has local model
    Given a media buy "mb-150" exists for the authenticated buyer
    And the Seller operates a local content evaluation model
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-150"
    Then each artifact record includes local_verdict with value "pass", "fail", or "unevaluated"

  @T-UC-024-inv-185-absent @invariant @BR-RULE-185
  Scenario: BR-RULE-185 INV-2 holds -- local_verdict absent when Seller has no local model
    Given a media buy "mb-151" exists for the authenticated buyer
    And the Seller does not operate a local content evaluation model
    When the Buyer Agent invokes get_media_buy_artifacts with media_buy_id "mb-151"
    Then artifact records do not include local_verdict field

  @T-UC-024-inv-185-failures @invariant @BR-RULE-185
  Scenario: BR-RULE-185 INV-3 holds -- failures_only method returns only local_verdict=fail records
    Given a media buy "mb-152" exists with mixed local verdicts (pass, fail, unevaluated)
    When the Buyer Agent invokes get_media_buy_artifacts with sampling method "failures_only"
    Then all returned artifact records have local_verdict "fail"

  @T-UC-024-inv-186-omitted @invariant @BR-RULE-186
  Scenario: BR-RULE-186 INV-1 holds -- feature_ids omitted evaluates all features
    Given a content standard "std-160" exists with features "brand_safety", "brand_suitability"
    And 5 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery without feature_ids
    Then all features are evaluated for each record

  @T-UC-024-inv-186-specified @invariant @BR-RULE-186
  Scenario: BR-RULE-186 INV-2 holds -- feature_ids with specific IDs evaluates only those features
    Given a content standard "std-161" exists with features "brand_safety", "brand_suitability", "competitor_adjacency"
    And 5 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with feature_ids ["brand_safety"]
    Then only "brand_safety" is evaluated for each record
    And other features are not included in the results breakdown

  @T-UC-024-inv-186-violated @invariant @BR-RULE-186 @error
  Scenario: BR-RULE-186 INV-3 violated -- empty feature_ids array rejected
    Given a content standard "std-162" exists
    And 5 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with feature_ids as an empty array
    Then the operation should fail
    And the error code should be "FEATURE_IDS_EMPTY"
    And the error message should contain "feature_ids"
    And the error should include "suggestion" field
    And the suggestion should contain "one or more feature IDs"
    # POST-F3: Recovery suggestion provided

  @T-UC-024-inv-187-true @invariant @BR-RULE-187
  Scenario: BR-RULE-187 INV-1 holds -- include_passed true shows all records in results
    Given a content standard "std-170" exists
    And 20 delivery records where 15 pass and 5 fail
    When the Buyer Agent invokes validate_content_delivery with include_passed true
    Then the results array contains all 20 records

  @T-UC-024-inv-187-false @invariant @BR-RULE-187
  Scenario: BR-RULE-187 INV-2 holds -- include_passed false shows only failing records
    Given a content standard "std-171" exists
    And 20 delivery records where 15 pass and 5 fail
    When the Buyer Agent invokes validate_content_delivery with include_passed false
    Then the results array contains only the 5 failing records

  @T-UC-024-inv-187-summary @invariant @BR-RULE-187
  Scenario: BR-RULE-187 INV-3 holds -- summary counts all records even when include_passed false
    Given a content standard "std-172" exists
    And 50 delivery records where 40 pass and 10 fail
    When the Buyer Agent invokes validate_content_delivery with include_passed false
    Then the summary shows total_records 50, passed_records 40, failed_records 10
    And the results array contains only the 10 failing records

  @T-UC-024-inv-188-present @invariant @BR-RULE-188
  Scenario: BR-RULE-188 INV-1 holds -- summary present with all three fields on success
    Given a content standard "std-180" exists
    And 10 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-180" and 10 records
    Then the response includes summary with total_records, passed_records, and failed_records

  @T-UC-024-inv-188-additive @invariant @BR-RULE-188
  Scenario: BR-RULE-188 INV-2 holds -- total_records equals passed_records plus failed_records
    Given a content standard "std-181" exists
    And 100 delivery records with valid artifacts
    When the Buyer Agent invokes validate_content_delivery with standards_id "std-181" and 100 records
    Then summary.total_records equals summary.passed_records plus summary.failed_records

  @T-UC-024-inv-188-filtered @invariant @BR-RULE-188
  Scenario: BR-RULE-188 INV-3 holds -- summary reflects all records even when results filtered
    Given a content standard "std-182" exists
    And 100 delivery records where 90 pass and 10 fail
    When the Buyer Agent invokes validate_content_delivery with include_passed false
    Then summary.total_records is 100
    And summary.passed_records is 90
    And summary.failed_records is 10
    And the results array contains only 10 records

  @T-UC-024-partition-artifact @partition @artifact
  Scenario Outline: Artifact structure partition validation - <partition>
    Given a content standard exists
    And an artifact conforming to <partition> partition
    When the artifact is submitted for calibration
    Then <outcome>

    Examples: Valid partitions
      | partition                | outcome                    |
      | complete_text_artifact   | the operation succeeds     |
      | complete_image_artifact  | the operation succeeds     |
      | multi_asset_artifact     | the operation succeeds     |
      | all_optional_fields      | the operation succeeds     |

    Examples: Invalid partitions
      | partition                  | outcome                                                  |
      | missing_property_id        | error "ARTIFACT_REQUIRED" with suggestion                |
      | missing_artifact_id        | error "ARTIFACT_REQUIRED" with suggestion                |
      | missing_assets             | error "ARTIFACT_REQUIRED" with suggestion                |
      | empty_assets               | error "ARTIFACT_REQUIRED" with suggestion                |
      | asset_missing_type         | error "ARTIFACT_REQUIRED" with suggestion                |
      | text_asset_missing_content | error "ARTIFACT_REQUIRED" with suggestion                |

  @T-UC-024-partition-records @partition @records
  Scenario Outline: Delivery records batch partition validation - <partition>
    Given a content standard exists
    And a records array conforming to <partition> partition
    When the records are submitted for validation
    Then <outcome>

    Examples: Valid partitions
      | partition           | outcome                |
      | single_record       | the operation succeeds |
      | typical_batch       | the operation succeeds |
      | maximum_batch       | the operation succeeds |
      | multi_buy_batch     | the operation succeeds |

    Examples: Invalid partitions
      | partition                | outcome                                                    |
      | empty_records            | error "RECORDS_REQUIRED" with suggestion                   |
      | missing_records          | error "RECORDS_REQUIRED" with suggestion                   |
      | exceeds_limit            | error "RECORDS_LIMIT_EXCEEDED" with suggestion             |
      | record_missing_record_id | error "ARTIFACT_REQUIRED" with suggestion                  |
      | record_missing_artifact  | error "ARTIFACT_REQUIRED" with suggestion                  |

  @T-UC-024-partition-sampling @partition @sampling
  Scenario Outline: Sampling parameter partition validation - <partition>
    Given a media buy exists for the authenticated buyer
    And a sampling configuration conforming to <partition> partition
    When the Buyer Agent requests media buy artifacts with the sampling configuration
    Then <outcome>

    Examples: Valid partitions
      | partition                 | outcome                |
      | omitted_defaults          | the operation succeeds |
      | explicit_rate_and_method  | the operation succeeds |
      | rate_only                 | the operation succeeds |
      | method_only               | the operation succeeds |
      | full_sample               | the operation succeeds |
      | zero_rate                 | the operation succeeds |

    Examples: Invalid partitions
      | partition         | outcome                                                   |
      | rate_below_zero   | error "SAMPLING_RATE_INVALID" with suggestion             |
      | rate_above_one    | error "SAMPLING_RATE_INVALID" with suggestion             |
      | invalid_method    | error "SAMPLING_METHOD_INVALID" with suggestion           |

  @T-UC-024-partition-pagination @partition @pagination
  Scenario Outline: Artifact pagination partition validation - <partition>
    Given a media buy exists for the authenticated buyer
    And a pagination configuration conforming to <partition> partition
    When the Buyer Agent requests media buy artifacts with the pagination configuration
    Then <outcome>

    Examples: Valid partitions
      | partition            | outcome                |
      | omitted_defaults     | the operation succeeds |
      | explicit_max_results | the operation succeeds |
      | minimum_page         | the operation succeeds |
      | maximum_page         | the operation succeeds |
      | with_cursor          | the operation succeeds |

    Examples: Invalid partitions
      | partition             | outcome                                                    |
      | max_results_zero      | error "PAGINATION_INVALID" with suggestion                 |
      | max_results_negative  | error "PAGINATION_INVALID" with suggestion                 |
      | max_results_exceeds   | error "PAGINATION_INVALID" with suggestion                 |
      | invalid_cursor        | error "PAGINATION_CURSOR_INVALID" with suggestion          |

  @T-UC-024-partition-verdict @partition @verdict
  Scenario Outline: Verdict model partition validation - <partition>
    Given a content standard exists
    And a verdict response conforming to <partition> partition
    When the verdict is evaluated
    Then <outcome>

    Examples: Valid partitions
      | partition              | outcome                    |
      | pass_verdict           | the operation succeeds     |
      | fail_verdict           | the operation succeeds     |
      | verdict_with_confidence | the operation succeeds    |
      | verdict_with_features  | the operation succeeds     |
      | feature_warning        | the operation succeeds     |
      | feature_unevaluated    | the operation succeeds     |

    Examples: Invalid partitions
      | partition                | outcome                                                    |
      | missing_verdict          | error "VERDICT_REQUIRED" with suggestion                   |
      | invalid_verdict_value    | error "VERDICT_INVALID" with suggestion                    |
      | confidence_below_zero    | error "CONFIDENCE_OUT_OF_RANGE" with suggestion            |
      | confidence_above_one     | error "CONFIDENCE_OUT_OF_RANGE" with suggestion            |
      | invalid_feature_status   | error "FEATURE_STATUS_INVALID" with suggestion             |

  @T-UC-024-partition-local-verdict @partition @local_verdict
  Scenario Outline: Local verdict partition validation - <partition>
    Given a media buy exists with delivery artifacts
    And artifact records conforming to <partition> partition
    When the artifacts are retrieved
    Then <outcome>

    Examples: Valid partitions
      | partition        | outcome                                        |
      | local_pass       | artifact includes local_verdict "pass"          |
      | local_fail       | artifact includes local_verdict "fail"          |
      | local_unevaluated | artifact includes local_verdict "unevaluated" |
      | absent           | artifact does not include local_verdict         |
      | drift_detected   | local_verdict differs from verification verdict |
      | aligned          | local_verdict matches verification verdict      |

    Examples: Invalid partitions
      | partition               | outcome                                                    |
      | invalid_local_verdict   | error "LOCAL_VERDICT_INVALID" with suggestion              |

  @T-UC-024-partition-feature-ids @partition @feature_ids
  Scenario Outline: Feature filtering partition validation - <partition>
    Given a content standard exists with defined features
    And a validation request conforming to <partition> partition
    When the validation is executed
    Then <outcome>

    Examples: Valid partitions
      | partition              | outcome                            |
      | omitted_all_features   | all features are evaluated         |
      | single_feature         | only the specified feature is evaluated |
      | multiple_features      | only the specified features are evaluated |

    Examples: Invalid partitions
      | partition           | outcome                                                   |
      | empty_feature_ids   | error "FEATURE_IDS_EMPTY" with suggestion                 |

  @T-UC-024-partition-include-passed @partition @include_passed
  Scenario Outline: Result filtering partition validation - <partition>
    Given a content standard exists
    And a validation request conforming to <partition> partition with mixed pass/fail records
    When the validation is executed
    Then <outcome>

    Examples: Valid partitions
      | partition            | outcome                                          |
      | default_include_all  | results contain all records                      |
      | explicit_true        | results contain all records                      |
      | exclude_passed       | results contain only failing records             |

    Examples: Invalid partitions
      | partition     | outcome                                                        |
      | non_boolean   | error "INCLUDE_PASSED_INVALID_TYPE" with suggestion            |

  @T-UC-024-partition-summary @partition @summary
  Scenario Outline: Summary counts partition validation - <partition>
    Given a validation completes successfully with records matching <partition>
    When the response is evaluated
    Then <outcome>

    Examples: Valid partitions
      | partition       | outcome                                                |
      | all_pass        | summary shows 0 failed_records                        |
      | all_fail        | summary shows 0 passed_records                        |
      | mixed           | summary shows non-zero passed and failed              |
      | single_record   | summary shows total_records 1                         |
      | maximum_batch   | summary shows total_records 10000                     |

    Examples: Invalid partitions
      | partition          | outcome                                                    |
      | missing_total      | error "SUMMARY_INCOMPLETE" with suggestion                 |
      | missing_passed     | error "SUMMARY_INCOMPLETE" with suggestion                 |
      | missing_failed     | error "SUMMARY_INCOMPLETE" with suggestion                 |
      | counts_mismatch    | error "SUMMARY_COUNTS_MISMATCH" with suggestion            |

  @T-UC-024-partition-dialogue @partition @dialogue
  Scenario Outline: Calibration dialogue partition validation - <partition>
    Given a content standard exists
    And a dialogue state conforming to <partition> partition
    When the calibration request is processed
    Then <outcome>

    Examples: Valid partitions
      | partition             | outcome                                |
      | single_turn           | verdict returned without continuation  |
      | multi_turn_artifact   | new artifact evaluated in context      |
      | multi_turn_question   | question answered within context       |
      | multi_turn_mixed      | mixed interactions handled in context  |

    Examples: Invalid partitions
      | partition              | outcome                                                  |
      | invalid_context_id     | error "CONTEXT_NOT_FOUND" with suggestion                |
      | missing_standards_id   | error "STANDARDS_NOT_FOUND" with suggestion              |

  @T-UC-024-boundary-artifact @boundary @artifact
  Scenario Outline: Artifact structure boundary validation - <boundary_point>
    Given a content standard exists
    And an artifact at the boundary: <boundary_point>
    When the artifact is submitted for calibration
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                  | outcome                                              |
      | artifact with exactly one asset (minimum valid) | the operation succeeds                               |
      | artifact missing property_id                    | error "ARTIFACT_REQUIRED" with suggestion            |
      | artifact missing artifact_id                    | error "ARTIFACT_REQUIRED" with suggestion            |
      | artifact missing assets                         | error "ARTIFACT_REQUIRED" with suggestion            |
      | artifact with empty assets array (0 items)      | error "ARTIFACT_REQUIRED" with suggestion            |
      | text asset missing content                      | error "ARTIFACT_REQUIRED" with suggestion            |
      | image asset missing url                         | error "ARTIFACT_REQUIRED" with suggestion            |
      | artifact with all four asset types              | the operation succeeds                               |

  @T-UC-024-boundary-records @boundary @records
  Scenario Outline: Delivery records batch boundary validation - <boundary_point>
    Given a content standard exists
    And a records array at the boundary: <boundary_point>
    When the records are submitted for validation
    Then <outcome>

    Examples: Boundary values
      | boundary_point                | outcome                                                    |
      | 0 records (empty array)       | error "RECORDS_REQUIRED" with suggestion                   |
      | 1 record (minimum valid)      | the operation succeeds                                     |
      | 10,000 records (maximum valid) | the operation succeeds                                    |
      | 10,001 records (exceeds limit) | error "RECORDS_LIMIT_EXCEEDED" with suggestion            |
      | records field absent          | error "RECORDS_REQUIRED" with suggestion                   |
      | record without record_id      | error "ARTIFACT_REQUIRED" with suggestion                  |
      | record without artifact       | error "ARTIFACT_REQUIRED" with suggestion                  |

  @T-UC-024-boundary-sampling @boundary @sampling
  Scenario Outline: Sampling parameter boundary validation - <boundary_point>
    Given a media buy exists for the authenticated buyer
    And a sampling configuration at the boundary: <boundary_point>
    When the Buyer Agent requests media buy artifacts
    Then <outcome>

    Examples: Boundary values
      | boundary_point                          | outcome                                                   |
      | sampling omitted (defaults to media buy rate) | the operation succeeds                              |
      | rate = 0 (minimum)                      | the operation succeeds                                    |
      | rate = 1 (maximum)                      | the operation succeeds                                    |
      | rate = -0.001 (below minimum)           | error "SAMPLING_RATE_INVALID" with suggestion             |
      | rate = 1.001 (above maximum)            | error "SAMPLING_RATE_INVALID" with suggestion             |
      | method = random                         | the operation succeeds                                    |
      | method = stratified                     | the operation succeeds                                    |
      | method = recent                         | the operation succeeds                                    |
      | method = failures_only                  | the operation succeeds                                    |
      | method = unknown value                  | error "SAMPLING_METHOD_INVALID" with suggestion           |

  @T-UC-024-boundary-pagination @boundary @pagination
  Scenario Outline: Artifact pagination boundary validation - <boundary_point>
    Given a media buy exists for the authenticated buyer
    And a pagination configuration at the boundary: <boundary_point>
    When the Buyer Agent requests media buy artifacts
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                | outcome                                                    |
      | pagination omitted (default max_results=1000) | the operation succeeds                                     |
      | max_results = 1 (minimum)                     | the operation succeeds                                     |
      | max_results = 0 (below minimum)               | error "PAGINATION_INVALID" with suggestion                 |
      | max_results = 10000 (maximum)                 | the operation succeeds                                     |
      | max_results = 10001 (above maximum)           | error "PAGINATION_INVALID" with suggestion                 |
      | cursor from valid previous response           | the operation succeeds                                     |
      | cursor with unknown/expired value             | error "PAGINATION_CURSOR_INVALID" with suggestion          |

  @T-UC-024-boundary-verdict @boundary @verdict
  Scenario Outline: Verdict model boundary validation - <boundary_point>
    Given a content standard exists
    And a verdict response at the boundary: <boundary_point>
    When the verdict is evaluated
    Then <outcome>

    Examples: Boundary values
      | boundary_point                     | outcome                                                    |
      | verdict = pass                     | the operation succeeds                                     |
      | verdict = fail                     | the operation succeeds                                     |
      | verdict absent                     | error "VERDICT_REQUIRED" with suggestion                   |
      | verdict = unknown string           | error "VERDICT_INVALID" with suggestion                    |
      | confidence = 0 (minimum)           | the operation succeeds                                     |
      | confidence = 1 (maximum)           | the operation succeeds                                     |
      | confidence = -0.001 (below minimum) | error "CONFIDENCE_OUT_OF_RANGE" with suggestion           |
      | confidence = 1.001 (above maximum) | error "CONFIDENCE_OUT_OF_RANGE" with suggestion            |
      | feature status = passed            | the operation succeeds                                     |
      | feature status = failed            | the operation succeeds                                     |
      | feature status = warning           | the operation succeeds                                     |
      | feature status = unevaluated       | the operation succeeds                                     |
      | feature status = unknown value     | error "FEATURE_STATUS_INVALID" with suggestion             |

  @T-UC-024-boundary-local-verdict @boundary @local_verdict
  Scenario Outline: Local verdict boundary validation - <boundary_point>
    Given a media buy exists with delivery artifacts
    And artifact records at the boundary: <boundary_point>
    When the artifacts are retrieved
    Then <outcome>

    Examples: Boundary values
      | boundary_point                | outcome                                                    |
      | local_verdict = pass          | artifact includes valid local_verdict                      |
      | local_verdict = fail          | artifact includes valid local_verdict                      |
      | local_verdict = unevaluated   | artifact includes valid local_verdict                      |
      | local_verdict absent          | artifact valid without local_verdict                       |
      | local_verdict = unknown value | error "LOCAL_VERDICT_INVALID" with suggestion              |

  @T-UC-024-boundary-feature-ids @boundary @feature_ids
  Scenario Outline: Feature filtering boundary validation - <boundary_point>
    Given a content standard exists with defined features
    And a validation request at the boundary: <boundary_point>
    When the validation is executed
    Then <outcome>

    Examples: Boundary values
      | boundary_point                          | outcome                                                   |
      | feature_ids omitted (evaluate all)      | all features evaluated                                    |
      | feature_ids with 1 element (minimum valid) | only that feature evaluated                            |
      | feature_ids with 0 elements (empty array) | error "FEATURE_IDS_EMPTY" with suggestion               |
      | feature_ids with unknown feature        | feature evaluated (may produce unevaluated)               |

  @T-UC-024-boundary-include-passed @boundary @include_passed
  Scenario Outline: Result filtering boundary validation - <boundary_point>
    Given a content standard exists
    And a validation request at the boundary: <boundary_point>
    When the validation is executed with mixed pass/fail records
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                 | outcome                                                |
      | include_passed omitted (default true)          | results contain all records                            |
      | include_passed = true                          | results contain all records                            |
      | include_passed = false                         | results contain only failing records                   |
      | include_passed = false with all records passing | results array is empty but summary counts are correct |
      | include_passed = false with all records failing | results contain all records (all failed)              |

  @T-UC-024-boundary-summary @boundary @summary
  Scenario Outline: Summary counts boundary validation - <boundary_point>
    Given a validation response at the boundary: <boundary_point>
    When the summary is evaluated
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                | outcome                                                    |
      | all records pass (failed_records = 0)         | summary valid with zero failures                           |
      | all records fail (passed_records = 0)         | summary valid with zero passes                             |
      | single record batch (total = 1)               | summary valid with total 1                                 |
      | maximum batch (total = 10000)                 | summary valid with total 10000                             |
      | total_records missing                         | error "SUMMARY_INCOMPLETE" with suggestion                 |
      | passed + failed != total                      | error "SUMMARY_COUNTS_MISMATCH" with suggestion            |
      | summary object absent from success response   | error "SUMMARY_INCOMPLETE" with suggestion                 |

  @T-UC-024-boundary-dialogue @boundary @dialogue
  Scenario Outline: Calibration dialogue boundary validation - <boundary_point>
    Given a content standard exists
    And a dialogue state at the boundary: <boundary_point>
    When the calibration request is processed
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                                  |
      | first turn (no contextId)                            | verdict returned successfully                            |
      | second turn in same conversation (with contextId)    | response within accumulated context                      |
      | second turn with invalid contextId                   | error "CONTEXT_NOT_FOUND" with suggestion                |
      | follow-up with different artifact in same conversation | artifact evaluated in accumulated context              |
      | text-only follow-up question (no new artifact)       | question answered in conversation context                |

