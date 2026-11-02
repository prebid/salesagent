# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: the BR-UC-002 / BR-UC-003 storyboards have no scenario that
# sends a request violating a *length or range* constraint the pinned request
# schema declares. Every generated error scenario exercises a business rule or
# a wrong TYPE; none sends a well-typed value that is out of bounds. That is
# exactly the relaxed surface: four local request
# models redeclare a parent field and drop the constraint the pin carries, so
# the out-of-bounds payload is accepted (or rejected with the wrong code).
#
# Each scenario cites the pinned constraint it grades. Schemas are read from
# the installed adcp SDK's own pinned tree (adcp==6.6.0 -> _schemas/3.1),
# which is the authority; the SDK's Python annotation is only a cross-check.
#
# Reconcile upstream in adcp-req, then retire this file in favour of the
# regenerated scenarios.
Feature: Request-schema bounds the local models must enforce (local)

  # ── create_media_buy ────────────────────────────────────────────────
  # Routed to MediaBuyCreateEnv by the T-UC-002-ext- tag prefix
  # (tests/bdd/conftest.py, the "extension/error scenarios" branch).

  @T-UC-002-ext-bounds-impressions @extension @error @local-relaxation
  Scenario: A negative package impression goal is refused as a correctable request defect
    # pin: media-buy/package-request.json .properties.impressions.minimum = 0
    Given a valid create_media_buy request
    And the account exists and is active
    But a package carries an impression goal of -5
    When the Buyer Agent sends the create_media_buy request
    Then the error is compliant with the AdCP error spec
    And the request is refused on the wire with code "INVALID_REQUEST" recovery "correctable" naming field "packages[0].impressions"
    And no media buy is persisted for the tenant

  @T-UC-002-ext-bounds-packages @extension @error @local-relaxation
  Scenario: A create request carrying an empty packages array is refused
    # pin: media-buy/create-media-buy-request.json .properties.packages.minItems = 1
    Given a valid create_media_buy request
    And the account exists and is active
    But the request carries an empty packages array
    When the Buyer Agent sends the create_media_buy request
    Then the error is compliant with the AdCP error spec
    And the request is refused on the wire with code "INVALID_REQUEST" recovery "correctable" naming field "packages"
    And no media buy is persisted for the tenant

  @T-UC-002-ext-bounds-creatives @extension @error @local-relaxation
  Scenario: A package carrying an empty creatives array is refused instead of silently skipped
    # pin: media-buy/package-request.json .properties.creatives.minItems = 1 (maxItems 100)
    # Buyer-visible SEMANTIC change, not only a constraint: today
    # src/core/helpers/creative_helpers.py:555 ("if not pkg.creatives: continue")
    # reads [] the same as None — "this package has no creatives" — and skips it,
    # so the buy is CREATED and reports pending_creatives. A buyer who sent []
    # by mistake gets a live media buy with no creatives attached and no signal
    # that anything was dropped. The pin says [] is not a legal value.
    Given a valid create_media_buy request
    And the account exists and is active
    But a package carries an empty creatives array
    When the Buyer Agent sends the create_media_buy request
    Then the error is compliant with the AdCP error spec
    And the request is refused on the wire with code "INVALID_REQUEST" recovery "correctable" naming field "packages[0].creatives"
    And no media buy is persisted for the tenant

  # ── update_media_buy ────────────────────────────────────────────────
  # Routed to MediaBuyDualEnv by the T-UC-003-ext- tag prefix, against the
  # media buy the conftest branch seeds.

  @T-UC-003-ext-bounds-packages @extension @error @local-relaxation
  Scenario: An update carrying an empty packages array is refused, not treated as a no-op success
    # pin: media-buy/update-media-buy-request.json .properties.packages.minItems = 1
    # The A2A wrapper's own pre-validation model (adcp_a2a_server.py:1982) omits
    # packages, so this row also proves there is no A2A divergence: both wrappers
    # reach the shared builder at media_buy_update.py:1489, which forwards
    # packages whenever it is not None — and [] is not None.
    Given an existing media buy to update
    But the update carries an empty packages array
    When the Buyer Agent sends the update_media_buy request as raw wire parameters
    Then the error is compliant with the AdCP error spec
    And the request is refused on the wire with code "INVALID_REQUEST" recovery "correctable" naming field "packages"
    And the media buy's persisted revision is unchanged
