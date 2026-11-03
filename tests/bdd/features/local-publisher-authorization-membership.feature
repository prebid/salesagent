# Hand-authored feature — not compiled from adcp-req
#
# Publisher-partner JSON routes require an active User row for the URL tenant.
# Ungraded by conformance storyboards: admin session cookies are not an AdCP
# tool surface.
#
# BOTH ADMIN TRANSPORTS. admin_integration drives the routes in process.
# e2e_admin drives them on the live stack, so the session cookie, proxy and
# blueprint mounting are graded too.

@pubauth
Feature: Publisher-partner admin routes require active tenant membership

  Background:
    Given the tenant has registered publisher partnerships with domains "pub.example"

  @T-ADMIN-PUBAUTH-anonymous
  Scenario Outline: A caller with no session is refused JSON 401
    Given the caller has no session
    When the operator <action>
    Then the admin route answers JSON 401 "Authentication required"
    And the publisher partners are unchanged

    Examples:
      | action                                                       |
      | lists publisher partners                                     |
      | creates a publisher partner                                  |
      | deletes the partnership with "pub.example"                   |
      | syncs publisher partners                                     |
      | opens the properties of the partnership with "pub.example"   |

  @T-ADMIN-PUBAUTH-outsider
  Scenario Outline: A caller without active membership is refused JSON 403
    Given the caller has no active membership
    When the operator <action>
    Then the admin route answers JSON 403 "Access denied"
    And the publisher partners are unchanged

    Examples:
      | action                                                       |
      | lists publisher partners                                     |
      | creates a publisher partner                                  |
      | deletes the partnership with "pub.example"                   |
      | syncs publisher partners                                     |
      | opens the properties of the partnership with "pub.example"   |

  # Session names THIS tenant. An active bystander User row also exists, so 403
  # is inactivity, not an empty membership table.
  @T-ADMIN-PUBAUTH-inactive
  Scenario Outline: An inactive member of this tenant is refused JSON 403
    Given the caller is an inactive member of this tenant
    When the operator <action>
    Then the admin route answers JSON 403 "Access denied"
    And the publisher partners are unchanged

    Examples:
      | action                                                       |
      | lists publisher partners                                     |
      | creates a publisher partner                                  |
      | deletes the partnership with "pub.example"                   |
      | syncs publisher partners                                     |
      | opens the properties of the partnership with "pub.example"   |

  @T-ADMIN-PUBAUTH-member-list
  Scenario: An active member lists publisher partners
    Given the caller is an active member
    When the operator lists publisher partners
    Then the listed partners include "pub.example"

  @T-ADMIN-PUBAUTH-member-create
  Scenario: An active member creates a publisher partner
    Given the caller is an active member
    When the operator creates a publisher partner
    Then the publisher "created-by-caller.example" is a partner

  @T-ADMIN-PUBAUTH-member-delete
  Scenario: An active member deletes a publisher partner
    Given the caller is an active member
    When the operator deletes the partnership with "pub.example"
    Then the publisher "pub.example" is not a partner
