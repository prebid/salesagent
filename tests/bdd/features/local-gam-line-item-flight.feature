# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the flight a Google Ad Manager seller writes on the line item it books for a
# buyer's create_media_buy. AdCP 3.1.1 create_media_buy.mdx (request table) defines start_time
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
# @source repo=adcp ref=v3.1.1 path=docs/media-buy/task-reference/create_media_buy.mdx
# @source repo=adcp ref=v3.1.1 path=static/schemas/source/core/start-timing.json
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/specialisms/sales-non-guaranteed/index.yaml pointer=/phases/create_buy
# @source repo=adcp ref=v3.1.1 path=static/schemas/source/enums/error-code.json
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
