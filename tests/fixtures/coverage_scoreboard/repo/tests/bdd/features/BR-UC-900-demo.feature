@schema-v3.1
Feature: BR-UC-900 Demo

  Background:
    Given a seller

  @T-UC-900-green @main-flow
  Scenario: Passes everywhere
    Then it passes

  @T-UC-900-split
  Scenario: Passes in-process, xfails in-network
    Then it passes somewhere

  @T-UC-900-gap
  Scenario Outline: Known gap on every transport
    Then <thing> is refused

    @extra
    Examples:
      | thing |
      | a     |
      | b     |

  @T-UC-900-red
  Scenario: Fails
    Then it fails

  @T-UC-900-unrun
  Scenario: Collected but never run
    Then nothing

  @T-UC-900-unbound
  Scenario: No module binds it
    Then nothing

  @wip
  Scenario: Has no identity tag
    Then nothing
