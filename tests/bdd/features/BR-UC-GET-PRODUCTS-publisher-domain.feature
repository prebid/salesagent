# Manual feature: which publisher a product's publisher_properties name (#1845).

Feature: A product names the publishers whose inventory it sells
  As a Buyer Agent
  I want each product's publisher_properties to name the publishers it sells
  So that I can verify the seller against each publisher's adagents.json

  # A seller (one sales agent, one host) represents many publishers. Each publisher
  # authorizes the agent in its OWN /.well-known/adagents.json, and a buyer verifies a
  # product by fetching that file at every publisher_properties[].publisher_domain and
  # failing closed when it finds no authorization (AdCP 3.2.7
  # governance/property/authorized-properties.mdx, "Authorization Validation Workflow"
  # and "Missing adagents.json: Treat as unauthorized").
  #
  # So publisher_domain is the PUBLISHER's domain, one entry per publisher:
  # core/product.json admits only the singular publisher_domain form on a product, and
  # core/publisher-property-selector.json makes property ids publisher-scoped. The seller's
  # own agent host is never a publisher here.
  #
  # A product stores its publishers as explicit selectors. Alembic revision 3c38b1ee241f
  # converted the legacy property_tags / property_ids columns into them and dropped the
  # columns: tags become by_tag per publisher carrying them, ids become by_id per
  # publisher, an empty selection becomes "all" per publisher. "Migrated from legacy ..."
  # products are built with that revision's own conversion.
  #
  # Only VERIFIED properties count: a pending property is one whose publisher has not
  # been seen to authorize this agent, and the capabilities portfolio names verified
  # publishers only (AuthorizedPropertyRepository._verified), so a product naming a
  # pending publisher would send the buyer to a file that refuses it. A selector the
  # product or its inventory profile stores explicitly is held to the same rule per
  # property, as verification is per property: a by_id selector keeps the ids of its
  # publisher's verified properties, a by_tag selector keeps the tags a verified property
  # of its publisher carries, and an "all" selector stays while its publisher has a
  # verified property. A selector left with nothing is dropped.
  #
  # core/product.json requires publisher_properties with minItems 1, so a product no
  # verified property backs has nothing it may truthfully claim and is not offered.
  #
  # all_inventory is the seller's default tag, on every property row the admin forms store,
  # so a row carrying it says nothing about the publisher's own adagents.json: a publisher
  # following docs/deployment/vm.md declares it there, and another need not. Selecting it is
  # announced as selection_type "all" on every path, stored selectors included. "all" claims
  # "all properties from each addressed publisher" (core/publisher-property-selector.json),
  # which under property_tags or property_ids authorization includes properties outside the
  # authorized scope; AdCP 3.2.7 does not say how a buyer treats that.
  #
  # A verified publisher is named by the host its row names (lower-case, no scheme, path or
  # trailing dot), as the capabilities portfolio names it, because core/product.json's
  # publisher_domain pattern admits nothing else. A row no such spelling can satisfy (a port)
  # costs that publisher: the other publishers stay, and a product naming only that one is
  # not offered.
  #
  # The by-id grouping of a form's choice is graded through an inventory profile the
  # operator saves with the admin form (AdminInventoryProfileEnv).
  #
  # Ungraded by the 3.2.7 storyboards: no compliance step compares publisher_domain
  # against the seller's authorized properties.

  Background:
    Given a tenant is configured for product discovery
    And the seller is authorized for property "news_home" of publisher "news.example" tagged "premium"
    And the seller is authorized for property "sports_home" of publisher "sports.example" tagged "premium"
    And the seller is authorized for property "weather_home" of publisher "weather.example" tagged "standard"


  @T-UC-GET-PRODUCTS-publisher-by-tag @publisher_domain_resolution @requires_db
  Scenario: a product selecting by tag names each publisher that carries the tag
    Given the seller offers product "premium" migrated from legacy property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory @publisher_domain_resolution @requires_db
  Scenario: the seller's all_inventory tag names every authorized publisher
    Given the seller offers product "run_of_network" migrated from legacy property tags "all_inventory"
    When the buyer requests products
    Then product "run_of_network" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "sports.example", "selection_type": "all"}, {"publisher_domain": "weather.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-default-all @publisher_domain_resolution @requires_db
  Scenario: a product selecting nothing offers every authorized publisher whole
    Given the seller offers product "everything" migrated from a legacy selection of no properties
    When the buyer requests products
    Then product "everything" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "sports.example", "selection_type": "all"}, {"publisher_domain": "weather.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-unbacked @publisher_domain_resolution @requires_db
  Scenario: a product no authorized property backs is not offered
    Given the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "orphan" naming publisher_properties [{"publisher_domain": "podcast.example", "property_tags": ["podcast"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-pending-not-named @publisher_domain_resolution @requires_db
  Scenario: a publisher whose property awaits verification is not named
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "premium" awaits verification
    And the seller offers product "premium" migrated from legacy property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-pending-only @publisher_domain_resolution @requires_db
  Scenario: a product only an unverified property backs is not offered
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "podcast" migrated from legacy property tags "podcast"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-explicit-unauthorized @publisher_domain_resolution @requires_db
  Scenario: a stored selector naming a publisher the seller holds no verified property of is dropped
    Given the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "stranger.example", "selection_type": "all"}]
    And the seller offers product "stranger" naming publisher_properties [{"publisher_domain": "stranger.example", "selection_type": "all"}]
    When the buyer requests products
    Then the buyer receives exactly the products "mixed"
    And product "mixed" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-stored-ids-per-property @publisher_domain_resolution @requires_db
  Scenario: a stored by_id selector names only its publisher's verified properties
    Given the seller's property "news_live" of publisher "news.example" tagged "live" awaits verification
    And the seller offers product "ids" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home", "news_live"], "selection_type": "by_id"}]
    When the buyer requests products
    Then product "ids" announces publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-UC-GET-PRODUCTS-publisher-stored-tag-per-property @publisher_domain_resolution @requires_db
  Scenario: a stored by_tag selector whose tag only a pending property carries is not offered
    Given the seller's property "news_live" of publisher "news.example" tagged "live" awaits verification
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "live" naming publisher_properties [{"publisher_domain": "news.example", "property_tags": ["live"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-profile-agent-host @publisher_domain_resolution @requires_db
  Scenario: an inventory profile the old form saved with the seller's own host stops naming it
    Given an inventory profile the old form saved naming the seller's own host
    And a product linked to that inventory profile with pricing
    And the seller offers product "premium" migrated from legacy property tags "premium"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-profile-by-id @publisher_domain_resolution @requires_db
  Scenario: properties the operator selects on an inventory profile are named one selector per publisher
    Given the operator saves inventory profile "home_pages" selecting properties "news_home, weather_home"
    And product "home_package" linked to that inventory profile with pricing
    When the buyer requests products
    Then product "home_package" announces publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather.example", "property_ids": ["weather_home"], "selection_type": "by_id"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory-stored @publisher_domain_resolution @requires_db
  Scenario: a stored by_tag selector of the seller's all_inventory tag names its publisher whole
    Given the seller offers product "stored_whole" naming publisher_properties [{"publisher_domain": "news.example", "property_tags": ["all_inventory"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then product "stored_whole" announces publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory-by-id @publisher_domain_resolution @requires_db
  Scenario: a stored by_id selector carrying a stray all_inventory tag stays by_id
    Given the seller offers product "stray_tag" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "property_tags": ["all_inventory"], "selection_type": "by_id"}]
    When the buyer requests products
    Then product "stray_tag" announces publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-UC-GET-PRODUCTS-publisher-canonical-domain @publisher_domain_resolution @requires_db
  Scenario: a verified publisher stored in another spelling is named by its host
    Given the seller is authorized for property "mixed_case_home" of publisher "Mixed.Example.com" tagged "cased"
    And the seller is authorized for property "dotted_home" of publisher "https://Dotted.Example.com./" tagged "cased"
    And the seller offers product "cased" migrated from legacy property tags "cased"
    And the seller offers product "stored_cased" naming publisher_properties [{"publisher_domain": "Mixed.Example.com", "selection_type": "all"}]
    When the buyer requests products
    Then product "cased" announces publisher_properties [{"publisher_domain": "dotted.example.com", "property_tags": ["cased"], "selection_type": "by_tag"}, {"publisher_domain": "mixed.example.com", "property_tags": ["cased"], "selection_type": "by_tag"}]
    And product "stored_cased" announces publisher_properties [{"publisher_domain": "mixed.example.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-rejected-domain @publisher_domain_resolution @requires_db
  Scenario: a verified publisher no canonical spelling can name costs that publisher, not the catalog
    Given the seller is authorized for property "ported_home" of publisher "news.example:8443" tagged "premium"
    And the seller is authorized for property "only_ported_home" of publisher "other.example:8443" tagged "ported"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "ported" migrated from legacy property tags "ported"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"
    And product "premium" announces publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  # A format option's publisher_domain names the publisher whose adagents.json formats[]
  # declares it (AdCP 3.2.7 core/product-format-declaration.json publisher_domain;
  # core/format-option-ref.json: the reference "resolves against the named publisher's
  # adagents.json top-level formats[] catalog"). This agent publishes no formats[], so an
  # option under its own host resolves nowhere, and core/product.json format_options says an
  # unresolved format-option reference "MUST fail closed": the product is not offered.
  # Alembic revision f9c29da38437 made every stored option under the agent host product-local;
  # this grades a row that names it anyway.
  @T-UC-GET-PRODUCTS-format-option-agent-host @publisher_domain_resolution @requires_db
  Scenario: a product whose format option names the seller's own host as publisher is not offered
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}]
    And the tenant also holds product "host_option" declaring format option "homepage_mrec" under the seller's own host
    When the buyer requests products
    Then no product announces the seller's own host as a format option's publisher_domain
    And the buyer receives exactly the products "news"
