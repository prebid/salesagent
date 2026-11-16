# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the flight a Google Ad Manager seller writes on the line item it books for a
# buyer's create_media_buy. AdCP 3.2.7 create_media_buy.mdx (request table) defines start_time
# as "asap" or an ISO 8601 date-time and end_time as an ISO 8601 date-time, "UTC unless timezone
# specified"; core/start-timing.json defines "asap" as "Start campaign as soon as possible".
# Both name absolute instants, so the line item must serve exactly those instants.
#
# Google Ad Manager reads a DateTime's date, hour, minute and second in the zone its
# timeZoneId names, and a network delivers in its own zone (NetworkService Network.timeZone,
# read-only). LineItem.startDateTime "must be in the future"; StartDateTimeType.IMMEDIATELY
# starts serving at once and ignores startDateTime. Order.startDateTime / endDateTime are
# read-only, derived from the order's line items.
#
# The sales-non-guaranteed storyboard's create_media_buy step sends start_time "asap" and
# end_time "2099-06-30T23:59:59Z" and carries the `upstream_traffic` check, which this repo's
# storyboard runner cannot observe from the buyer's wire
# (docs/test-obligations/storyboard-wireability.yaml). These scenarios grade that booking
# in-process: the real GoogleAdManager adapter runs, and only Google's SOAP client is stood in.
#
# The scheduled dates are fixed and lie in the future until 2035.
#
# The seller asks the network for its zone before it creates anything in GAM. When GAM refuses
# that lookup, the buyer reads the code the seller's GAM fault classification assigns, from the
# pinned enums/error-code.json (CONFIGURATION_ERROR: the seller's deployment is misconfigured,
# terminal; RATE_LIMITED: transient), and GAM holds no order or line item for the buy.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the setup
# declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
# @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/pricing-models.mdx
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/start-timing.json
# @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/specialisms/sales-non-guaranteed/index.yaml pointer=/phases/create_buy
# @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json
Feature: A Google Ad Manager seller books the buyer's flight in the network's time zone (local)

  @T-UC-002-local-gam-flight @extension
  Scenario Outline: A scheduled flight is written as the buyer's instants, in the network's own time zone
    Given a valid create_media_buy request
    And the start_time is <start_time>
    And the end_time is <end_time>
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", on a network in time zone "<zone>"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager received one line item, starting at "<gam_start>" and ending at "<gam_end>" in time zone "<zone>"
    And Google Ad Manager received one order, carrying no flight dates of its own

    Examples:
      | start_time                | end_time                  | zone                | gam_start           | gam_end             |
      | 2035-01-15T15:00:00Z      | 2035-01-31T23:59:59Z      | America/Los_Angeles | 2035-01-15 07:00:00 | 2035-01-31 15:59:59 |
      | 2035-07-16T15:00:00Z      | 2035-07-31T23:59:59Z      | America/Los_Angeles | 2035-07-16 08:00:00 | 2035-07-31 16:59:59 |
      | 2035-01-15T10:00:00-05:00 | 2035-02-01T00:30:00+01:00 | America/Los_Angeles | 2035-01-15 07:00:00 | 2035-01-31 15:30:00 |
      | 2035-03-01T16:00:00Z      | 2035-03-31T16:00:00Z      | America/Los_Angeles | 2035-03-01 08:00:00 | 2035-03-31 09:00:00 |
      | 2035-01-15T15:00:00Z      | 2035-01-31T23:59:59Z      | Asia/Kolkata        | 2035-01-15 20:30:00 | 2035-02-01 05:29:59 |

  @T-UC-002-local-gam-flight-asap @extension
  Scenario: An "asap" buy starts serving immediately, and ends at the buyer's instant in the network's time zone
    Given a valid create_media_buy request
    And the start_time is asap
    And the end_time is 2099-06-30T23:59:59Z
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", on a network in time zone "America/Los_Angeles"
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And Google Ad Manager received one line item, starting immediately and ending at "2099-06-30 16:59:59" in time zone "America/Los_Angeles"

  # A buy the seller approves after its start serves over the days it has left. AdCP 3.2.7
  # pricing-models.mdx: a flat rate is a "Fixed cost for the entire campaign period", and
  # create_media_buy.mdx: a package's budget is a "Hard lifetime package spend cap". Spreading
  # either over the flight's original days would deliver or bill less than was sold, and rounding
  # the days left down would spread more than was sold, so they round up. Eleven days remain in
  # the rows that pass four: 7000.00 is billed at 636.363636 a day, and 140000 impressions at
  # 12727 a day. Nobody has confirmed how real GAM bills CPD for a partial day.
  @T-UC-002-local-gam-approved-after-start @extension
  Scenario Outline: A buy the seller approves after its start sizes its per-day figures over the days it still serves - <case>
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller's <delivery> product has pricing options:
      | product_id | pricing_option_id | pricing_model | fixed_price | floor_price |
      | prod_a     | <option>          | <model>       | <fixed>     | <floor>     |
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", with product settings '<settings>'
    And the tenant requires manual approval
    And the buyer's packages, over a 14-day flight, are:
      | product_id | pricing_option_id | budget   | bid_price |
      | prod_a     | <option>          | <budget> | <bid>     |
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When <passed> days pass before the seller approves the media buy
    And the seller approves the media buy
    Then the seller's approval books the media buy
    And Google Ad Manager received one line item, which starts serving <starts>
    And Google Ad Manager received these line items, in package order:
      | lineItemType | priority   | costType   | costPerUnitMicros | goalType   | unitType    | units   |
      | <type>       | <priority> | <costType> | <micros>          | <goalType> | IMPRESSIONS | <units> |

    Examples:
      | case                     | passed | delivery       | option                    | model     | fixed   | floor | settings                       | budget | bid  | starts                 | type           | priority | costType | micros    | goalType | units |
      | flat rate before start   | 0      | guaranteed     | flat_rate_usd_sponsorship | flat_rate | 7000.00 |       | {"line_item_type": "STANDARD"} | 7000   |      | at its scheduled start | SPONSORSHIP    | 4        | CPD      | 500000000 | DAILY    | 100   |
      | flat rate after start    | 4      | guaranteed     | flat_rate_usd_sponsorship | flat_rate | 7000.00 |       | {"line_item_type": "STANDARD"} | 7000   |      | immediately            | SPONSORSHIP    | 4        | CPD      | 636363636 | DAILY    | 100   |
      | daily goal before start  | 0      | non-guaranteed | cpm_usd_auction           | cpm       |         | 1.00  | {"primary_goal_type": "DAILY"} | 560    | 4.00 | at its scheduled start | PRICE_PRIORITY | 12       | CPM      | 4000000   | DAILY    | 10000 |
      | daily goal after start   | 4      | non-guaranteed | cpm_usd_auction           | cpm       |         | 1.00  | {"primary_goal_type": "DAILY"} | 560    | 4.00 | immediately            | PRICE_PRIORITY | 12       | CPM      | 4000000   | DAILY    | 12727 |

  @T-UC-002-local-gam-flight-zone-refused @extension
  Scenario Outline: A network that refuses to name its time zone gets no order, and the buyer reads the classified fault
    Given a valid create_media_buy request
    And the start_time is 2035-01-15T15:00:00Z
    And the end_time is 2035-01-31T23:59:59Z
    And the account exists and is active
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001", on a network that refuses to name its time zone with "<fault>"
    When the Buyer Agent sends the create_media_buy request
    Then the response contains error code <code>
    And Google Ad Manager received no order and no line item

    Examples:
      | fault                                           | code                |
      | AuthenticationError.NETWORK_API_ACCESS_DISABLED | CONFIGURATION_ERROR |
      | QuotaError.EXCEEDED_QUOTA                       | RATE_LIMITED        |
