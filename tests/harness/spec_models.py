"""Pinned SDK model lookups used by the harness client.

Lives under ``tests/`` on purpose. ``spec_response_model`` is a response-side
parse-back helper for :mod:`tests.harness.client`; it never had a production
caller, and keeping it in ``src/core/version_compat.py`` meant a production
module carried a test-only export.

The REQUEST-side sibling, ``spec_request_model``, stays in production: the
acceptance seam itself depends on it.
"""

from pydantic import BaseModel

from tests.helpers.adcp_domain_types import response_declaration


def spec_response_model(tool_name: str) -> type[BaseModel] | None:
    """The pinned SDK response model for an MCP tool, or None if there isn't one
    single class to parse into.

    `get_products` -> `adcp.types.domains.media_buy.get_products_response.GetProductsResponse`.
    Mirrors `spec_request_model`, mechanically, with the same "a miss carries
    information" reading: several tools resolve to a `Union` of outcome
    variants at the SDK level (`create_media_buy` -> immediate success /
    validation-error / async-task variants) rather than one plain
    `BaseModel` subclass — `isinstance(model, type)` is False for those
    (`types.UnionType`, not a class), so this deliberately returns `None`
    for them rather than guessing which union member a given wire dict
    matches. "No model" therefore means "no single pinned class to parse
    the wire body into", not "this tool has no response schema at all".

    THE MODEL MUST BE ONE PRODUCTION ACTUALLY ANSWERS WITH, and reading the flat
    `adcp.types` namespace silently stopped satisfying that at adcp 9.0.0. The
    flat `GetProductsResponse` became an `adcp.types.canonical_creative` copy
    that is NOT in the MRO of `src.core.schemas.product.GetProductsResponse`
    (production inherits `LegacyGetProductsResponse`, the `domains.media_buy`
    class). So the harness parsed the seller's response back through a contract
    the seller never claimed to satisfy, and the canonical model refuses legacy
    `format_ids` outright: "contains legacy creative identity; use an explicit
    Legacy* model". Four tools were affected — `get_products`, `list_creatives`,
    `get_media_buys`, `get_media_buy_delivery` — and production was correct
    throughout; the defect was entirely harness-side.

    A WRONG CLASS THAT STILL PARSES is the worst shape of this bug, which is why
    `tests/unit/test_response_schema_category_resolves.py` asserts class identity
    against production's own response type rather than against a name. The
    pre-existing name check (`spec_response_model("get_products").__name__ ==
    "GetProductsResponse"`) passed throughout — both classes carry that name.
    """
    _, declared = response_declaration(tool_name)
    return declared if isinstance(declared, type) and issubclass(declared, BaseModel) else None
