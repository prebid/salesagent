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
  # all_inventory is the seller's default tag, so selecting it selects each publisher whole
  # (see BR-UC-GET-PRODUCTS-publisher-domain.feature for what that claims).
  #
  # The product add and edit forms store selectors the same way and refuse a selection
  # the same way, through the one validator the profile form uses: a tag or property only
  # a pending property backs is not offered, and is refused if submitted anyway, because
  # get_products would not sell it. The edit form preselects what the product sells, the
  # verified publishers only.
  #
  # Saving an edit form with its selection untouched changes nothing about that selection.
  # A product page's pre-checked boxes are compared with what it posted, because the form
  # cannot express a legacy column, a pending publisher's selector or a mix of modes. A
  # profile page pre-fills tags or property ids only when they regenerate the stored
  # selectors, and shows them as JSON otherwise.
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
  Scenario: a property awaiting verification is refused on submit
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

  @T-ADMIN-INVPROFILE-016 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form keeps a legacy tag selection
    Given the seller offers product "premium" selecting property tags "premium"
    When the operator renames product "premium" through its edit form, changing nothing else
    Then product "premium" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-017 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form keeps a pending publisher's stored selector
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "premium" awaits verification
    And the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "podcast.example", "property_tags": ["premium"], "selection_type": "by_tag"}]
    When the operator renames product "mixed" through its edit form, changing nothing else
    Then product "mixed" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-018 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form keeps a legacy property id selection with a pending id
    Given the seller's property "news_extra" of publisher "news.example" tagged "premium" awaits verification
    And the seller offers product "ids" selecting property ids "news_home, news_extra"
    When the operator renames product "ids" through its edit form, changing nothing else
    Then product "ids" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-019 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form keeps selectors of both modes
    Given the seller offers product "both" naming publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "weather.example", "property_ids": ["weather_home"], "selection_type": "by_id"}]
    When the operator renames product "both" through its edit form, changing nothing else
    Then product "both" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-020 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form is not refused over a stored pending property
    Given the seller's property "news_extra" of publisher "news.example" tagged "premium" awaits verification
    And the seller offers product "stored_ids" naming publisher_properties [{"publisher_domain": "news.example", "property_ids": ["news_home", "news_extra"], "selection_type": "by_id"}]
    When the operator renames product "stored_ids" through its edit form, changing nothing else
    Then product "stored_ids" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-021 @admin_inventory_profile @requires_db
  Scenario: saving a product edit form keeps a selection of all_inventory
    Given the seller offers product "whole" selecting property tags "all_inventory"
    When the operator renames product "whole" through its edit form, changing nothing else
    Then product "whole" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-022 @admin_inventory_profile @requires_db
  Scenario: saving a profile edit form keeps the properties of every publisher
    Given the operator saves inventory profile "home_pages" selecting properties "news_home, weather_home"
    When the operator renames inventory profile "home_pages" through its edit form, changing nothing else
    Then inventory profile "home_pages" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-023 @admin_inventory_profile @requires_db
  Scenario: saving a profile edit form keeps the tags of every publisher
    When the operator creates inventory profile "tagged" selecting tags "premium, standard"
    And the operator renames inventory profile "tagged" through its edit form, changing nothing else
    Then inventory profile "tagged" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-024 @admin_inventory_profile @requires_db
  Scenario: saving a profile edit form keeps selectors its tags and property ids cannot regenerate
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "premium" awaits verification
    And an inventory profile "stored" storing publisher_properties [{"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "podcast.example", "property_tags": ["premium"], "selection_type": "by_tag"}]
    When the operator renames inventory profile "stored" through its edit form, changing nothing else
    Then inventory profile "stored" is renamed and its stored selection is unchanged

  @T-ADMIN-INVPROFILE-026 @admin_inventory_profile @requires_db
  Scenario: the product form's full mode refuses a property awaiting verification
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    When the operator creates product "legacy" in full mode selecting properties "podcast_home"
    Then the page contains "Not verified authorized properties: podcast_home"
    And no product "legacy" is stored

  @T-ADMIN-INVPROFILE-027 @admin_inventory_profile @requires_db
  Scenario: the profile form's JSON mode refuses a publisher awaiting verification
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    When the operator creates inventory profile "podcasts" in JSON mode storing publisher_properties [{"publisher_domain": "podcast.example", "selection_type": "all"}]
    Then the page contains "Not verified authorized publisher selections: podcast.example"
    And no inventory profile "podcasts" is stored

  @T-ADMIN-INVPROFILE-025 @admin_inventory_profile @requires_db
  Scenario: the forms offer only verified properties
    Given the seller's property "podcast_home" of publisher "podcast.example" tagged "podcast" awaits verification
    And the seller offers product "premium" selecting property tags "premium"
    And an inventory profile "existing" exists
    When the operator opens the add product form
    Then the form offers exactly the properties "news_home, sports_home, weather_home"
    And the form offers no choice of publisher "podcast.example"
    When the operator opens the edit form of product "premium"
    Then the form offers exactly the properties "news_home, sports_home, weather_home"
    And the form offers no choice of publisher "podcast.example"
    When the operator opens the add inventory profile form
    Then the form offers exactly the properties "news_home, sports_home, weather_home"
    When the operator opens the edit form of inventory profile "existing"
    Then the form offers exactly the properties "news_home, sports_home, weather_home"
