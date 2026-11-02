# `buyer_ref`: what the pin says and what remains in the tree

This page used to be the working plan for removing `buyer_ref` from the BDD
scenarios, the step definitions, and the production error text. That work has
landed on both sides, so the plan is retired and only the durable facts stay
here. Measured 2026-09-15: no top-level `buyer_ref` or `buyer_refs` remains in
`tests/bdd/features/`, `tests/bdd/steps/`, or `src/`, and the upstream scenario
source (`adcp-req/tests/features/`) matches.

## The pinned shape

At the pinned AdCP version (3.1.1, `adcp==6.6.0`), `buyer_ref` and `buyer_refs`
are **not a declared field on any media-buy message** — not on the create or
update request or response, not on `core/package.json`, `package-request`,
`package-update`, or the delivery request. Do not add one to a DTO, a scenario,
or a data table. Identity is `media_buy_id`, `package_id`, or `product_id`;
`buyer_campaign_ref` is a real field and is unrelated.

Correlation travels on `context`, the opaque freeform object the pin puts on
every message. The pinned schemas *recommend* a per-package correlation value
there, naming `context.buyer_ref` as the common spelling
(`_schemas/3.1/core/package.json`, `media-buy/package-request.json`), while
calling the top-level field deprecated. Nothing declares a `buyer_ref` property
inside `context`, because nothing declares any property inside it.

## What deliberately remains, and must not be "cleaned up"

- The two absence assertions in
  `tests/bdd/features/BR-UC-003-update-media-buy.feature` ("the response should
  NOT contain `buyer_ref` field"). They grade the removal.
- The deprecation comment in `BR-UC-026-package-media-buy.feature`, and the two
  comments in `src/core/schemas/_base.py` recording that the SDK 5.7 parent
  wrongly declared the field.
- The undeclared-scalar fixture in
  `tests/bdd/steps/domain/local_context_echo.py`, which uses `buyer_ref` as a
  key inside `context` precisely because the pin declares no such property —
  that is the point of the case.
- The `context.buyer_ref` text inside the `PACKAGE_NOT_FOUND` suggestion. It is
  the pin's own `enumMetadata` sentence, loaded into `CODE_TABLE`
  (`src/core/errors/codes.py`) and served unedited. Buyer-facing suggestions are
  a function of the error code, so there is no per-site string in `src/` to edit
  and no local wording to substitute.
