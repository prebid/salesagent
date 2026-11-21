# Hand-authored: the admin surface has no AdCP spec of its own. What it authors is graded by
# core/product-format-declaration.json through the product scenarios.
#
# The inventory page's "Create Profile" modal builds a FormData and fetches it. These scenarios
# send that exact form: field names, the Accept header the modal sets, the same URL. What they
# cannot reach is the modal's JavaScript running in a browser, so the form is restated here
# rather than produced by it; the page scenario reads that the rendered modal carries the
# canonical editor and no longer the field the handler ignores.

Feature: The inventory page's profile modal authors canonical format declarations

  Background:
    Given an admin user is authenticated for tenant "test-tenant"
    And the seller holds a verified property of publisher "pub-psatest.com"

  @T-ADMIN-INVPROF-001 @main-flow
  Scenario: The modal creates a profile with its format options
    When the modal submits a new profile "modal_created" declaring an image of 300x250
    Then the modal's answer follows through to a page
    And the profile "modal_created" stores an image of 300x250

  @T-ADMIN-INVPROF-002 @error-path
  Scenario: The modal is told when a profile is refused
    When the modal submits a new profile "modal_refused" declaring no format options
    Then the modal is answered 400 with the reason as JSON
    And no profile "modal_refused" is stored

  @T-ADMIN-INVPROF-003 @main-flow
  Scenario: The modal's edit keeps a format change
    Given a stored profile "modal_edited" declaring an image of 300x250
    When the modal edits the profile "modal_edited" to declare an image of 728x90
    Then the modal's answer follows through to a page
    And the profile "modal_edited" stores an image of 728x90

  @T-ADMIN-INVPROF-004 @error-path
  Scenario: A browser form is still redirected with its refusal
    When a browser form submits a new profile "form_refused" declaring no format options
    Then the form is redirected
    And no profile "form_refused" is stored

  @T-ADMIN-INVPROF-005 @main-flow
  Scenario: The rendered modal carries the canonical editor
    When the inventory page is opened
    Then the page's modal has a format_options field and no formats field

  # A format option's publisher_domain names the publisher whose adagents.json formats[]
  # declares it (AdCP 3.2.7 core/product-format-declaration.json publisher_domain), and this
  # agent publishes no formats[]: a seller authors no publisher's options (core/placement.json
  # publisher_domain: no "authority to mint IDs in that publisher's catalog namespace"). So the
  # profile modal and the product form refuse a declaration naming the seller's own host as
  # its publisher, and store nothing under it.

  @T-ADMIN-INVPROF-006 @error-path
  Scenario: The modal refuses a format option naming the seller's own host as publisher
    When the modal submits a new profile "host_option" declaring format option "homepage_mrec" under the seller's own host
    Then the modal is answered 400 with the reason as JSON
    And the refusal names the seller's own host as the format option's publisher
    And no profile "host_option" is stored

  @T-ADMIN-INVPROF-007 @error-path
  Scenario: The modal's edit refuses a format option naming the seller's own host as publisher
    Given a stored profile "host_edit" declaring an image of 300x250
    When the modal edits the profile "host_edit" to declare format option "homepage_mrec" under the seller's own host
    Then the refusal names the seller's own host as the format option's publisher
    And the profile "host_edit" stores an image of 300x250

  @T-ADMIN-INVPROF-008 @error-path
  Scenario: The product form refuses a format option naming the seller's own host as publisher
    When the operator submits the product form for "host_option" declaring format option "homepage_mrec" under the seller's own host
    Then the refusal names the seller's own host as the format option's publisher
    And no product "host_option" is stored
