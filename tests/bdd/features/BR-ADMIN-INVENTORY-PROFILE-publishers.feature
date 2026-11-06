# Hand-authored feature — not compiled from adcp-req
# Admin UI: the publishers an inventory profile names, and the products buyers are not offered (#1845)

Feature: BR-ADMIN-INVENTORY-PROFILE-publishers Which publishers the operator's selections name
  As a Tenant Admin
  I want an inventory profile to name each publisher whose properties I select
  So that a product sold from it names publishers a buyer can verify

  # A seller represents many publishers, each authorizing the agent in its own
  # adagents.json, and a buyer verifies a product at every
  # publisher_properties[].publisher_domain (AdCP 3.1.1
  # governance/property/authorized-properties.mdx). So the profile form stores one
  # selector per publisher of the VERIFIED authorized properties the selection matches:
  # core/product.json admits only the singular publisher_domain form on a product, and
  # core/publisher-property-selector.json makes property ids publisher-scoped. The
  # seller's own host is no publisher and the form no longer offers it as a default.
  #
  # all_inventory is the seller's tag, which a publisher's adagents.json does not carry,
  # so selecting it selects each publisher whole.
  #
  # The product add and edit forms store selectors the same way and refuse a selection
  # the same way, through the one validator the profile form uses: a tag or property only
  # a pending property backs is refused, because get_products would not sell it. The
  # edit form preselects what the product sells, the verified publishers only.
  #
  # The products page marks a product get_products leaves out because it names no
  # verified publisher, so the operator sees the gap rather than only a server log line.
  #
  # Transports: the admin form surface, in process (Flask test client) and over e2e
  # (requests against the live stack), through AdminInventoryProfileEnv.

  Background:
    Given a tenant is configured for product discovery
    And the seller is authorized for property "news_home" of publisher "news.example" tagged "premium"
    And the seller is authorized for property "sports_home" of publisher "sports.example" tagged "premium"
    And the seller is authorized for property "weather_home" of publisher "weather.example" tagged "standard"

  @T-ADMIN-INVPROFILE-001 @admin_inventory_profile @requires_db
  Scenario: selecting a tag names each publisher whose properties carry it
    When the operator creates inventory profile "premium_sites" selecting tags "premium"
    Then inventory profile "premium_sites" stores publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-ADMIN-INVPROFILE-002 @admin_inventory_profile @requires_db
  Scenario: selecting the seller's all_inventory tag names every publisher whole
    When the operator creates inventory profile "everything" selecting tags "all_inventory"
    Then inventory profile "everything" stores publisher_properties [{"publisher_domain": "news.example", "selection_type": "all"}, {"publisher_domain": "sports.example", "selection_type": "all"}, {"publisher_domain": "weather.example", "selection_type": "all"}]

  @T-ADMIN-INVPROFILE-003 @admin_inventory_profile @requires_db
  Scenario: selected properties are grouped under their publishers
    When the operator creates inventory profile "home_pages" selecting properties "news_home, weather_home"
    Then inventory profile "home_pages" stores publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather.example", "property_ids": ["weather_home"], "selection_type": "by_id"}]

  @T-ADMIN-INVPROFILE-004 @admin_inventory_profile @requires_db
  Scenario: editing a profile regroups its properties by publisher
    Given an inventory profile "edited" exists
    When the operator edits inventory profile "edited" to select properties "sports_home, news_home"
    Then inventory profile "edited" stores publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "sports.example", "property_ids": ["sports_home"], "selection_type": "by_id"}]

  @T-ADMIN-INVPROFILE-005 @admin_inventory_profile @requires_db
  Scenario: a tag no verified property carries is refused
    When the operator creates inventory profile "orphan" selecting tags "podcast"
    Then the page contains "No verified authorized property carries the tags: podcast"
    And no inventory profile "orphan" is stored

  @T-ADMIN-INVPROFILE-006 @admin_inventory_profile @requires_db
  Scenario: a property awaiting verification is not selectable
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    When the operator creates inventory profile "podcasts" selecting properties "podcast_home"
    Then the page contains "Not verified authorized properties: podcast_home"
    And no inventory profile "podcasts" is stored

  @T-ADMIN-INVPROFILE-007 @admin_inventory_profile @requires_db
  Scenario: the profile forms do not offer the seller's own host as a publisher
    Given an inventory profile "existing" exists
    When the operator opens the add inventory profile form
    Then the page does not name the seller's own host
    When the operator opens the edit form of inventory profile "existing"
    Then the page does not name the seller's own host

  @T-ADMIN-INVPROFILE-008 @admin_inventory_profile @requires_db
  Scenario: the products page marks a product buyers are not offered
    Given the seller offers product "premium" selecting property tags "premium"
    And the seller offers product "orphan" selecting property tags "podcast"
    When the operator opens the products page
    Then the products page marks exactly the products "orphan" as not offered to buyers

  @T-ADMIN-INVPROFILE-009 @admin_inventory_profile @requires_db
  Scenario: the product form stores a tag selection under its publishers
    When the operator creates product "premium_sites" selecting tags "news.example:premium, sports.example:premium"
    Then product "premium_sites" stores publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-ADMIN-INVPROFILE-010 @admin_inventory_profile @requires_db
  Scenario: the product form groups selected properties under their publishers
    When the operator creates product "home_pages" selecting properties "news_home, weather_home"
    Then product "home_pages" stores publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather.example", "property_ids": ["weather_home"], "selection_type": "by_id"}]

  @T-ADMIN-INVPROFILE-011 @admin_inventory_profile @requires_db
  Scenario: the product form refuses a tag only a property awaiting verification carries
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    When the operator creates product "podcasts" selecting tags "podcast.example:podcast"
    Then the page contains "No verified authorized property carries the tags: podcast"
    And no product "podcasts" is stored

  @T-ADMIN-INVPROFILE-012 @admin_inventory_profile @requires_db
  Scenario: the product form refuses a property awaiting verification
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    When the operator creates product "podcasts" selecting properties "news_home, podcast_home"
    Then the page contains "Not verified authorized properties: podcast_home"
    And no product "podcasts" is stored

  @T-ADMIN-INVPROFILE-013 @admin_inventory_profile @requires_db
  Scenario: the product edit form refuses a tag only a property awaiting verification carries
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "news" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]
    When the operator edits product "news" to select tags "podcast.example:podcast"
    Then the page contains "No verified authorized property carries the tags: podcast"
    And product "news" stores publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-ADMIN-INVPROFILE-014 @admin_inventory_profile @requires_db
  Scenario: the product edit form refuses a property awaiting verification
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "news" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]
    When the operator edits product "news" to select properties "podcast_home"
    Then the page contains "Not verified authorized properties: podcast_home"
    And product "news" stores publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-ADMIN-INVPROFILE-015 @admin_inventory_profile @requires_db
  Scenario: the product edit form preselects only the properties the product sells
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "podcast.example", "property_ids": ["podcast_home"], "selection_type": "by_id"}]
    When the operator opens the edit form of product "mixed"
    Then the form preselects exactly the properties "news_home"
