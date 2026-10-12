# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: the package a seller persists for a create_media_buy request records what
# the buyer booked, whatever the ad server's adapter echoes. AdCP 3.1.1
# media-buy/get-media-buys-response.json: packages[].product_id "sellers MUST echo the
# request package's product_id on every response package object that represents that
# requested package"; packages[].targeting_overlay "Sellers SHOULD echo any persisted
# targeting". The pending_creatives_to_start storyboard grades the product_id half
# (step get_media_buy_after_sync, field_equals_context media_buys[0].packages[0].product_id);
# the budget and targeting halves are graded here.
#
# Transports: a2a, mcp and rest on the in-process adapter double, which echoes only the
# package id; e2e_rest on the live server's Mock adapter.
#
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/domains/media-buy/scenarios/pending_creatives_to_start.yaml
Feature: UC-019 get_media_buys reports the package the buyer booked (local)

  @T-UC-019-local-post-create-package-record @post-create-poll
  Scenario: get_media_buys reports the product, budget and targeting the buyer booked for a package
    Given the buyer captured a media_buy_id from a successful create_media_buy response for one package spending 5000 targeting country "US"
    When the Buyer Agent calls get_media_buys with that media_buy_id under the same account
    Then the response is compliant with the get_media_buys spec
    And the freshly-created buy's package reports the requested product_id, budget 5000 and targeting_overlay geo_countries "US"
