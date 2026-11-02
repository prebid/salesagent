"""``PackageRequest.creatives`` accepts what ``core/creative-asset.json`` accepts.

The item type of a REQUEST field decides what create_media_buy admits. Pointing it at a
RESPONSE model widens the accepted shape, because a listing model carries the fields a
seller emits, not the constraints a buyer must satisfy — here ``assets``, which the pin
types as a map of discriminated ``AssetVariant`` objects and the listing model stores as
an untyped dict.

Grounding: AdCP 3.1.1 (the pinned version), ``core/creative-asset.json`` — ``assets`` is
required and each slot value carries an ``asset_type`` discriminator.
``media-buy/package-request.json`` types ``creatives[]`` as that same CreativeAsset.
Graded by ``tests/bdd/features/BR-UC-002-create-media-buy.feature``
@T-UC-002-inv-015-6 (BR-RULE-015 INV-6), which requires ``INVALID_REQUEST``.

Error code: ``INVALID_REQUEST``, not ``VALIDATION_ERROR``. ``3.1/enums/error-code.json``
splits them on whether the schema or business logic is the judge — INVALID_REQUEST is
"malformed, missing required fields, or violates schema constraints"; VALIDATION_ERROR is
"invalid field values or violates business rules BEYOND schema validation". A missing
discriminator is a schema constraint, and ``src/core/exceptions.py::adcp_error_for``
already maps every pydantic ``ValidationError`` to INVALID_REQUEST on that reasoning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.types import ErrorCode
from pydantic import ValidationError

from src.core.exceptions import adcp_error_for
from src.core.schemas import CreateMediaBuyRequest, PackageRequest
from src.core.schemas.creative import CreativeAssetRequest

_FORMAT_ID = {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}

# The payload @T-UC-002-inv-015-6 describes: the assets map value carries no ``asset_type``,
# so no branch of the AssetVariant union can be selected.
CREATIVE_WITH_UNDISCRIMINATED_ASSET = {
    "creative_id": "cr_no_asset_type",
    "name": "Missing asset_type discriminator",
    "format_id": _FORMAT_ID,
    "assets": {"banner": {"url": "https://cdn.example.com/a.png", "width": 300, "height": 250}},
}

# The same creative made spec-legal by naming the discriminator. Present so a green result
# above cannot be bought by refusing everything.
CREATIVE_WITH_DISCRIMINATED_ASSET = {
    "creative_id": "cr_ok",
    "name": "Discriminated asset",
    "format_id": _FORMAT_ID,
    "assets": {
        "banner": {
            "asset_type": "image",
            "url": "https://cdn.example.com/a.png",
            "width": 300,
            "height": 250,
        }
    },
}


def _package(creative: dict) -> dict:
    return {
        "product_id": "product_1",
        "pricing_option_id": "test_pricing",
        "budget": 5000.0,
        "creatives": [creative],
    }


def _create_media_buy_payload(creative: dict) -> dict:
    return {
        "account": {"account_id": "acct_test"},
        "brand": {"domain": "flashsale.com"},
        "start_time": "asap",
        "end_time": datetime.now(UTC) + timedelta(days=30),
        "packages": [_package(creative)],
        "idempotency_key": "unit-test-key-package-creative-item-type",
    }


def _validation_error(model, payload) -> ValidationError | None:
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return exc
    return None


class TestPackageCreativeItemType:
    def test_create_media_buy_refuses_a_package_creative_lacking_asset_type(self):
        """@T-UC-002-inv-015-6: the buy is refused, INVALID_REQUEST, naming the slot."""
        exc = _validation_error(CreateMediaBuyRequest, _create_media_buy_payload(CREATIVE_WITH_UNDISCRIMINATED_ASSET))

        assert exc is not None, (
            "create_media_buy ACCEPTED a package creative whose assets value carries no "
            "asset_type discriminator; core/creative-asset.json requires one"
        )

        error = adcp_error_for(exc)
        assert error.error_code == ErrorCode.INVALID_REQUEST, (
            f"a schema-constraint violation must be INVALID_REQUEST, got {error.error_code}"
        )

        offending = [e for e in exc.errors() if "creatives" in e["loc"] and "banner" in e["loc"]]
        assert offending, (
            "the rejection must name the offending asset slot; "
            f"no error located it, locs were {[e['loc'] for e in exc.errors()]}"
        )

    def test_create_media_buy_still_accepts_a_discriminated_package_creative(self):
        """The refusal above must come from the discriminator, not from refusing everything."""
        req = CreateMediaBuyRequest.model_validate(_create_media_buy_payload(CREATIVE_WITH_DISCRIMINATED_ASSET))

        assert req.packages is not None and len(req.packages) == 1
        creatives = req.packages[0].creatives
        assert creatives is not None and len(creatives) == 1, "the inline creative was dropped"
        assert creatives[0].creative_id == "cr_ok"

    @pytest.mark.parametrize(
        "creative",
        [
            pytest.param(CREATIVE_WITH_UNDISCRIMINATED_ASSET, id="undiscriminated"),
            pytest.param(CREATIVE_WITH_DISCRIMINATED_ASSET, id="discriminated"),
        ],
    )
    def test_package_creatives_and_sync_creatives_judge_the_same_payload_alike(self, creative):
        """One item type, two tools.

        ``sync_creatives`` and ``create_media_buy`` both accept ``core/creative-asset.json``
        items. A payload one refuses and the other admits means the two tools are running
        different schemas, and the buyer's creative enters the system through whichever
        door is looser.
        """
        sync_error = _validation_error(CreativeAssetRequest, creative)
        package_error = _validation_error(PackageRequest, _package(creative))

        assert (sync_error is None) == (package_error is None), (
            "create_media_buy and sync_creatives disagree about the same creative: "
            f"sync_creatives {'refused' if sync_error else 'accepted'} it, "
            f"create_media_buy {'refused' if package_error else 'accepted'} it"
        )
