# Manual feature: what a stored pricing option looks like when a buyer discovers it.

Feature: Pricing options a seller stores are announced to buyers on the wire
  As a Buyer Agent
  I want each of the seller's pricing options to reach me as its typed AdCP option
  So that I can select one by its pricing_option_id and know what it costs

  # The seller stores pricing in its own columns (pricing_model, rate, currency,
  # is_fixed, price_guidance, parameters) and announces AdCP's nine typed options
  # (pricing-options/*.json). Nothing but get_products shows whether that translation
  # is right, and eight of the nine models had no scenario at all — their only coverage
  # called the translator directly with a hand-built option, which grades the translator
  # against itself and never once asks what a buyer receives.
  #
  # Every scenario here therefore stores a REAL row and reads the REAL wire.
  #
  # pricing_option_id is what makes this more than presentation: it is REQUIRED by all
  # nine option schemas, and media-buy/package-request.json marks the buyer's copy
  # `x-entity: product_pricing_option`. The buyer selects an option by sending this
  # string back, so the id announced here is a promise the seller has to keep.

  Background:
    Given a tenant is configured for product discovery


  @T-UC-GET-PRODUCTS-pricing-fixed @pricing_option_announcement @requires_db
  Scenario Outline: a stored fixed <pricing_model> option reaches the buyer intact
    Given the seller stores a fixed "<pricing_model>" pricing option at <rate> "USD" with parameters <parameters>
    When the buyer requests products
    Then the buyer receives the pricing option <announced>

    Examples: one row per pricing model, with the exact option the buyer receives
      | pricing_model | rate    | parameters                    | announced                                                                                                                          |
      | cpm           | 5.00    | none                          | {"currency": "USD", "fixed_price": 5.0, "max_bid": false, "pricing_model": "cpm", "pricing_option_id": "cpm_usd_fixed"}             |
      | vcpm          | 8.50    | none                          | {"currency": "USD", "fixed_price": 8.5, "max_bid": false, "pricing_model": "vcpm", "pricing_option_id": "vcpm_usd_fixed"}           |
      | cpc           | 1.25    | none                          | {"currency": "USD", "fixed_price": 1.25, "max_bid": false, "pricing_model": "cpc", "pricing_option_id": "cpc_usd_fixed"}            |
      | cpcv          | 0.05    | none                          | {"currency": "USD", "fixed_price": 0.05, "max_bid": false, "pricing_model": "cpcv", "pricing_option_id": "cpcv_usd_fixed"}          |
      | cpv           | 0.10    | {"view_threshold": 0.75}      | {"currency": "USD", "fixed_price": 0.1, "max_bid": false, "parameters": {"view_threshold": 0.75}, "pricing_model": "cpv", "pricing_option_id": "cpv_usd_fixed"} |
      | cpp           | 12.00   | {"demographic": "A18-49"}     | {"currency": "USD", "fixed_price": 12.0, "parameters": {"demographic": "A18-49"}, "pricing_model": "cpp", "pricing_option_id": "cpp_usd_fixed"} |
      | flat_rate     | 1000.00 | none                          | {"currency": "USD", "fixed_price": 1000.0, "pricing_model": "flat_rate", "pricing_option_id": "flat_rate_usd_fixed"}                |
      | cpa           | 25.00   | {"event_type": "qualify_lead"} | {"currency": "USD", "event_type": "qualify_lead", "fixed_price": 25.0, "pricing_model": "cpa", "pricing_option_id": "cpa_usd_fixed"} |
      | time          | 100.00  | {"time_unit": "week"}         | {"currency": "USD", "fixed_price": 100.0, "parameters": {"time_unit": "week"}, "pricing_model": "time", "pricing_option_id": "time_usd_fixed"} |
    # No row uses a value production could plausibly hardcode as a default: the cpa row
    # asks for qualify_lead rather than purchase, and the time row for week rather than
    # day. Measured — with "purchase" stored, pinning event_type to the constant
    # EventType.purchase in production left all 63 green, so the field was not graded.
    #
    # cpa promotes event_type out of the stored parameters to a top-level field, because
    # cpa-option.json declares it there and puts it in `required`. cpp and time keep
    # theirs under `parameters`, which is where their own schemas declare them.
    # max_bid appears only on the five bid-based models — it is not a field of the other four.

  @T-UC-GET-PRODUCTS-pricing-auction @pricing_option_announcement @requires_db
  Scenario Outline: a stored auction <pricing_model> option reaches the buyer with its floor
    Given the seller stores an auction "<pricing_model>" pricing option with guidance <guidance> in "USD"
    When the buyer requests products
    Then the buyer receives the pricing option <announced>

    Examples: auction pricing — the floor moves out of price_guidance to floor_price
      | pricing_model | guidance                   | announced                                                                                                                                                                     |
      | cpm           | {"floor": 2.0, "p25": 3.0} | {"currency": "USD", "floor_price": 2.0, "max_bid": false, "price_guidance": {"floor": 2.0, "p25": 3.0}, "pricing_model": "cpm", "pricing_option_id": "cpm_usd_auction"}         |
      | vcpm          | {"floor": 4.0}             | {"currency": "USD", "floor_price": 4.0, "max_bid": false, "price_guidance": {"floor": 4.0}, "pricing_model": "vcpm", "pricing_option_id": "vcpm_usd_auction"}                   |
      | cpc           | {"floor": 0.5, "p50": 1.0} | {"currency": "USD", "floor_price": 0.5, "max_bid": false, "price_guidance": {"floor": 0.5, "p50": 1.0}, "pricing_model": "cpc", "pricing_option_id": "cpc_usd_auction"} |
    # Every auction row carries a floor because the database requires one --
    # check_auction_has_price_guidance demands price_guidance with a 'floor' key. A
    # floorless auction option is not a case production can meet.

  @T-UC-GET-PRODUCTS-pricing-id-case @pricing_option_announcement @requires_db
  Scenario: the announced pricing_option_id is lowercase whatever case the seller stored
    # The defect this closes: the id used to be recomputed at each reader from the
    # stored columns, and the readers disagreed about case. get_products lowercased the
    # model, the delivery resolver did not — so a row stored as "CPM" was announced as
    # cpm_usd_fixed and then looked up as CPM_usd_fixed, matching nothing. The id is
    # stored now, so there is one spelling and only one.
    Given the seller stores a fixed "CPM" pricing option at 5.00 "USD" with parameters none
    When the buyer requests products
    Then the announced pricing option "pricing_option_id" is "cpm_usd_fixed"

  @T-UC-GET-PRODUCTS-pricing-id-distinct @pricing_option_announcement @requires_db
  Scenario: two options on one product are announced under distinct ids
    # pricing_option_id must identify ONE option within the product — otherwise a buyer
    # sending it back names two rows and the seller picks whichever it happens to find.
    Given the seller stores a fixed "cpm" pricing option at 5.00 "USD" with parameters none
    And the seller stores an auction "cpm" pricing option with guidance {"floor": 2.0} in "USD" on the same product
    When the buyer requests products
    Then the announced pricing option ids are ["cpm_usd_auction", "cpm_usd_fixed"]

  @T-UC-GET-PRODUCTS-pricing-unannounceable @pricing_option_announcement @requires_db
  Scenario Outline: an option the seller cannot announce is refused, not half-announced
    # A stored row missing what its model requires cannot become a valid AdCP option.
    # The seller answers INTERNAL_ERROR — it is the seller's own data that is wrong, and
    # there is nothing the buyer can correct — rather than emitting an option that would
    # fail the buyer's own schema check.
    #
    # Only defects the DATABASE can actually hold are listed. A fixed option with no rate
    # and an auction option with no floor were here too, and both are unreachable:
    # check_fixed_has_rate and check_auction_has_price_guidance reject them at INSERT, so
    # production can never meet such a row and a scenario demanding a response to one
    # grades fiction. What remains are the model-specific `parameters` requirements, which
    # no constraint covers.
    Given the seller stores <a defective option>
    When the buyer requests products
    Then the response contains error code INTERNAL_ERROR

    Examples: stored rows that cannot be announced
      | a defective option                                                              |
      | a fixed "cpp" pricing option at 12.00 "USD" with parameters none                |
      | a fixed "cpa" pricing option at 25.00 "USD" with parameters none                |
      | a fixed "cpa" pricing option at 25.00 "USD" with parameters {"event_type": "teleport"} |
      | a fixed "time" pricing option at 100.00 "USD" with parameters none              |
