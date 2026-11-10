# Manual feature: which publishers a product names in publisher_properties, and which format
# options it is served -- both decided by each publisher's own adagents.json (#1845, #2330).

Feature: A product names the publishers whose inventory it sells
  As a Buyer Agent
  I want each product's publisher_properties to name the publishers that authorize the seller
  So that I can verify the seller against each publisher's adagents.json

  # A seller (one sales agent, one host) represents many publishers. Each publisher
  # authorizes the agent in its OWN /.well-known/adagents.json, and a buyer checks each
  # product publisher_properties selector against that publisher's authorized_agents[] by
  # the seller's URL, treating a missing file as "unauthorized (fail closed)" (AdCP
  # governance/property/authorized-properties.mdx :209-210, :256). The seller's gate
  # agrees with that check by reading the same file: the seller fetches each publisher's
  # adagents.json, stores the last valid one, and derives from it the properties the file
  # authorizes THIS agent for (src/services/publisher_ingest.py). No property is
  # authorized any other way, so every Given here writes what a publisher's file says and
  # the seller ingests it.
  #
  # publisher_domain is the PUBLISHER's domain, one entry per publisher: core/product.json
  # admits only the singular publisher_domain form on a product, and
  # core/publisher-property-selector.json makes property ids publisher-scoped. The seller's
  # own agent host is never a publisher here. A product's selector is narrowed per
  # property to what the files authorize: by_id keeps the ids the publisher authorizes,
  # by_tag the tags an authorized property carries, all stays while its publisher
  # authorizes a property. A selector left with nothing is dropped, and a product left with
  # none is not offered (core/product.json publisher_properties: minItems 1).
  #
  # all_inventory is the seller's default tag. Selecting it is announced as selection_type
  # "all" on every path, stored selectors included.
  #
  # FRESHNESS. A stored document is usable for 24 hours after the last SUCCESSFUL fetch;
  # any failed fetch keeps the stored document and does not extend that window, and a
  # last_updated older than the stored one is an invalid response (managed-networks.mdx
  # :623-626). Past the window the publisher authorizes nothing. Time is moved through the
  # harness clock -- the seller's last successful fetch is moved back -- never slept.
  #
  # FORMAT OPTIONS. A product references a publisher's format option by
  # {publisher_domain, format_option_id} alone (core/product-format-declaration.json: the
  # pair is the identity) and is served the publisher's CURRENT format_kind and params from
  # the file's formats[] (adagents.json formats: "Products selling this publisher's
  # inventory SHOULD reference these declarations by format_option_id"). An option with
  # applies_to scope applies only to the named properties. An unresolved reference "MUST
  # fail closed" (core/product.json format_options): the product is not offered.
  #
  # TRANSPORTS. The seller ingests a publisher's file through the operator's admin routes
  # on the transport under test, and the buyer reads the result over it. On e2e_rest the
  # live server fetches the file itself, on 443, from the stack's publisher-origin through
  # tls-proxy; in process the harness serves it where the ingest service calls
  # fetch_adagents (tests/harness/publisher_authorization.py).
  #
  # Ungraded by the storyboards: no compliance step compares publisher_domain against the
  # seller's authorized properties or a publisher's formats[].

  Background:
    Given a tenant is configured for product discovery
    And the seller is authorized for property "news_home" of publisher "news-psatest.com" tagged "premium"
    And the seller is authorized for property "sports_home" of publisher "sports-psatest.com" tagged "premium"
    And the seller is authorized for property "weather_home" of publisher "weather-psatest.com" tagged "standard"


  @T-UC-GET-PRODUCTS-publisher-by-tag @publisher_domain_resolution @requires_db
  Scenario: a product selecting by tag names each publisher that carries the tag
    Given the seller offers product "premium" migrated from legacy property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory @publisher_domain_resolution @requires_db
  Scenario: the seller's all_inventory tag names every authorizing publisher
    Given the seller offers product "run_of_network" migrated from legacy property tags "all_inventory"
    When the buyer requests products
    Then product "run_of_network" announces publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "sports-psatest.com", "selection_type": "all"}, {"publisher_domain": "weather-psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-default-all @publisher_domain_resolution @requires_db
  Scenario: a product selecting nothing offers every authorizing publisher whole
    Given the seller offers product "everything" migrated from a legacy selection of no properties
    When the buyer requests products
    Then product "everything" announces publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "sports-psatest.com", "selection_type": "all"}, {"publisher_domain": "weather-psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-unbacked @publisher_domain_resolution @requires_db
  Scenario: a product no authorized property backs is not offered
    Given the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "orphan" naming publisher_properties [{"publisher_domain": "podcast-psatest.com", "property_tags": ["podcast"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-listed-not-named @publisher_domain_resolution @requires_db
  Scenario: a publisher whose file lists a property without authorizing the seller is not named
    Given the publisher "podcast-psatest.com" lists property "podcast_home" tagged "premium" without authorizing the seller for it
    And the seller offers product "premium" migrated from legacy property tags "premium"
    When the buyer requests products
    Then product "premium" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}]

  @T-UC-GET-PRODUCTS-publisher-listed-only @publisher_domain_resolution @requires_db
  Scenario: a product only an unauthorized property backs is not offered
    Given the publisher "podcast-psatest.com" lists property "podcast_home" tagged "podcast" without authorizing the seller for it
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "podcast" naming publisher_properties [{"publisher_domain": "podcast-psatest.com", "property_tags": ["podcast"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  @T-UC-GET-PRODUCTS-publisher-explicit-unauthorized @publisher_domain_resolution @requires_db
  Scenario: a stored selector naming a publisher that authorizes nothing is dropped
    Given the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "stranger-psatest.com", "selection_type": "all"}]
    And the seller offers product "stranger" naming publisher_properties [{"publisher_domain": "stranger-psatest.com", "selection_type": "all"}]
    When the buyer requests products
    Then the buyer receives exactly the products "mixed"
    And product "mixed" announces publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-stored-ids-per-property @publisher_domain_resolution @requires_db
  Scenario: a stored by_id selector names only the properties its publisher authorizes
    Given the publisher "news-psatest.com" lists property "news_live" tagged "live" without authorizing the seller for it
    And the seller offers product "ids" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home", "news_live"], "selection_type": "by_id"}]
    When the buyer requests products
    Then product "ids" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-UC-GET-PRODUCTS-publisher-stored-tag-per-property @publisher_domain_resolution @requires_db
  Scenario: a stored by_tag selector whose tag only an unauthorized property carries is not offered
    Given the publisher "news-psatest.com" lists property "news_live" tagged "live" without authorizing the seller for it
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "live" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_tags": ["live"], "selection_type": "by_tag"}]
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
    Then product "home_package" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather-psatest.com", "property_ids": ["weather_home"], "selection_type": "by_id"}]

  # A by_id selector names its properties; a stray all_inventory tag stored beside them does
  # not widen it to the publisher whole (core/publisher-property-selector.json: the
  # selection_type decides which members apply).
  @T-UC-GET-PRODUCTS-publisher-all-inventory-by-id @publisher_domain_resolution @requires_db
  Scenario: a stored by_id selector carrying a stray all_inventory tag stays by_id
    Given the seller offers product "stray_tag" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "property_tags": ["all_inventory"], "selection_type": "by_id"}]
    When the buyer requests products
    Then product "stray_tag" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "selection_type": "by_id"}]

  @T-UC-GET-PRODUCTS-publisher-product-form @publisher_domain_resolution @requires_db
  Scenario: properties the operator selects on the product form are named one selector per publisher
    Given the operator saves product "home_pages" selecting properties "news_home, weather_home"
    When the buyer requests products
    Then product "home_pages" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "selection_type": "by_id"}, {"publisher_domain": "weather-psatest.com", "property_ids": ["weather_home"], "selection_type": "by_id"}]

  # A selection references its publisher by foreign key, ON DELETE CASCADE: removing a
  # publisher removes every selection of it, and a product left selecting no publisher has
  # no publisher_properties (core/product.json: minItems 1), so it is not offered.
  @T-UC-GET-PRODUCTS-publisher-removed @publisher_domain_resolution @requires_db
  Scenario: removing a publisher drops its selections, and a product left with none is not offered
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the seller offers product "mixed" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "sports-psatest.com", "selection_type": "all"}]
    When the operator removes the publisher "news-psatest.com"
    And the buyer requests products
    Then the buyer receives exactly the products "mixed"
    And product "mixed" announces publisher_properties [{"publisher_domain": "sports-psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-all-inventory-stored @publisher_domain_resolution @requires_db
  Scenario: a stored by_tag selector of the seller's all_inventory tag names its publisher whole
    Given the seller offers product "stored_whole" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_tags": ["all_inventory"], "selection_type": "by_tag"}]
    When the buyer requests products
    Then product "stored_whole" announces publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-canonical-domain @publisher_domain_resolution @requires_db
  Scenario: a publisher given in another spelling is held and named by its host
    Given the seller is authorized for property "mixed_case_home" of publisher "Mixed.Psatest.com" tagged "cased"
    And the seller is authorized for property "dotted_home" of publisher "https://Dotted.Psatest.com./" tagged "cased"
    And the seller offers product "cased" migrated from legacy property tags "cased"
    And the seller offers product "stored_cased" naming publisher_properties [{"publisher_domain": "Mixed.Psatest.com", "selection_type": "all"}]
    When the buyer requests products
    Then product "cased" announces publisher_properties [{"publisher_domain": "dotted.psatest.com", "property_tags": ["cased"], "selection_type": "by_tag"}, {"publisher_domain": "mixed.psatest.com", "property_tags": ["cased"], "selection_type": "by_tag"}]
    And product "stored_cased" announces publisher_properties [{"publisher_domain": "mixed.psatest.com", "selection_type": "all"}]

  @T-UC-GET-PRODUCTS-publisher-rejected-domain @publisher_domain_resolution @requires_db
  Scenario: an authorizing publisher no canonical spelling can name costs that publisher, not the catalog
    Given the seller is authorized for property "ported_home" of publisher "news-psatest.com:8443" tagged "premium"
    And the seller is authorized for property "only_ported_home" of publisher "other-psatest.com:8443" tagged "ported"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "ported" migrated from legacy property tags "ported"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"
    And product "premium" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}, {"publisher_domain": "sports-psatest.com", "property_tags": ["premium"], "selection_type": "by_tag"}]

  # ── Authorization by entry, read from the file ───────────────────────────
  #
  # adagents.json authorized_agents[] variants: property_ids resolves against the file's
  # top-level properties (:261), property_tags matches their tags (:347), inline_properties
  # carries its own properties (:433). An entry is this agent's only when its url names this
  # agent (canonical URL, path significant -- local-publisher-authorization.feature).

  @T-UC-GET-PRODUCTS-publisher-file-authorizes @publisher_domain_resolution @requires_db
  Scenario Outline: the publisher's file authorizes this agent through each entry variant
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And <entry>
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    When the buyer requests products
    Then product "radio" announces publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]

    Examples:
      | variant           | entry                                                                              |
      | property_ids      | the publisher "radio-psatest.com" authorizes this agent for properties "radio_home"   |
      | property_tags     | the publisher "radio-psatest.com" authorizes this agent for properties tagged "audio" |
      | inline_properties | the publisher "radio-psatest.com" authorizes this agent for an inline property "radio_inline" |

  @T-UC-GET-PRODUCTS-publisher-other-agent @publisher_domain_resolution @requires_db
  Scenario: an entry naming another agent authorizes nothing for this one
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" authorizes "https://other-seller.example.com/mcp" for property "radio_home"
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  # An entry scoped to a property the product does not select authorizes nothing for the
  # product: the selection is narrowed to what the file authorizes.
  @T-UC-GET-PRODUCTS-publisher-entry-scope @publisher_domain_resolution @requires_db
  Scenario: an entry scoped to another property authorizes nothing for the product's selection
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" lists property "radio_live" tagged "live" in its adagents.json
    And the publisher "radio-psatest.com" authorizes this agent for properties "radio_home"
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "live" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "property_ids": ["radio_live"], "selection_type": "by_id"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  # effective_until is a qualifier of the entry: outside its window the entry authorizes
  # nothing (adagents.json authorized_agents[] effective_from / effective_until).
  @T-UC-GET-PRODUCTS-publisher-entry-expired @publisher_domain_resolution @requires_db
  Scenario: an entry whose effective_until has passed authorizes nothing
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" authorized this agent for property "radio_home" until 2 hours ago
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  # adagents.mdx §5: "Per-property validation failures MUST NOT prevent processing of
  # remaining properties in the same file. Treat a non-conforming property as absent from
  # the array" (core/property.json requires identifiers). The seller skips it, logs it, and
  # the properties beside it still authorize.
  @T-UC-GET-PRODUCTS-publisher-malformed-property @publisher_domain_resolution @requires_db
  Scenario: a property the file lists without identifiers is skipped and the others still authorize
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" lists property "radio_broken" without identifiers in its adagents.json
    And the publisher "radio-psatest.com" authorizes this agent for properties "radio_home", "radio_broken"
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "property_ids": ["radio_home", "radio_broken"], "selection_type": "by_id"}]
    When the buyer requests products
    Then product "radio" announces publisher_properties [{"publisher_domain": "radio-psatest.com", "property_ids": ["radio_home"], "selection_type": "by_id"}]
    And the seller holds properties "radio_home" from "radio-psatest.com"

  # ── The file changes, and the seller refreshes it ─────────────────────────

  # adagents.mdx §5: "File-level failures (unparseable JSON, missing required top-level
  # authorized_agents) MUST abort processing for that domain". A refreshed file without
  # authorized_agents is an invalid response: the stored file is kept under the 24-hour
  # rule (managed-networks.mdx :623-626) and the attempt is recorded "invalid".
  @T-UC-GET-PRODUCTS-publisher-no-authorized-agents @publisher_domain_resolution @requires_db
  Scenario: a refreshed file without authorized_agents is refused and the stored one kept
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" authorizes this agent for properties "radio_home"
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    And the publisher "radio-psatest.com" serves an adagents.json without authorized_agents
    When the seller refreshes the adagents.json of "radio-psatest.com"
    And the buyer requests products
    Then product "radio" announces publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    And the seller kept the adagents.json of "radio-psatest.com" it stored before, recording "invalid"

  @T-UC-GET-PRODUCTS-publisher-drops-property @publisher_domain_resolution @requires_db
  Scenario: a property the publisher stops listing leaves the selection on refresh
    Given the seller is authorized for property "news_live" of publisher "news-psatest.com" tagged "live"
    And the seller offers product "ids" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home", "news_live"], "selection_type": "by_id"}]
    And the seller offers product "live" naming publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_live"], "selection_type": "by_id"}]
    And the publisher "news-psatest.com" stops listing property "news_live"
    When the seller refreshes the adagents.json of "news-psatest.com"
    And the buyer requests products
    Then product "ids" announces publisher_properties [{"publisher_domain": "news-psatest.com", "property_ids": ["news_home"], "selection_type": "by_id"}]
    And the buyer receives exactly the products "ids"

  # A failed fetch (404, 5xx) keeps the stored document and does not move the last
  # successful fetch. So within 24 hours of it the product is still offered, and once the
  # 24 hours pass it is not -- the failed fetch in between extended nothing.
  @T-UC-GET-PRODUCTS-publisher-fetch-fails @publisher_domain_resolution @requires_db
  Scenario Outline: a failed refresh keeps the stored file for 24 hours after the last successful fetch
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the seller's last successful fetch of "news-psatest.com" was 23 hours ago
    And the publisher "news-psatest.com" now answers <status>
    When the seller refreshes the adagents.json of "news-psatest.com"
    And the buyer requests products
    Then product "news" announces publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the seller kept the adagents.json of "news-psatest.com" it stored before, recording "<recorded>"
    Given the seller's last successful fetch of "news-psatest.com" was 25 hours ago
    When the buyer requests products
    Then the buyer receives no products

    Examples:
      | status | recorded    |
      | 404    | not_found   |
      | 503    | unreachable |

  @T-UC-GET-PRODUCTS-publisher-stale-last-updated @publisher_domain_resolution @requires_db
  Scenario: a file dated before the stored one is refused and the stored one kept
    Given the publisher "radio-psatest.com" lists property "radio_home" tagged "audio" in its adagents.json
    And the publisher "radio-psatest.com" authorizes this agent for properties "radio_home"
    And the publisher "radio-psatest.com" dates its adagents.json "2026-06-01T00:00:00Z"
    And the seller ingested the adagents.json of "radio-psatest.com"
    And the seller offers product "radio" naming publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    And the publisher "radio-psatest.com" stops listing property "radio_home"
    And the publisher "radio-psatest.com" dates its adagents.json "2026-05-01T00:00:00Z"
    When the seller refreshes the adagents.json of "radio-psatest.com"
    And the buyer requests products
    Then product "radio" announces publisher_properties [{"publisher_domain": "radio-psatest.com", "selection_type": "all"}]
    And the seller kept the adagents.json of "radio-psatest.com" it stored before, recording "invalid"

  # revoked_publisher_domains: validators "MUST treat any publisher domain listed here as
  # no-longer-authorized" (adagents.json :106).
  @T-UC-GET-PRODUCTS-publisher-revoked @publisher_domain_resolution @requires_db
  Scenario: a publisher whose file revokes its own domain is not offered
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the seller offers product "sports" naming publisher_properties [{"publisher_domain": "sports-psatest.com", "selection_type": "all"}]
    And the publisher "news-psatest.com" lists its own domain in revoked_publisher_domains
    When the seller refreshes the adagents.json of "news-psatest.com"
    And the buyer requests products
    Then the buyer receives exactly the products "sports"

  # authorized_agents: [] asserts no sales authorization; validators "MUST NOT read it as
  # deny-all, authorize-all, or a revocation ... MUST still consume the catalog arrays"
  # (adagents.json :245). So nothing is authorized, and the formats[] catalog is stored.
  @T-UC-GET-PRODUCTS-publisher-no-agents @publisher_domain_resolution @requires_db
  Scenario: a file authorizing no agent offers nothing and its format catalog is still stored
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the seller offers product "sports" naming publisher_properties [{"publisher_domain": "sports-psatest.com", "selection_type": "all"}]
    And the publisher "news-psatest.com" authorizes no agent in its adagents.json
    And the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    When the seller refreshes the adagents.json of "news-psatest.com"
    And the buyer requests products
    Then the buyer receives exactly the products "sports"
    And the seller stored format options "news_mrec" of "news-psatest.com"

  # ── Format options from the publisher's catalog ───────────────────────────

  @T-UC-GET-PRODUCTS-format-option-resolved @publisher_domain_resolution @requires_db
  Scenario: a product referencing a publisher's format option is served the publisher's declaration
    Given the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "news" selling publisher "news-psatest.com" whole with format option "news_mrec" of "news-psatest.com"
    When the buyer requests products
    Then product "news" announces format_options [{"format_kind": "image", "format_option_id": "news_mrec", "publisher_domain": "news-psatest.com", "params": {"width": 300, "height": 250}}]

  @T-UC-GET-PRODUCTS-format-option-changed @publisher_domain_resolution @requires_db
  Scenario: a publisher that changes its option's params is served the new params after a refresh
    Given the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "news" selling publisher "news-psatest.com" whole with format option "news_mrec" of "news-psatest.com"
    And the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 320, "height": 50}} in its adagents.json
    When the seller refreshes the adagents.json of "news-psatest.com"
    And the buyer requests products
    Then product "news" announces format_options [{"format_kind": "image", "format_option_id": "news_mrec", "publisher_domain": "news-psatest.com", "params": {"width": 320, "height": 50}}]

  # The operator picks the publisher's option on the product form, which stores its key; the
  # buyer is served the publisher's declaration under that key on every transport.
  @T-UC-GET-PRODUCTS-format-option-picked-on-form @publisher_domain_resolution @requires_db
  Scenario: a publisher option the operator picks on the product form is served from the publisher's catalog
    Given the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the operator saved the edit form of product "news" with format option "news_mrec" of "news-psatest.com"
    When the buyer requests products
    Then product "news" announces format_options [{"format_kind": "image", "format_option_id": "news_mrec", "publisher_domain": "news-psatest.com", "params": {"width": 300, "height": 250}}]

  @T-UC-GET-PRODUCTS-format-option-missing @publisher_domain_resolution @requires_db
  Scenario: a product referencing an option missing from the publisher's formats is not offered
    Given the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "news" selling publisher "news-psatest.com" whole with format option "news_mrec" of "news-psatest.com"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  # applies_to_property_ids / applies_to_property_tags scope an option to a subset of the
  # file's properties (adagents.json formats :164, :170); the product sells none of them.
  @T-UC-GET-PRODUCTS-format-option-out-of-scope @publisher_domain_resolution @requires_db
  Scenario: a product whose properties the option's scope excludes is not offered
    Given the seller is authorized for property "news_live" of publisher "news-psatest.com" tagged "live"
    And the publisher "news-psatest.com" declares format option {"format_option_id": "live_banner", "format_kind": "image", "params": {"width": 728, "height": 90}, "applies_to_property_ids": ["news_live"]} in its adagents.json
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "premium" migrated from legacy property tags "premium"
    And the seller offers product "home" selling property "news_home" of publisher "news-psatest.com" with format option "live_banner" of "news-psatest.com"
    When the buyer requests products
    Then the buyer receives exactly the products "premium"

  # An option resolves only while its publisher's stored file is usable: past 24 hours after
  # the last successful fetch the publisher authorizes nothing (managed-networks.mdx
  # :623-626), so the product no longer sells it and an option of it fails closed
  # (core/product.json format_options). The same product without the option is still
  # offered for its other publisher.
  @T-UC-GET-PRODUCTS-format-option-stale @publisher_domain_resolution @requires_db
  Scenario: a product's option of a publisher whose file is past 24 hours fails closed
    Given the publisher "news-psatest.com" declares format option {"format_option_id": "news_mrec", "format_kind": "image", "params": {"width": 300, "height": 250}} in its adagents.json
    And the seller ingested the adagents.json of "news-psatest.com"
    And the seller offers product "with_option" carrying format option "news_mrec" of "news-psatest.com" and naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "sports-psatest.com", "selection_type": "all"}]
    And the seller offers product "without_option" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}, {"publisher_domain": "sports-psatest.com", "selection_type": "all"}]
    And the seller's last successful fetch of "news-psatest.com" was 25 hours ago
    When the buyer requests products
    Then the buyer receives exactly the products "without_option"
    And product "without_option" announces publisher_properties [{"publisher_domain": "sports-psatest.com", "selection_type": "all"}]

  # A format option's publisher_domain names the publisher whose adagents.json formats[]
  # declares it. The host this agent serves its own adagents.json at publishes no formats[],
  # so an option under it resolves nowhere and the product is not offered.
  @T-UC-GET-PRODUCTS-format-option-agent-host @publisher_domain_resolution @requires_db
  Scenario: a product whose format option names the seller's own host as publisher is not offered
    Given the seller offers product "news" naming publisher_properties [{"publisher_domain": "news-psatest.com", "selection_type": "all"}]
    And the tenant also holds product "host_option" declaring format option "homepage_mrec" under the seller's own host
    When the buyer requests products
    Then no product announces the seller's own host as a format option's publisher_domain
    And the buyer receives exactly the products "news"
