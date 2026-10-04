# Manual feature: which publisher a product's publisher_properties name (#1845).

Feature: A product names the publishers whose inventory it sells
  As a Buyer Agent
  I want each product's publisher_properties to name the publishers it sells
  So that I can verify the seller against each publisher's adagents.json

  # A seller (one sales agent, one host) represents many publishers. Each publisher
  # authorizes the agent in its OWN /.well-known/adagents.json, and a buyer verifies a
  # product by fetching that file at every publisher_properties[].publisher_domain and
  # failing closed when it finds no authorization (AdCP 3.1.1
  # governance/property/authorized-properties.mdx, "Authorization Validation Workflow"
  # and "Missing adagents.json: Treat as unauthorized").
  #
  # So publisher_domain is the PUBLISHER's domain. A product that selects by tag, by
  # property id, or not at all resolves against the seller's authorized properties,
  # one entry per publisher: core/product.json admits only the singular publisher_domain
  # form on a product, and core/publisher-property-selector.json makes property ids
  # publisher-scoped. The seller's own agent host is never a publisher here.
  #
  # Only VERIFIED properties count: a pending property is one whose publisher has not
  # been seen to authorize this agent, and the capabilities portfolio names verified
  # publishers only (AuthorizedPropertyRepository._verified), so a product naming a
  # pending publisher would send the buyer to a file that refuses it. A selector the
  # product or its inventory profile stores explicitly is held to the same rule.
  #
  # core/product.json requires publisher_properties with minItems 1, so a product no
  # verified property backs has nothing it may truthfully claim and is not offered.
  #
  # all_inventory is the SELLER's tag. A buyer resolves a by_tag selector against the
  # publisher's own adagents.json ("Selects properties from a publisher's adagents.json",
  # core/publisher-property-selector.json), where that tag names nothing, so selecting it
  # selects each publisher whole.
  #
  # The by-id grouping is graded through an inventory profile the operator saves with
  # the admin form (AdminInventoryProfileEnv): a product's own property_ids column is
  # written by no form.
  #
  # Ungraded by the 3.1.1 storyboards: no compliance step compares publisher_domain
  # against the seller's authorized properties.

  Background:
    Given a tenant is configured for product discovery
    And the seller is authorized for property "news_home" of publisher "news.example" tagged "premium"
    And the seller is authorized for property "sports_home" of publisher "sports.example" tagged "premium"
    And the seller is authorized for property "weather_home" of publisher "weather.example" tagged "standard"


  @T-UC-GET-PRODUCTS-publisher-by-tag @publisher_domain_resolution @requires_db
  Scenario: a product selecting by tag names each publisher that carries the tag
    Given the seller offers product "premium" selecting property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory @publisher_domain_resolution @requires_db
  Scenario: the seller's all_inventory tag names every authorized publisher
    Given the seller offers product "run_of_network" selecting property tags "all_inventory"
    When the buyer requests products
    Then product "run_of_network" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "sports.example", "selection_type": "all"}, {"publisher_domain": "weather.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-default-all @publisher_domain_resolution @requires_db
  Scenario: a product selecting nothing offers every authorized publisher whole
    Given the seller offers product "everything" selecting no properties
    When the buyer requests products
    Then product "everything" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "sports.example", "selection_type": "all"}, {"publisher_domain": "weather.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-unbacked @publisher_domain_resolution @requires_db
  Scenario: a product no authorized property backs is not offered
    Given the seller offers product "premium" selecting property tags "premium"
    And the seller offers product "orphan" selecting property tags "podcast"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-pending-not-named @publisher_domain_resolution @requires_db
  Scenario: a publisher whose property awaits verification is not named
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "premium" awaits verification
    And the seller offers product "premium" selecting property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-pending-only @publisher_domain_resolution @requires_db
  Scenario: a product only an unverified property backs is not offered
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "premium" selecting property tags "premium"
    And the seller offers product "podcast" selecting property tags "podcast"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-explicit-unauthorized @publisher_domain_resolution @requires_db
  Scenario: a stored selector naming a publisher the seller holds no verified property of is dropped
    Given the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "stranger.example", "selection_type": "all"}]
    And the seller offers product "stranger" naming publisher_properties [{"publisher_domain": "stranger.example", "selection_type": "all"}]
    When the buyer requests products
    Then the buyer receives exactly the products "mixed"
    And product "mixed" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-profile-agent-host @publisher_domain_resolution @requires_db
  Scenario: an inventory profile the old form saved with the seller's own host stops naming it
    Given an inventory profile the old form saved naming the seller's own host
    And a product linked to that inventory profile with pricing
    And the seller offers product "premium" selecting property tags "premium"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-profile-by-id @publisher_domain_resolution @requires_db
  Scenario: properties the operator selects on an inventory profile are named one selector per publisher
    Given the operator saves inventory profile "home_pages" selecting properties "news_home, weather_home"
    And product "home_package" linked to that inventory profile with pricing
    When the buyer requests products
    Then product "home_package" announces publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather.example", "property_ids": ["weather_home"], "selection_type": "by_id"}]
