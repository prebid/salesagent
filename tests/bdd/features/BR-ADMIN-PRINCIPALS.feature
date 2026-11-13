# Hand-authored feature, not compiled from adcp-req.
# Transports: admin_integration (Flask) and e2e_admin (live HTTP).
@admin-principals
Feature: BR-ADMIN-PRINCIPALS Advertiser identity and credentials
  Advertiser IDs are stored exactly as submitted within the addressed tenant.

  Background:
    Given the advertiser form is open

  @T-ADMIN-PRIN-001
  Scenario Outline: Accepted IDs issue the credential shown to the operator
    When the advertiser form submits ID <id>
    Then the advertiser is stored with ID <id> and the displayed token
    And the advertiser create audit records success
    Examples:
      | id                                                |
      | "Buyer_123-brand"                                  |
      | "Anonymous"                                       |
      | "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"    |

  @T-ADMIN-PRIN-002
  Scenario Outline: Refused input creates neither an advertiser nor a credential
    When the advertiser form submits ID <id>
    Then the advertiser create is refused with "<message>"
    Examples:
      | id                                                 | message                                                                  |
      | ""                                                 | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "   "                                              | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | " buyer "                                          | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "two words"                                        | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "a/b"                                              | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "☃"                                                | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"    | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | omitted                                            | Advertiser ID must contain 1 to 45 letters, numbers, underscores, or hyphens |
      | "anonymous"                                        | Advertiser ID 'anonymous' is reserved                                     |
      | "unknown"                                          | Advertiser ID 'unknown' is reserved                                       |
      | "admin"                                            | Advertiser ID 'admin' is reserved                                         |

  @T-ADMIN-PRIN-003
  Scenario Outline: Invalid mapping and missing name are audited without validation internals
    When the advertiser form submits field "<field>" as "<value>"
    Then the advertiser create is refused with "<message>"
    Examples:
      | field             | value | message                              |
      | name              |       | Principal name is required           |
      | enable_mock       |       | Select at least one platform mapping |
      | gam_advertiser_id | abc   | GAM Advertiser ID must be numeric    |

  @T-ADMIN-PRIN-004
  Scenario: A duplicate ID cannot replace an advertiser or its token
    Given advertiser "buyer" exists in this tenant
    When the advertiser form submits ID "buyer"
    Then the advertiser create is refused with "Advertiser ID is already in use"
    And the original advertiser credential is unchanged

  @T-ADMIN-PRIN-005
  Scenario: The same ID is available in another tenant
    Given advertiser "buyer" exists in another tenant
    When the advertiser form submits ID "buyer"
    Then the advertiser is stored with ID "buyer" and the displayed token
    And the advertiser create audit records success

  @T-ADMIN-PRIN-006
  Scenario Outline: Editing uses the same mapping refusals and leaves the advertiser unchanged
    Given advertiser "buyer" exists in this tenant
    When the advertiser edit submits field "<field>" as "<value>"
    Then the advertiser edit is refused with "<message>"
    And the original advertiser credential is unchanged
    Examples:
      | field             | value | message                              |
      | enable_mock       |       | Select at least one platform mapping |
      | gam_advertiser_id | abc   | GAM Advertiser ID must be numeric    |

  @T-ADMIN-PRIN-007
  Scenario: An anonymous form post cannot create an advertiser
    Given the advertiser operator signs out
    When the advertiser form submits ID "buyer"
    Then the advertiser post requires login without an audit success

  @T-ADMIN-PRIN-008
  Scenario: The browser receives the server ID rule
    Then the advertiser form exposes the shared ID rule
