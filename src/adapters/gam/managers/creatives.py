"""
GAM Creatives Manager

Handles creative validation, creation, upload, and association with line items
for Google Ad Manager campaigns.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from src.core.errors.details import ErrorProblem
from src.core.exceptions import AdCPSalesAgentError, problems_refusal
from src.core.schemas import AssetStatus
from src.core.traffic_carrier import AdapterCreativeAsset, PackageAssignmentDict

from ..creative_plan import (
    GamCreativePlan,
    GamCreativeRefusal,
    GamRefusalReason,
    plan_or_refuse,
)
from ..utils.error_handler import map_gam_exception

logger = logging.getLogger(__name__)


def _line_item_weights(package_assignments: Sequence[PackageAssignmentDict]) -> list[tuple[str, int]]:
    """(GAM line item id, weight) for each of an asset's package assignments that has a line item."""
    return [
        (line_item_id, assignment.get("weight", 100))
        for line_item_id, assignment in _assigned_line_items(package_assignments)
    ]


def _assigned_line_items(
    package_assignments: Sequence[PackageAssignmentDict],
) -> list[tuple[str, PackageAssignmentDict]]:
    """(GAM line item id, assignment) for each of an asset's package assignments.

    The line item id is the seller's own package -> line item mapping: the adapter
    returns it from create_media_buy (``AdapterCreateResult.platform_line_item_ids``)
    and the tool persists it as ``package_config["platform_line_item_id"]``, which is
    where every caller reads it from. Nothing here derives it from a package id or a
    line item name -- names follow the tenant's naming template and package ids are
    opaque. An assignment without one names a package with no line item, and is skipped
    out loud.
    """
    result = []
    for assignment in package_assignments:
        line_item_id = assignment.get("platform_line_item_id")
        if line_item_id:
            result.append((str(line_item_id), assignment))
        else:
            logger.warning(f"Package {assignment.get('package_id')} has no GAM line item; assignment skipped")
    return result


def _placement_targeting_name(
    asset: Mapping[str, Any], placement_targeting_map: dict[str, str] | None
) -> tuple[str | None, str | None]:
    """(targetingName, the placement id it is for) of an asset's creative-level placement targeting (adcp#208)."""
    assignment_placement_ids = asset.get("placement_ids", [])
    if not (assignment_placement_ids and placement_targeting_map):
        return None, None
    # Use first placement_id - GAM LICA only supports one targetingName per association
    first_placement_id = assignment_placement_ids[0]
    if first_placement_id not in placement_targeting_map:
        return None, first_placement_id
    if len(assignment_placement_ids) > 1:
        logger.warning(
            f"Creative has {len(assignment_placement_ids)} placement_ids but GAM LICA "
            f"only supports one targetingName. Using first: {first_placement_id}"
        )
    return placement_targeting_map[first_placement_id], first_placement_id


class GAMCreativesManager:
    """Manages creative operations for Google Ad Manager."""

    def __init__(self, client_manager, advertiser_id: str):
        """Initialize creatives manager.

        Args:
            client_manager: GAMClientManager instance
            advertiser_id: GAM advertiser ID
        """
        self.client_manager = client_manager
        self.advertiser_id = advertiser_id

    def add_creative_assets(
        self,
        media_buy_id: str,
        assets: list[AdapterCreativeAsset],
        today: datetime,
        placement_targeting_map: dict[str, str] | None = None,
    ) -> list[AssetStatus]:
        """Creates new Creatives in GAM and associates them with LineItems.

        Args:
            media_buy_id: GAM order ID
            assets: List of creative asset dictionaries (each may contain placement_ids
                from creative assignments for placement targeting). An asset carrying
                ``platform_creative_id`` names a creative GAM already holds: it is
                associated with the line items it is not on yet, and not created again. If it also
                carries ``platform_creative_outdated``, GAM's copy is updated with its content first.
            today: Current datetime
            placement_targeting_map: Optional map of placement_id → targeting_name for
                creative-level targeting. Built from product impl_config.placement_targeting.
                When provided, creatives with placement_ids will have their LICAs created
                with the corresponding targetingName for GAM creative-level targeting.

        Returns:
            List of AssetStatus objects indicating success/failure for each creative
        """
        logger.info(f"Adding {len(assets)} creative assets for order '{media_buy_id}'")

        creative_service = self.client_manager.get_service("CreativeService")
        lica_service = self.client_manager.get_service("LineItemCreativeAssociationService")
        line_item_service = self.client_manager.get_service("LineItemService")

        created_asset_statuses = []

        # Seller-side concept enrichment (#1506). GAM has no first-class "creative
        # group", so we fall back to the GAM Order as the closest native grouping:
        # creatives trafficked into the same order run together as a campaign. This
        # is a fallback, NOT the authoritative buyer-side concept — the namespaced
        # id and the concept_source marker keep it distinguishable from a future
        # buyer-supplied concept (which must take precedence). See docs/adapters/.
        concept_id = f"gam-order-{media_buy_id}"
        concept_name = f"GAM Order {media_buy_id}"

        def _approved(creative_id: str, platform_creative_id: str | None = None) -> AssetStatus:
            return AssetStatus(
                creative_id=creative_id,
                platform_creative_id=platform_creative_id,
                status="approved",
                concept_id=concept_id,
                concept_name=concept_name,
                concept_source="gam_order",
            )

        # Creative placeholders of the order's line items, keyed by line item id
        try:
            creative_placeholders = self._get_line_item_placeholders(media_buy_id, line_item_service)
        except Exception as fault:
            raise map_gam_exception(fault) from fault

        # Check if any creatives have non-default weights
        # If so, update affected line items to use MANUAL rotation
        self._update_line_items_for_weighted_creatives(assets, line_item_service)

        for asset in assets:
            try:
                # A creative GAM already holds is placed on the line items, not created again. If
                # its content changed since, GAM's copy is updated first, keeping its id and its
                # associations. Nobody has tried updateCreatives against real GAM. Creative.size
                # is read-only after creation, so a changed size is not expected to take effect.
                # Each package's carrier on its own line item, so a creative on two packages
                # serves on each at the sizes that package's product accepts, never their union.
                plans = self._plans_by_line_item(asset, creative_placeholders)
                plan = next(iter(plans.values()))
                gam_creative_id = asset.get("platform_creative_id")
                if not gam_creative_id or asset.get("platform_creative_outdated"):
                    creative = self._planned_creative(asset, plan)
                    if gam_creative_id:
                        creative_service.updateCreatives([{**creative, "id": int(gam_creative_id)}])
                        logger.info(f"✓ Updated GAM Creative ID: {gam_creative_id}")
                    else:
                        logger.info(f"Creating {creative.get('xsi_type')} for creative {asset['creative_id']}")
                        created_creatives = creative_service.createCreatives([creative])
                        if not created_creatives:
                            logger.error(f"Failed to create creative {asset['creative_id']} - no creatives returned")
                            created_asset_statuses.append(
                                AssetStatus(creative_id=asset["creative_id"], status="failed")
                            )
                            continue

                        gam_creative_id = created_creatives[0]["id"]
                        logger.info(f"✓ Created GAM Creative ID: {gam_creative_id}")

                # Associate creative with line items (includes placement targeting if configured)
                self._associate_creative_with_line_items(
                    gam_creative_id,
                    asset,
                    lica_service,
                    placement_targeting_map,
                    # A creative GAM held before this push may have been created at another
                    # package's size, so every association states its own.
                    lica_sizes=self._lica_sizes_by_line_item(
                        plans, None if asset.get("platform_creative_id") else plan.creative["size"]
                    ),
                )
                # Content that changed was held from serving while it was under review
                # (``set_creative_serving``); its approval is what this push carries.
                if asset.get("platform_creative_outdated"):
                    self.set_creative_serving(str(gam_creative_id), serving=True)

                created_asset_statuses.append(_approved(asset["creative_id"], str(gam_creative_id)))

            except AdCPSalesAgentError as e:
                # Typed rejection (e.g. CREATIVE_REJECTED): this surface reports
                # per-asset partial success, so the buyer-correctable reason
                # must ride the status — a bare "failed" is unactionable.
                logger.error(
                    "Creative %s rejected: %s field=%s details=%s",
                    asset["creative_id"],
                    e.message,
                    e.field,
                    e.details or {},
                    exc_info=True,
                )
                # This surface reports per-asset partial success, and the error's own slot
                # is off the wire, so the offending FIELD rides the status line — read from
                # the typed ``field`` attribute, never by stringifying ``details``: that
                # would put every key on the wire, including ones named after local
                # variables, and hand-roll a serializer in the adapter layer.
                created_asset_statuses.append(
                    AssetStatus(
                        creative_id=asset["creative_id"],
                        status="failed",
                        message=f"{e.message} (field: {e.field})" if e.field else e.message,
                        error=e,
                    )
                )
            except Exception as e:
                logger.error(f"Error creating creative {asset['creative_id']}: {str(e)}")
                created_asset_statuses.append(
                    AssetStatus(
                        creative_id=asset["creative_id"], status="failed", message=str(e), error=map_gam_exception(e)
                    )
                )

        return created_asset_statuses

    @staticmethod
    def _plans_by_line_item(
        asset: AdapterCreativeAsset, creative_placeholders: Mapping[str, list]
    ) -> dict[str, GamCreativePlan]:
        """Per line item *asset* is assigned to, its package's carrier planned on that line item; raises the refusal.

        Each line item's own placeholders, and the carrier core read for that package's
        product, so nothing is planned against another package's sizes. The planning call is
        the pre-booking check's (``plan_or_refuse``); every line item's refusal rides one
        error, which core builds (``problems_refusal``): this manager picks no error class.
        """
        plans = {
            line_item_id: plan_or_refuse(
                assignment["creative"], creative_placeholders.get(line_item_id, []), assignment["package_id"]
            )
            for line_item_id, assignment in _assigned_line_items(asset["package_assignments"])
        }
        if not plans:
            # No assigned package has a line item, so no line item states a size.
            raise problems_refusal(
                [GamCreativeRefusal(asset["creative_id"], GamRefusalReason.NO_PLACEHOLDER).problem()]
            )
        refused = [plan for plan in plans.values() if isinstance(plan, ErrorProblem)]
        if refused:
            raise problems_refusal(refused)
        return {line_item_id: plan for line_item_id, plan in plans.items() if isinstance(plan, GamCreativePlan)}

    def _planned_creative(self, asset: AdapterCreativeAsset, plan: GamCreativePlan) -> dict[str, Any]:
        """The planned GAM creative, with the advertiser and name this manager owns."""
        return {
            **plan.creative,
            "advertiserId": self.advertiser_id,
            "name": asset.get("name", f"AdCP Creative {asset['creative_id']}"),
        }

    @staticmethod
    def _lica_sizes_by_line_item(
        plans: Mapping[str, GamCreativePlan], creative_size: dict[str, int] | None
    ) -> dict[str, list[dict[str, int]]]:
        """Per line item, the sizes its own plan serves the creative at.

        A line item left with only the creative's own size states none, as the plan does for a
        creative with one candidate size. *creative_size* ``None`` is a size this push does not
        know, so every line item states its sizes: they "override the value set for
        Creative.size" (Ad Manager API v202602), whatever size GAM created the creative at.
        """
        by_line_item = {}
        for line_item_id, plan in plans.items():
            sizes = list(plan.lica_sizes) or [plan.creative["size"]]
            by_line_item[line_item_id] = [] if sizes == [creative_size] else sizes
        return by_line_item

    def set_creative_serving(self, gam_creative_id: str, *, serving: bool) -> None:
        """Activate or deactivate every line item creative association of a GAM creative.

        GAM API LineItemCreativeAssociation.Status: ACTIVE "the associated Creative can be
        served"; INACTIVE "the associated Creative is ineligible for being served".
        LineItemCreativeAssociationService.performLineItemCreativeAssociationAction applies
        ActivateLineItemCreativeAssociations or DeactivateLineItemCreativeAssociations to the
        associations its statement matches, here every one naming the creative. Nobody has
        tried this against real GAM. Ceiling: one page of the statement builder's default
        limit (500) associations.
        """
        lica_service = self.client_manager.get_service("LineItemCreativeAssociationService")
        statement = (
            self.client_manager.get_statement_builder()
            .Where("creativeId = :creativeId")
            .WithBindVariable("creativeId", int(gam_creative_id))
        )
        action = "ActivateLineItemCreativeAssociations" if serving else "DeactivateLineItemCreativeAssociations"
        lica_service.performLineItemCreativeAssociationAction({"xsi_type": action}, statement.ToStatement())
        logger.info(f"{action} for GAM creative {gam_creative_id}")

    def _get_line_item_placeholders(self, media_buy_id: str, line_item_service) -> dict[str, list]:
        """The creative placeholders of each line item in an order, keyed by line item id.

        Args:
            media_buy_id: GAM order ID
            line_item_service: GAM LineItemService

        Returns:
            ``{line_item_id: creativePlaceholders}``
        """
        statement = (
            self.client_manager.get_statement_builder()
            .Where("orderId = :orderId")
            .WithBindVariable("orderId", int(media_buy_id))
        )
        response = line_item_service.getLineItemsByStatement(statement.ToStatement())
        # GAM API returns a LineItemPage object (Zeep SOAP), not a dict
        line_items = getattr(response, "results", [])
        return {str(item["id"]): getattr(item, "creativePlaceholders", []) for item in line_items}

    def _update_line_items_for_weighted_creatives(
        self, assets: Sequence[AdapterCreativeAsset], line_item_service
    ) -> None:
        """Update line items to use MANUAL rotation if creatives have non-default weights.

        The spec supports creative rotation weights. When weights differ from the default (100),
        GAM requires MANUAL rotation type on the line item to respect the weights.

        Args:
            assets: List of creative assets with package_assignments containing weights
            line_item_service: GAM LineItemService
        """
        # Collect all weights per line item to determine if MANUAL rotation is needed
        line_item_weights: dict[str, list[int]] = {}

        for asset in assets:
            for line_item_id, weight in _line_item_weights(asset.get("package_assignments", [])):
                line_item_weights.setdefault(line_item_id, []).append(weight)

        # Determine which line items need MANUAL rotation
        # MANUAL is required when any creative has a non-default weight (not 100)
        # This covers both cases: varying weights AND uniform non-default weights
        line_items_needing_manual = []
        for line_item_id, weights in line_item_weights.items():
            if any(w != 100 for w in weights):
                line_items_needing_manual.append(line_item_id)
                logger.info(f"Line item {line_item_id} has non-default weights {weights} - will use MANUAL rotation")

        if not line_items_needing_manual:
            logger.info("All creatives have default weights - keeping EVEN rotation")
            return

        if not line_item_service:
            logger.warning("No line item service available - cannot update rotation type")
            return

        # Fetch and update line items
        for line_item_id in line_items_needing_manual:
            try:
                # Get the line item
                statement = (
                    self.client_manager.get_statement_builder()
                    .Where("id = :id")
                    .WithBindVariable("id", int(line_item_id))
                )
                response = line_item_service.getLineItemsByStatement(statement.ToStatement())
                line_items = getattr(response, "results", [])

                if not line_items:
                    logger.warning(f"Line item {line_item_id} not found for rotation update")
                    continue

                line_item = line_items[0]
                # GAM Zeep objects support dict-style access for both read and write
                current_rotation = line_item["creativeRotationType"] if "creativeRotationType" in line_item else "EVEN"

                if current_rotation != "MANUAL":
                    # Update to MANUAL rotation
                    line_item["creativeRotationType"] = "MANUAL"
                    line_item_service.updateLineItems([line_item])
                    logger.info(f"Updated line item {line_item_id} from {current_rotation} to MANUAL rotation")
                else:
                    logger.info(f"Line item {line_item_id} already uses MANUAL rotation")

            except Exception as e:
                # Log full traceback for debugging, but don't fail the whole operation
                # Weights will still be set on LICAs even if rotation type update fails
                logger.error(f"Failed to update rotation type for line item {line_item_id}: {e}", exc_info=True)

    def _associate_creative_with_line_items(
        self,
        gam_creative_id: str,
        asset: AdapterCreativeAsset,
        lica_service,
        placement_targeting_map: dict[str, str] | None = None,
        lica_sizes: dict[str, list[dict[str, int]]] | None = None,
    ) -> None:
        """Associate creative with those of its assigned line items it is not on yet.

        Supports creative rotation weights. When weights differ from the default (100),
        the weight is passed to GAM's manualCreativeRotationWeight field for MANUAL rotation.

        Supports creative-level placement targeting (adcp#208). When placement_ids are specified
        in the creative assignment and a placement_targeting_map is provided, the LICA is created
        with a targetingName that links to the line item's creativeTargetings rule.

        Args:
            gam_creative_id: The GAM creative ID to associate
            asset: Creative asset dictionary (contains package_assignments, placement_ids)
            lica_service: GAM LICA service
            placement_targeting_map: Optional map of placement_id → targeting_name for
                creative-level targeting. Built from product impl_config.placement_targeting.
            lica_sizes: Optional line item id → the association's ``sizes``, which
                "Overrides the value set for Creative.size" (Ad Manager API v202602). A line
                item with none states none.
        """
        # A creative GAM held before this call may already be on some of these line items:
        # GAM is asked, and only the missing associations are created. The statement names
        # the creative and the line item is matched here, as strings, because GAM ids arrive
        # as numbers or strings. Ceiling: one page, the statement builder's default limit
        # (500). An association past it is not seen, its create is sent again, and the asset
        # comes back failed; a creative on more line items than that needs paging. An
        # inactive association counts as held.
        held: set[tuple[str, str]] = set()
        if asset.get("platform_creative_id"):
            statement = (
                self.client_manager.get_statement_builder()
                .Where("creativeId = :creativeId")
                .WithBindVariable("creativeId", int(gam_creative_id))
            )
            page = lica_service.getLineItemCreativeAssociationsByStatement(statement.ToStatement())
            held = {(str(a["creativeId"]), str(a["lineItemId"])) for a in getattr(page, "results", None) or []}

        for line_item_id, weight in _line_item_weights(asset.get("package_assignments", [])):
            if (str(gam_creative_id), line_item_id) in held:
                logger.info(f"Creative {gam_creative_id} is already on line item {line_item_id}")
                continue

            targeting_name, first_placement_id = _placement_targeting_name(asset, placement_targeting_map)

            # Create Line Item Creative Association (weight support + adcp#208 placement targeting)
            association: dict[str, Any] = {
                "creativeId": gam_creative_id,
                "lineItemId": line_item_id,
            }
            if sizes := (lica_sizes or {}).get(line_item_id):
                association["sizes"] = sizes

            # Add weight for manual rotation if not default
            # GAM uses manualCreativeRotationWeight for MANUAL rotation type
            if weight != 100:
                association["manualCreativeRotationWeight"] = weight
                logger.info(f"Setting creative weight to {weight} for LICA")

            # Add targetingName for creative-level placement targeting (adcp#208)
            # This links the LICA to a creativeTargetings rule defined on the line item
            if targeting_name:
                association["targetingName"] = targeting_name
                logger.info(f"Setting targetingName '{targeting_name}' for LICA (placement: {first_placement_id})")

            try:
                lica_service.createLineItemCreativeAssociations([association])
                weight_info = f" (weight: {weight})" if weight != 100 else ""
                targeting_info = f" (targetingName: {targeting_name})" if targeting_name else ""
                logger.info(
                    f"✓ Associated creative {gam_creative_id} with line item {line_item_id}{weight_info}{targeting_info}"
                )
            except Exception as e:
                logger.error(f"Failed to associate creative {gam_creative_id} with line item {line_item_id}: {e}")
                raise
