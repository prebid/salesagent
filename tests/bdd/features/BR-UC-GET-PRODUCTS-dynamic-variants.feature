# Local regression for #2291. AdCP 3.1.1 core/product.json requires nonempty
# pricing_options. Dynamic generation has no graded storyboard binding.
Feature: Signal driven variants reach buyers with their stored pricing

  @T-UC-GET-PRODUCTS-dynamic-new @dynamic-catalog @requires_db
  Scenario: A new variant appears beside static inventory and its template
    Given a signal driven catalogue with a new variant
    When the buyer discovers the signal driven catalogue
    Then the catalogue includes static inventory and one priced variant
    And the variant name is "Audience inventory - Premium audience"

  @T-UC-GET-PRODUCTS-dynamic-reuse @dynamic-catalog @requires_db
  Scenario: Repeated discovery reuses one variant and its pricing rows
    Given a signal driven catalogue with a new variant
    When the buyer discovers the signal driven catalogue twice
    Then the catalogue includes static inventory and one priced variant
    And the variant name is "Audience inventory - Premium audience"
    And the stored variant has exactly its two template pricing options

  @T-UC-GET-PRODUCTS-dynamic-legacy-access @dynamic-catalog @requires_db
  Scenario: Pricing repair cannot expose a legacy variant of a restricted template
    Given a signal driven catalogue with a legacy variant restricted to another buyer
    When the buyer discovers the signal driven catalogue twice
    Then the catalogue contains only static inventory

  @T-UC-GET-PRODUCTS-dynamic-updated-access @dynamic-catalog @requires_db
  Scenario: A saved variant respects a later access restriction on its template
    Given a signal driven catalogue with a new variant
    When the buyer discovers the signal driven catalogue
    And the seller restricts the template to another buyer
    And the buyer discovers the signal driven catalogue
    Then the catalogue contains only static inventory

  @T-UC-GET-PRODUCTS-dynamic-legacy @dynamic-catalog @requires_db
  Scenario: An existing variant without pricing is completed on reuse
    Given a signal driven catalogue with a legacy unpriced variant
    When the buyer discovers the signal driven catalogue twice
    Then the catalogue includes static inventory and one priced variant
    And the variant name is "Saved audience inventory"
    And the stored variant has exactly its two template pricing options

  @T-UC-GET-PRODUCTS-dynamic-access @dynamic-catalog @requires_db
  Scenario: A template restricted to another buyer cannot expose its variant
    Given a signal driven catalogue restricted to another buyer
    When the buyer discovers the signal driven catalogue
    Then the catalogue contains only static inventory

  @T-UC-GET-PRODUCTS-dynamic-priced @dynamic-catalog @requires_db
  Scenario: Reuse preserves an existing negotiated price instead of replacing it
    Given a signal driven catalogue with an existing negotiated variant price
    When the buyer discovers the signal driven catalogue twice
    Then the variant retains only its negotiated buyer-contract price of 12.50
    And the variant name is "Saved audience inventory"

  @T-UC-GET-PRODUCTS-dynamic-invalid-signal @dynamic-catalog @requires_db
  Scenario: A signal that exceeds the stored name limit does not discard valid inventory
    Given a signal driven catalogue with an invalid signal before a valid signal
    When the buyer discovers the signal driven catalogue
    Then the catalogue includes static inventory and one priced variant
    And the stored variant has exactly its two template pricing options
