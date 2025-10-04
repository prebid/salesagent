import random
from datetime import UTC, datetime, timedelta
from typing import Any

from src.adapters.base import AdServerAdapter
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    DeliveryTotals,
    MediaPackage,
    PackagePerformance,
    ReportingPeriod,
    UpdateMediaBuyResponse,
)


class MockAdServer(AdServerAdapter):
    """
    A mock ad server that simulates the lifecycle of a media buy.
    It conforms to the AdServerAdapter interface.
    """

    adapter_name = "mock"
    _media_buys: dict[str, dict[str, Any]] = {}

    # Supported targeting dimensions (mock supports everything)
    SUPPORTED_DEVICE_TYPES = {"mobile", "desktop", "tablet", "ctv", "dooh", "audio"}
    SUPPORTED_MEDIA_TYPES = {"video", "display", "native", "audio", "dooh"}

    def __init__(self, config, principal, dry_run=False, creative_engine=None, tenant_id=None, strategy_context=None):
        """Initialize mock adapter with GAM-like objects."""
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # Store strategy context for simulation behavior
        self.strategy_context = strategy_context
        self._current_simulation_time = None

        # Initialize HITL configuration from principal's platform_mappings
        self._initialize_hitl_config()

        # Initialize GAM-like object hierarchy for this instance
        self._initialize_mock_objects()

    def _is_simulation(self) -> bool:
        """Check if we're running in simulation mode."""
        return (
            self.strategy_context
            and hasattr(self.strategy_context, "is_simulation")
            and hasattr(self.strategy_context, "strategy_id")
            and self.strategy_context.is_simulation
            and self.strategy_context.strategy_id.startswith("sim_")
        )

    def _should_force_error(self, error_type: str) -> bool:
        """Check if strategy should force a specific error."""
        if not self._is_simulation() or not self.strategy_context:
            return False
        if hasattr(self.strategy_context, "should_force_error"):
            return self.strategy_context.should_force_error(error_type)
        return False

    def _get_simulation_scenario(self) -> str:
        """Get current simulation scenario."""
        if not self._is_simulation() or not self.strategy_context:
            return "normal"
        if hasattr(self.strategy_context, "get_config_value"):
            return self.strategy_context.get_config_value("scenario", "normal")
        return "normal"

    def _apply_strategy_multipliers(self, base_value: float, multiplier_key: str) -> float:
        """Apply strategy-based multipliers to base values."""
        if not self.strategy_context:
            return base_value

        if hasattr(self.strategy_context, "get_config_value"):
            multiplier = self.strategy_context.get_config_value(multiplier_key, 1.0)
            return base_value * multiplier
        return base_value

    def _simulate_time_progression(self) -> datetime:
        """Get current time for simulation (real or simulated)."""
        if self._is_simulation() and self._current_simulation_time:
            return self._current_simulation_time
        return datetime.now(UTC)

    def set_simulation_time(self, simulation_time: datetime):
        """Set the current simulation time."""
        self._current_simulation_time = simulation_time

    def _initialize_hitl_config(self):
        """Initialize Human-in-the-Loop configuration from principal platform_mappings."""
        # Extract HITL config from principal's mock platform mapping
        mock_mapping = self.principal.platform_mappings.get("mock", {})
        self.hitl_config = mock_mapping.get("hitl_config", {})

        # Parse HITL settings with defaults
        self.hitl_enabled = self.hitl_config.get("enabled", False)
        self.hitl_mode = self.hitl_config.get("mode", "sync")  # "sync" | "async" | "mixed"

        # Sync mode settings
        sync_settings = self.hitl_config.get("sync_settings", {})
        self.sync_delay_ms = sync_settings.get("delay_ms", 2000)
        self.streaming_updates = sync_settings.get("streaming_updates", True)
        self.update_interval_ms = sync_settings.get("update_interval_ms", 500)

        # Async mode settings
        async_settings = self.hitl_config.get("async_settings", {})
        self.async_auto_complete = async_settings.get("auto_complete", False)
        self.async_auto_complete_delay_ms = async_settings.get("auto_complete_delay_ms", 10000)
        self.async_webhook_url = async_settings.get("webhook_url")
        self.webhook_on_complete = async_settings.get("webhook_on_complete", True)

        # Per-operation mode overrides
        self.operation_modes = self.hitl_config.get("operation_modes", {})

        # Approval simulation settings
        approval_sim = self.hitl_config.get("approval_simulation", {})
        self.approval_simulation_enabled = approval_sim.get("enabled", False)
        self.approval_probability = approval_sim.get("approval_probability", 0.8)
        self.rejection_reasons = approval_sim.get(
            "rejection_reasons",
            [
                "Budget exceeds limits",
                "Invalid targeting parameters",
                "Creative policy violation",
                "Inventory unavailable",
            ],
        )

        if self.hitl_enabled:
            self.log(f"🤖 HITL mode enabled: {self.hitl_mode}")
            if self.hitl_mode == "mixed":
                self.log(f"   Operation overrides: {self.operation_modes}")

    def _initialize_mock_objects(self):
        """Create realistic GAM-like objects for testing."""
        # Ad unit hierarchy (like GAM's inventory structure)
        self.ad_units = {
            "root": {
                "id": "1001",
                "name": "Publisher Network",
                "path": "/",
                "children": ["homepage", "sports", "news", "entertainment"],
            },
            "homepage": {
                "id": "2001",
                "name": "Homepage",
                "path": "/homepage",
                "parent": "root",
                "children": ["homepage_top", "homepage_sidebar", "homepage_footer"],
            },
            "homepage_top": {
                "id": "2101",
                "name": "Homepage - Top Banner",
                "path": "/homepage/top",
                "parent": "homepage",
                "sizes": ["728x90", "970x250", "320x50"],
            },
            "homepage_sidebar": {
                "id": "2102",
                "name": "Homepage - Sidebar",
                "path": "/homepage/sidebar",
                "parent": "homepage",
                "sizes": ["300x250", "300x600"],
            },
            "sports": {
                "id": "3001",
                "name": "Sports Section",
                "path": "/sports",
                "parent": "root",
                "children": ["sports_article", "sports_scores"],
            },
            "news": {
                "id": "4001",
                "name": "News Section",
                "path": "/news",
                "parent": "root",
                "children": ["news_article", "news_breaking"],
            },
        }

        # Custom targeting keys (like GAM's key-value targeting)
        self.targeting_keys = {
            "content_category": {
                "id": "key_1",
                "name": "content_category",
                "values": ["sports", "news", "entertainment", "business", "technology"],
            },
            "article_type": {
                "id": "key_2",
                "name": "article_type",
                "values": ["breaking", "feature", "opinion", "analysis", "review"],
            },
            "user_segment": {
                "id": "key_3",
                "name": "user_segment",
                "values": ["premium", "registered", "anonymous", "subscriber"],
            },
            "page_position": {
                "id": "key_4",
                "name": "page_position",
                "values": ["above_fold", "below_fold", "sticky", "interstitial"],
            },
            "aee_audience": {
                "id": "key_5",
                "name": "aee_audience",
                "values": [
                    "auto_intenders",
                    "luxury_travel",
                    "sports_enthusiasts",
                    "tech_buyers",
                ],
            },
        }

        # Predefined line item templates (for common product types)
        self.line_item_templates = {
            "standard_display": {
                "type": "STANDARD",
                "priority": 8,
                "creative_sizes": ["300x250", "728x90"],
                "targeting": {
                    "ad_units": ["homepage", "news", "sports"],
                    "device_categories": ["DESKTOP", "TABLET"],
                },
            },
            "mobile_app": {
                "type": "STANDARD",
                "priority": 8,
                "creative_sizes": ["320x50", "300x250"],
                "targeting": {
                    "device_categories": ["MOBILE"],
                    "operating_systems": ["IOS", "ANDROID"],
                },
            },
            "video_preroll": {
                "type": "STANDARD",
                "priority": 6,
                "creative_sizes": ["VIDEO"],
                "targeting": {
                    "ad_units": ["video_player"],
                    "content_category": ["sports", "entertainment"],
                },
            },
            "programmatic_guaranteed": {
                "type": "SPONSORSHIP",
                "priority": 4,
                "creative_sizes": ["300x250", "728x90", "970x250"],
                "targeting": {
                    "ad_units": ["homepage_top"],
                    "user_segment": ["premium", "subscriber"],
                },
            },
        }

        # Creative placeholders
        self.creative_library = {
            "300x250": {
                "id": "creative_1",
                "name": "Standard Medium Rectangle",
                "size": "300x250",
                "type": "IMAGE",
            },
            "728x90": {
                "id": "creative_2",
                "name": "Leaderboard",
                "size": "728x90",
                "type": "IMAGE",
            },
            "VIDEO": {
                "id": "creative_3",
                "name": "Video Creative",
                "size": "VIDEO",
                "type": "VIDEO",
                "duration": 30,
            },
        }

        self.log("Mock ad server initialized with GAM-like object hierarchy")
        self.log(f"  - {len(self.ad_units)} ad units in hierarchy")
        self.log(f"  - {len(self.targeting_keys)} custom targeting keys")
        self.log(f"  - {len(self.line_item_templates)} line item templates")
        self.log(f"  - {len(self.creative_library)} creative templates")

    def _validate_targeting(self, targeting_overlay):
        """Mock adapter accepts all targeting."""
        return []  # No unsupported features

    def _get_operation_mode(self, operation_name: str) -> str:
        """Get the HITL mode for a specific operation."""
        if not self.hitl_enabled:
            return "immediate"

        # Check for operation-specific override
        if operation_name in self.operation_modes:
            return self.operation_modes[operation_name]

        # Use global mode
        return self.hitl_mode

    def _create_workflow_step(self, step_type: str, status: str, request_data: dict) -> dict:
        """Create a workflow step for async HITL operations."""
        from src.core.config_loader import get_current_tenant
        from src.core.context_manager import get_context_manager

        # Get context manager and tenant info
        ctx_manager = get_context_manager()
        tenant = get_current_tenant()

        # Create a context for async operations if needed
        context = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=self.principal.principal_id)

        # Create workflow step
        step = ctx_manager.create_workflow_step(
            context_id=context.context_id,
            step_type=step_type,
            tool_name=step_type.replace("mock_", ""),
            request_data=request_data,
            status=status,
            owner="mock_adapter",
        )

        return step

    def _stream_working_updates(self, operation_name: str, delay_ms: int):
        """Stream progress updates during synchronous HITL operation."""
        if not self.streaming_updates:
            return

        import time

        num_updates = max(1, delay_ms // self.update_interval_ms)

        for i in range(num_updates):
            progress = (i + 1) / num_updates * 100
            self.log(f"⏳ Processing {operation_name}... {progress:.0f}%")

            # Only sleep if not the last update
            if i < num_updates - 1:
                time.sleep(self.update_interval_ms / 1000)

    def _simulate_approval(self) -> tuple[bool, str | None]:
        """Simulate approval/rejection process."""
        if not self.approval_simulation_enabled:
            return True, None

        import random

        # Simulate approval probability
        approved = random.random() < self.approval_probability

        if approved:
            return True, None
        else:
            # Pick a random rejection reason
            reason = random.choice(self.rejection_reasons)
            return False, reason

    def _schedule_async_completion(self, step_id: str, delay_ms: int):
        """Schedule automatic completion of an async task (for testing)."""
        if not self.async_auto_complete:
            return

        # This is a simulation - in a real system this would use a proper
        # job queue like Celery, RQ, or similar
        import threading
        import time

        def complete_after_delay():
            time.sleep(delay_ms / 1000)

            try:
                from src.core.context_manager import get_context_manager

                ctx_manager = get_context_manager()

                # Simulate approval process
                approved, rejection_reason = self._simulate_approval()

                if approved:
                    ctx_manager.update_workflow_step(
                        step_id, status="completed", response_data={"status": "approved", "auto_completed": True}
                    )
                    self.log(f"✅ Auto-completed task {step_id}")
                else:
                    ctx_manager.update_workflow_step(
                        step_id, status="failed", error=f"Auto-rejected: {rejection_reason}"
                    )
                    self.log(f"❌ Auto-rejected task {step_id}: {rejection_reason}")

                # Send webhook if configured
                if self.webhook_on_complete and self.async_webhook_url:
                    self._send_completion_webhook(step_id, approved, rejection_reason)

            except Exception as e:
                self.log(f"⚠️ Error in async completion for {step_id}: {e}")

        # Start background thread for auto-completion
        thread = threading.Thread(target=complete_after_delay)
        thread.daemon = True
        thread.start()

    def _send_completion_webhook(self, step_id: str, approved: bool, rejection_reason: str = None):
        """Send webhook notification when async task completes."""
        if not self.async_webhook_url:
            return

        from datetime import UTC, datetime

        import requests

        payload = {
            "event": "task_completed",
            "step_id": step_id,
            "principal_id": self.principal.principal_id,
            "status": "completed" if approved else "failed",
            "approved": approved,
            "rejection_reason": rejection_reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            response = requests.post(
                self.async_webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10
            )
            response.raise_for_status()
            self.log(f"📤 Sent webhook notification for {step_id}")
        except Exception as e:
            self.log(f"⚠️ Webhook failed for {step_id}: {e}")

    def _validate_media_buy_request(
        self, request: CreateMediaBuyRequest, packages: list[MediaPackage], start_time: datetime, end_time: datetime
    ):
        """Validate media buy request with GAM-like validation rules."""
        errors = []

        # Date validation (like GAM)
        if start_time >= end_time:
            errors.append("NotNullError.NULL @ lineItem[0].endDateTime")

        # Ensure consistent timezone handling for date comparison
        current_time = datetime.now(UTC) if end_time.tzinfo else datetime.now()
        if end_time <= current_time:
            errors.append("InvalidArgumentError @ lineItem[0].endDateTime")

        # Inventory targeting validation (like GAM requirement)
        has_inventory_targeting = False
        if request.targeting_overlay and hasattr(request.targeting_overlay, "custom"):
            if request.targeting_overlay.custom and "inventory" in str(request.targeting_overlay.custom):
                has_inventory_targeting = True

        # For non-guaranteed line items, require some form of inventory targeting
        for package in packages:
            if package.delivery_type == "non_guaranteed":
                if not has_inventory_targeting:
                    errors.append("RequiredError.REQUIRED @ lineItem[0].targeting.inventoryTargeting")
                    break

        # Goal validation (like GAM limits)
        for package in packages:
            if package.impressions > 1000000:  # Mock limit
                errors.append(
                    f"ReservationDetailsError.PERCENTAGE_UNITS_BOUGHT_TOO_HIGH @ lineItem[0].primaryGoal.units; trigger:'{package.impressions}'"
                )

        # Budget validation
        if request.budget and request.budget.total <= 0:
            errors.append("InvalidArgumentError @ order.totalBudget")

        if request.budget and request.budget.total > 1000000:  # Mock limit
            errors.append("InvalidArgumentError.VALUE_TOO_LARGE @ order.totalBudget")

        # If we have errors, format them like GAM does
        if errors:
            error_message = "[" + ", ".join(errors) + "]"
            raise Exception(error_message)

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
    ) -> CreateMediaBuyResponse:
        """Simulates the creation of a media buy using GAM-like templates."""
        # NO QUIET FAILURES policy - Check for unsupported targeting
        if request.targeting_overlay:
            # Mock adapter mirrors GAM behavior - these targeting types are not supported
            if request.targeting_overlay.device_type_any_of:
                raise ValueError(
                    f"Device targeting requested but not supported. "
                    f"Cannot fulfill buyer contract for device types: {request.targeting_overlay.device_type_any_of}."
                )

            if request.targeting_overlay.os_any_of:
                raise ValueError(
                    f"OS targeting requested but not supported. "
                    f"Cannot fulfill buyer contract for OS types: {request.targeting_overlay.os_any_of}."
                )

            if request.targeting_overlay.browser_any_of:
                raise ValueError(
                    f"Browser targeting requested but not supported. "
                    f"Cannot fulfill buyer contract for browsers: {request.targeting_overlay.browser_any_of}."
                )

            if request.targeting_overlay.content_cat_any_of:
                raise ValueError(
                    f"Content category targeting requested but not supported. "
                    f"Cannot fulfill buyer contract for categories: {request.targeting_overlay.content_cat_any_of}."
                )

            if request.targeting_overlay.keywords_any_of:
                raise ValueError(
                    f"Keyword targeting requested but not supported. "
                    f"Cannot fulfill buyer contract for keywords: {request.targeting_overlay.keywords_any_of}."
                )

        # GAM-like validation (based on real GAM behavior)
        self._validate_media_buy_request(request, packages, start_time, end_time)

        # HITL Mode Processing
        operation_mode = self._get_operation_mode("create_media_buy")

        if operation_mode == "async":
            return self._create_media_buy_async(request, packages, start_time, end_time)
        elif operation_mode == "sync":
            return self._create_media_buy_sync_with_delay(request, packages, start_time, end_time)

        # Continue with immediate processing (default behavior)
        return self._create_media_buy_immediate(request, packages, start_time, end_time)

    def _create_media_buy_async(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
    ) -> CreateMediaBuyResponse:
        """Create media buy in async HITL mode."""
        self.log("🤖 Processing create_media_buy in ASYNC mode")

        # Create workflow step for async tracking
        request_data = {
            "request": request.model_dump(),
            "packages": [p.model_dump() for p in packages],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "operation": "create_media_buy",
        }

        step = self._create_workflow_step(
            step_type="mock_create_media_buy", status="pending", request_data=request_data
        )

        self.log(f"   Created workflow step: {step.step_id}")

        # Schedule auto-completion if configured
        if self.async_auto_complete:
            self.log(f"   Auto-completion scheduled in {self.async_auto_complete_delay_ms}ms")
            self._schedule_async_completion(step.step_id, self.async_auto_complete_delay_ms)
        else:
            self.log("   Manual completion required - use complete_task tool")

        # Return pending response
        return CreateMediaBuyResponse(
            media_buy_id=f"pending_{step.step_id}",
            status="submitted",
            message=f"Media buy submitted for processing. Task ID: {step.step_id}",
            creative_deadline=None,
        )

    def _create_media_buy_sync_with_delay(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
    ) -> CreateMediaBuyResponse:
        """Create media buy in sync HITL mode with configurable delay."""
        self.log(f"🤖 Processing create_media_buy in SYNC mode ({self.sync_delay_ms}ms delay)")

        # Stream working updates during delay
        self._stream_working_updates("create_media_buy", self.sync_delay_ms)

        # Final delay to reach total configured delay
        import time

        if self.streaming_updates:
            remaining_delay = (
                self.sync_delay_ms - (self.sync_delay_ms // self.update_interval_ms) * self.update_interval_ms
            )
            if remaining_delay > 0:
                time.sleep(remaining_delay / 1000)
        else:
            time.sleep(self.sync_delay_ms / 1000)

        # Simulate approval if configured
        approved, rejection_reason = self._simulate_approval()
        if not approved:
            self.log(f"❌ Simulated rejection: {rejection_reason}")
            raise Exception(f"Media buy rejected: {rejection_reason}")

        # Continue with immediate processing
        self.log("✅ SYNC delay completed, proceeding with creation")
        return self._create_media_buy_immediate(request, packages, start_time, end_time)

    def _create_media_buy_immediate(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
    ) -> CreateMediaBuyResponse:
        """Create media buy immediately (original behavior)."""

        # Generate a unique media_buy_id
        import uuid

        media_buy_id = f"buy_{request.po_number}" if request.po_number else f"buy_{uuid.uuid4().hex[:8]}"

        # Strategy-aware behavior modifications
        if self._is_simulation():
            strategy_id = getattr(self.strategy_context, "strategy_id", "unknown")
            self.log(f"🧪 Running in simulation mode with strategy: {strategy_id}")
            scenario = self._get_simulation_scenario()
            self.log(f"   Simulation scenario: {scenario}")

            # Check for forced errors
            if self._should_force_error("budget_exceeded"):
                raise Exception("Simulated error: Campaign budget exceeds available funds")

            if self._should_force_error("targeting_invalid"):
                raise Exception("Simulated error: Invalid targeting parameters")

            if self._should_force_error("inventory_unavailable"):
                raise Exception("Simulated error: Requested inventory not available")

        # Select appropriate template based on packages
        template_name = "standard_display"  # Default
        if any(p.name and "video" in p.name.lower() for p in packages):
            template_name = "video_preroll"
        elif any(p.name and "mobile" in p.name.lower() for p in packages):
            template_name = "mobile_app"
        elif any(p.delivery_type == "guaranteed" for p in packages):
            template_name = "programmatic_guaranteed"

        template = self.line_item_templates.get(template_name, self.line_item_templates["standard_display"])

        # Log operation start
        self.audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=self.adapter_principal_id,
            success=True,
            details={
                "media_buy_id": media_buy_id,
                "po_number": request.po_number,
                "flight_dates": f"{start_time.date()} to {end_time.date()}",
            },
        )

        # Calculate total budget from packages (CPM * impressions / 1000)
        total_budget = sum((p.cpm * p.impressions / 1000) for p in packages if p.delivery_type == "guaranteed")
        # Use the request's budget if available, otherwise use calculated
        total_budget = request.get_total_budget() if hasattr(request, "get_total_budget") else total_budget

        # Apply strategy-based bid adjustment
        if self.strategy_context and hasattr(self.strategy_context, "get_bid_adjustment"):
            bid_adjustment = self.strategy_context.get_bid_adjustment()
            if bid_adjustment != 1.0:
                adjusted_budget = total_budget * bid_adjustment
                self.log(
                    f"📈 Strategy bid adjustment: {bid_adjustment:.2f} (${total_budget:,.2f} → ${adjusted_budget:,.2f})"
                )
                total_budget = adjusted_budget

        self.log(f"Creating media buy with ID: {media_buy_id}")
        self.log(f"Using template: {template_name} (priority: {template['priority']})")
        self.log(f"Budget: ${total_budget:,.2f}")
        self.log(f"Flight dates: {start_time.date()} to {end_time.date()}")

        # Simulate API call details
        if self.dry_run:
            self.log("Would call: MockAdServer.createCampaign()")
            self.log("  API Request: {")
            self.log(f"    'advertiser_id': '{self.adapter_principal_id}',")
            self.log(f"    'campaign_name': 'AdCP Campaign {media_buy_id}',")
            self.log(f"    'budget': {total_budget},")
            self.log(f"    'start_date': '{start_time.isoformat()}',")
            self.log(f"    'end_date': '{end_time.isoformat()}',")
            self.log("    'targeting': {")
            if request.targeting_overlay:
                if request.targeting_overlay.geo_country_any_of:
                    self.log(f"      'countries': {request.targeting_overlay.geo_country_any_of},")
                if request.targeting_overlay.geo_region_any_of:
                    self.log(f"      'regions': {request.targeting_overlay.geo_region_any_of},")
                if request.targeting_overlay.geo_metro_any_of:
                    self.log(f"      'metros': {request.targeting_overlay.geo_metro_any_of},")
                if request.targeting_overlay.key_value_pairs:
                    self.log(f"      'key_values': {request.targeting_overlay.key_value_pairs},")
                if request.targeting_overlay.media_type_any_of:
                    self.log(f"      'media_types': {request.targeting_overlay.media_type_any_of},")
            self.log("    }")
            self.log("  }")

        if not self.dry_run:
            self._media_buys[media_buy_id] = {
                "id": media_buy_id,
                "po_number": request.po_number,
                "buyer_ref": request.buyer_ref,
                "packages": [p.model_dump() for p in packages],
                "total_budget": total_budget,
                "start_time": start_time,
                "end_time": end_time,
                "creatives": [],
            }
            self.log("✓ Media buy created successfully")
            self.log(f"  Campaign ID: {media_buy_id}")
            # Log successful creation
            self.audit_logger.log_success(f"Created Mock Order ID: {media_buy_id}")
        else:
            self.log(f"Would return: Campaign ID '{media_buy_id}' with status 'pending_creative'")

        return CreateMediaBuyResponse(
            media_buy_id=media_buy_id,
            buyer_ref=request.buyer_ref,
            creative_deadline=datetime.now(UTC) + timedelta(days=2),
        )

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Simulates adding creatives with HITL support."""

        # HITL Mode Processing
        operation_mode = self._get_operation_mode("add_creative_assets")

        if operation_mode == "async":
            return self._add_creative_assets_async(media_buy_id, assets, today)
        elif operation_mode == "sync":
            return self._add_creative_assets_sync_with_delay(media_buy_id, assets, today)

        # Continue with immediate processing (default behavior)
        return self._add_creative_assets_immediate(media_buy_id, assets, today)

    def _add_creative_assets_async(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Add creative assets in async HITL mode."""
        self.log("🤖 Processing add_creative_assets in ASYNC mode")

        # Create workflow step for async tracking
        request_data = {
            "media_buy_id": media_buy_id,
            "assets": assets,
            "today": today.isoformat(),
            "operation": "add_creative_assets",
        }

        step = self._create_workflow_step(
            step_type="mock_add_creative_assets", status="pending", request_data=request_data
        )

        self.log(f"   Created workflow step: {step.step_id}")
        self.log(f"   Processing {len(assets)} creative assets")

        # Schedule auto-completion if configured
        if self.async_auto_complete:
            self.log(f"   Auto-completion scheduled in {self.async_auto_complete_delay_ms}ms")
            self._schedule_async_completion(step.step_id, self.async_auto_complete_delay_ms)
        else:
            self.log("   Manual completion required - use complete_task tool")

        # Return pending status for all assets
        return [AssetStatus(creative_id=asset["id"], status="pending") for asset in assets]

    def _add_creative_assets_sync_with_delay(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Add creative assets in sync HITL mode with configurable delay."""
        self.log(f"🤖 Processing add_creative_assets in SYNC mode ({self.sync_delay_ms}ms delay)")

        # Stream working updates during delay
        self._stream_working_updates("add_creative_assets", self.sync_delay_ms)

        # Final delay to reach total configured delay
        import time

        if self.streaming_updates:
            remaining_delay = (
                self.sync_delay_ms - (self.sync_delay_ms // self.update_interval_ms) * self.update_interval_ms
            )
            if remaining_delay > 0:
                time.sleep(remaining_delay / 1000)
        else:
            time.sleep(self.sync_delay_ms / 1000)

        # Simulate approval for each creative if configured
        approved_assets = []
        rejected_assets = []

        for asset in assets:
            approved, rejection_reason = self._simulate_approval()
            if approved:
                approved_assets.append(asset)
            else:
                rejected_assets.append((asset, rejection_reason))
                self.log(f"❌ Creative {asset['id']} rejected: {rejection_reason}")

        if rejected_assets and not approved_assets:
            # All rejected
            raise Exception(f"All creatives rejected: {', '.join([reason for _, reason in rejected_assets])}")
        elif rejected_assets:
            # Some rejected - log warnings but continue with approved ones
            for asset, reason in rejected_assets:
                self.log(f"⚠️ Creative {asset['id']} rejected: {reason}")

        # Continue with immediate processing for approved assets
        self.log(f"✅ SYNC delay completed, proceeding with {len(approved_assets)} approved creatives")
        return self._add_creative_assets_immediate(media_buy_id, approved_assets, today)

    def _add_creative_assets_immediate(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Add creative assets immediately (original behavior)."""

        # Log operation
        self.audit_logger.log_operation(
            operation="add_creative_assets",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=self.adapter_principal_id,
            success=True,
            details={"media_buy_id": media_buy_id, "creative_count": len(assets)},
        )

        self.log(
            f"[bold]MockAdServer.add_creative_assets[/bold] for campaign '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log(f"Adding {len(assets)} creative assets")

        if self.dry_run:
            for i, asset in enumerate(assets):
                self.log("Would call: MockAdServer.uploadCreative()")
                self.log(f"  Creative {i+1}:")
                self.log(f"    'creative_id': '{asset['id']}',")
                self.log(f"    'name': '{asset['name']}',")
                self.log(f"    'format': '{asset['format']}',")
                self.log(f"    'media_url': '{asset['media_url']}',")
                self.log(f"    'click_url': '{asset['click_url']}'")
            self.log(f"Would return: All {len(assets)} creatives with status 'approved'")
        else:
            if media_buy_id not in self._media_buys:
                raise ValueError(f"Media buy {media_buy_id} not found.")

            self._media_buys[media_buy_id]["creatives"].extend(assets)
            self.log(f"✓ Successfully uploaded {len(assets)} creatives")

        return [AssetStatus(creative_id=asset["id"], status="approved") for asset in assets]

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Simulates checking the status of a media buy."""
        if media_buy_id not in self._media_buys:
            raise ValueError(f"Media buy {media_buy_id} not found.")

        buy = self._media_buys[media_buy_id]
        start_date = buy["start_time"]
        end_date = buy["end_time"]

        # Ensure consistent timezone handling for comparisons
        # Convert today to match timezone of stored dates or vice versa
        if start_date.tzinfo and not today.tzinfo:
            today = today.replace(tzinfo=UTC)
        elif not start_date.tzinfo and today.tzinfo:
            start_date = start_date.replace(tzinfo=UTC)
            end_date = end_date.replace(tzinfo=UTC)

        if today < start_date:
            status = "pending_start"
        elif today > end_date:
            status = "completed"
        else:
            status = "delivering"

        # Get buyer_ref from stored media buy data
        buyer_ref = buy.get("buyer_ref", buy.get("po_number", "unknown"))
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, buyer_ref=buyer_ref, status=status)

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Simulates getting delivery data for a media buy with testing hooks support."""
        self.log(
            f"[bold]MockAdServer.get_media_buy_delivery[/bold] for principal '{self.principal.name}' and media buy '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log(f"Reporting date: {today}")

        # Apply testing hooks if strategy context contains them
        if self.strategy_context and hasattr(self.strategy_context, "force_error"):
            if self.strategy_context.force_error == "platform_error":
                self.log("[red]Simulating platform error[/red]")
                raise Exception("Platform connectivity error (simulated)")
            elif self.strategy_context.force_error == "budget_exceeded":
                self.log("[yellow]Simulating budget exceeded scenario[/yellow]")
            elif self.strategy_context.force_error == "low_delivery":
                self.log("[yellow]Simulating low delivery scenario[/yellow]")

        # Simulate API call
        if self.dry_run:
            self.log("Would call: MockAdServer.getDeliveryReport()")
            self.log("  API Request: {")
            self.log(f"    'advertiser_id': '{self.adapter_principal_id}',")
            self.log(f"    'campaign_id': '{media_buy_id}',")
            self.log(f"    'start_date': '{date_range.start.date()}',")
            self.log(f"    'end_date': '{date_range.end.date()}'")
            self.log("  }")
        else:
            self.log(f"Retrieving delivery data for campaign {media_buy_id}")

        # Get the media buy details
        if media_buy_id in self._media_buys:
            buy = self._media_buys[media_buy_id]
            total_budget = buy["total_budget"]
            start_time = buy["start_time"]
            end_time = buy["end_time"]

            # Ensure consistent timezone handling for arithmetic operations
            # Convert today to match timezone of stored dates or vice versa
            if start_time.tzinfo and not today.tzinfo:
                today = today.replace(tzinfo=UTC)
            elif not start_time.tzinfo and today.tzinfo:
                start_time = start_time.replace(tzinfo=UTC)
                end_time = end_time.replace(tzinfo=UTC)

            # Calculate campaign progress
            campaign_duration = (end_time - start_time).total_seconds() / 86400  # days
            elapsed_duration = (today - start_time).total_seconds() / 86400  # days

            if elapsed_duration <= 0:
                # Campaign hasn't started
                impressions = 0
                spend = 0.0
            elif elapsed_duration >= campaign_duration:
                # Campaign completed - deliver full budget with some variance
                spend = total_budget * random.uniform(0.95, 1.05)
                impressions = int(spend / 0.01)  # $10 CPM
            else:
                # Campaign in progress - calculate based on pacing
                progress_ratio = elapsed_duration / campaign_duration
                daily_budget = total_budget / campaign_duration

                # Apply strategy-based pacing multiplier
                pacing_multiplier = 1.0
                if self.strategy_context and hasattr(self.strategy_context, "get_pacing_multiplier"):
                    pacing_multiplier = self.strategy_context.get_pacing_multiplier()
                    if self._is_simulation():
                        self.log(f"🚀 Strategy pacing multiplier: {pacing_multiplier:.2f}")

                # Strategy-aware spend calculation
                if self._is_simulation():
                    scenario = self._get_simulation_scenario()

                    # Check for forced budget exceeded error
                    if self._should_force_error("budget_exceeded"):
                        spend = total_budget * 1.15  # Overspend by 15%
                        self.log("🚨 Simulating budget exceeded scenario")
                    elif scenario == "high_performance":
                        spend = daily_budget * elapsed_duration * pacing_multiplier * 1.3
                        self.log("📈 High performance scenario - accelerated spend")
                    elif scenario == "underperforming":
                        spend = daily_budget * elapsed_duration * pacing_multiplier * 0.6
                        self.log("📉 Underperforming scenario - reduced spend")
                    else:
                        # Normal variance with strategy pacing
                        daily_variance = random.uniform(0.8, 1.2)
                        spend = daily_budget * elapsed_duration * daily_variance * pacing_multiplier
                else:
                    # Production mode - normal variance with strategy pacing
                    daily_variance = random.uniform(0.8, 1.2)
                    spend = daily_budget * elapsed_duration * daily_variance * pacing_multiplier

                # Cap at total budget (unless simulating budget exceeded)
                if not self._should_force_error("budget_exceeded"):
                    spend = min(spend, total_budget)

                impressions = int(spend / 0.01)  # $10 CPM
        else:
            # Fallback for missing media buy
            impressions = random.randint(8000, 12000)
            spend = impressions * 0.01  # $10 CPM

        if not self.dry_run:
            self.log(f"✓ Retrieved delivery data: {impressions:,} impressions, ${spend:,.2f} spend")
        else:
            self.log("Would retrieve delivery data from ad server")

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            totals=DeliveryTotals(impressions=impressions, spend=spend, clicks=100, video_completions=5000),
            by_package=[],
            currency="USD",
        )

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        return True

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        return UpdateMediaBuyResponse(status="accepted")

    def get_config_ui_endpoint(self) -> str | None:
        """Return the URL path for the mock adapter's configuration UI."""
        return "/adapters/mock/config"

    def register_ui_routes(self, app):
        """Register Flask routes for the mock adapter configuration UI."""

        from flask import render_template, request

        @app.route("/adapters/mock/config/<tenant_id>/<product_id>", methods=["GET", "POST"])
        def mock_product_config(tenant_id, product_id):
            # Import here to avoid circular imports
            from functools import wraps

            from database_session import get_db_session

            from src.admin.utils import require_auth
            from src.core.database.models import Product

            # Apply auth decorator manually
            @require_auth()
            @wraps(mock_product_config)
            def wrapped_view():
                with get_db_session() as session:
                    # Get product details
                    product_obj = session.query(Product).filter_by(tenant_id=tenant_id, product_id=product_id).first()

                    if not product_obj:
                        return "Product not found", 404

                    product = {"product_id": product_id, "name": product_obj.name}

                    # Get current config
                    config = product_obj.implementation_config or {}

                    if request.method == "POST":
                        # Update configuration
                        new_config = {
                            "daily_impressions": int(request.form.get("daily_impressions", 100000)),
                            "fill_rate": float(request.form.get("fill_rate", 85)),
                            "ctr": float(request.form.get("ctr", 0.5)),
                            "viewability_rate": float(request.form.get("viewability_rate", 70)),
                            "latency_ms": int(request.form.get("latency_ms", 50)),
                            "error_rate": float(request.form.get("error_rate", 0.1)),
                            "test_mode": request.form.get("test_mode", "normal"),
                            "price_variance": float(request.form.get("price_variance", 10)),
                            "seasonal_factor": float(request.form.get("seasonal_factor", 1.0)),
                            "verbose_logging": "verbose_logging" in request.form,
                            "predictable_ids": "predictable_ids" in request.form,
                        }

                        # Validate the configuration
                        validation_errors = self.validate_product_config(new_config)
                        if validation_errors:
                            return render_template(
                                "adapters/mock_product_config.html",
                                tenant_id=tenant_id,
                                product=product,
                                config=config,
                                error=validation_errors[0],
                            )

                        # Save to database
                        product_obj.implementation_config = new_config
                        session.commit()

                        return render_template(
                            "adapters/mock_product_config.html",
                            tenant_id=tenant_id,
                            product=product,
                            config=new_config,
                            success=True,
                        )

                    return render_template(
                        "adapters/mock_product_config.html",
                        tenant_id=tenant_id,
                        product=product,
                        config=config,
                    )

            return wrapped_view()

    def validate_product_config(self, config: dict) -> list[str]:
        """Validate mock adapter configuration."""
        errors = []

        # Validate ranges
        if config.get("fill_rate", 0) < 0 or config.get("fill_rate", 0) > 100:
            errors.append("Fill rate must be between 0 and 100")

        if config.get("error_rate", 0) < 0 or config.get("error_rate", 0) > 100:
            errors.append("Error rate must be between 0 and 100")

        if config.get("ctr", 0) < 0 or config.get("ctr", 0) > 100:
            errors.append("CTR must be between 0 and 100")

        if config.get("viewability_rate", 0) < 0 or config.get("viewability_rate", 0) > 100:
            errors.append("Viewability rate must be between 0 and 100")

        if config.get("daily_impressions", 0) < 1000:
            errors.append("Daily impressions must be at least 1000")

        if config.get("latency_ms", 0) < 0:
            errors.append("Latency cannot be negative")

        return errors

    async def get_available_inventory(self) -> dict[str, Any]:
        """
        Return mock inventory that simulates a typical publisher's ad server.
        This helps demonstrate the AI configuration capabilities.
        """
        return {
            "placements": [
                {
                    "id": "homepage_top",
                    "name": "Homepage Top Banner",
                    "path": "/",
                    "sizes": ["728x90", "970x250", "970x90"],
                    "position": "above_fold",
                    "typical_cpm": 15.0,
                },
                {
                    "id": "homepage_sidebar",
                    "name": "Homepage Sidebar",
                    "path": "/",
                    "sizes": ["300x250", "300x600"],
                    "position": "right_rail",
                    "typical_cpm": 8.0,
                },
                {
                    "id": "article_inline",
                    "name": "Article Inline",
                    "path": "/article/*",
                    "sizes": ["300x250", "336x280", "728x90"],
                    "position": "in_content",
                    "typical_cpm": 5.0,
                },
                {
                    "id": "article_sidebar_sticky",
                    "name": "Article Sidebar Sticky",
                    "path": "/article/*",
                    "sizes": ["300x250", "300x600"],
                    "position": "sticky_rail",
                    "typical_cpm": 10.0,
                },
                {
                    "id": "category_top",
                    "name": "Category Page Banner",
                    "path": "/category/*",
                    "sizes": ["728x90", "970x90"],
                    "position": "above_fold",
                    "typical_cpm": 12.0,
                },
                {
                    "id": "mobile_interstitial",
                    "name": "Mobile Interstitial",
                    "path": "/*",
                    "sizes": ["320x480", "300x250"],
                    "position": "interstitial",
                    "device": "mobile",
                    "typical_cpm": 20.0,
                },
                {
                    "id": "video_preroll",
                    "name": "Video Pre-roll",
                    "path": "/video/*",
                    "sizes": ["640x360", "640x480"],
                    "position": "preroll",
                    "format": "video",
                    "typical_cpm": 25.0,
                },
            ],
            "ad_units": [
                {
                    "path": "/",
                    "name": "Homepage",
                    "placements": ["homepage_top", "homepage_sidebar"],
                },
                {
                    "path": "/article/*",
                    "name": "Article Pages",
                    "placements": ["article_inline", "article_sidebar_sticky"],
                },
                {
                    "path": "/category/*",
                    "name": "Category Pages",
                    "placements": ["category_top"],
                },
                {
                    "path": "/video/*",
                    "name": "Video Pages",
                    "placements": ["video_preroll"],
                },
                {
                    "path": "/sports",
                    "name": "Sports Section",
                    "placements": ["homepage_top", "article_inline"],
                },
                {
                    "path": "/business",
                    "name": "Business Section",
                    "placements": ["homepage_top", "article_inline"],
                },
                {
                    "path": "/technology",
                    "name": "Tech Section",
                    "placements": [
                        "homepage_top",
                        "article_inline",
                        "article_sidebar_sticky",
                    ],
                },
            ],
            "targeting_options": {
                "geo": {
                    "countries": [
                        "US",
                        "CA",
                        "GB",
                        "AU",
                        "DE",
                        "FR",
                        "IT",
                        "ES",
                        "NL",
                        "SE",
                        "JP",
                        "BR",
                        "MX",
                    ],
                    "us_states": [
                        "CA",
                        "NY",
                        "TX",
                        "FL",
                        "IL",
                        "WA",
                        "MA",
                        "PA",
                        "OH",
                        "GA",
                    ],
                    "us_dmas": [
                        "New York",
                        "Los Angeles",
                        "Chicago",
                        "Philadelphia",
                        "Dallas-Ft. Worth",
                        "San Francisco-Oakland-San Jose",
                    ],
                },
                "device": ["desktop", "mobile", "tablet"],
                "os": ["windows", "macos", "ios", "android", "linux"],
                "browser": ["chrome", "safari", "firefox", "edge", "samsung"],
                "categories": {
                    "iab": ["IAB1", "IAB2", "IAB3", "IAB4", "IAB5"],
                    "custom": [
                        "sports",
                        "business",
                        "technology",
                        "entertainment",
                        "lifestyle",
                        "politics",
                    ],
                },
                "audience": {
                    "demographics": ["18-24", "25-34", "35-44", "45-54", "55+"],
                    "interests": [
                        "sports_enthusiast",
                        "tech_savvy",
                        "luxury_shopper",
                        "travel_lover",
                        "fitness_focused",
                    ],
                    "behavior": ["frequent_buyer", "early_adopter", "price_conscious"],
                },
            },
            "creative_specs": [
                {
                    "type": "display",
                    "sizes": [
                        "300x250",
                        "728x90",
                        "970x250",
                        "300x600",
                        "320x50",
                        "336x280",
                        "970x90",
                    ],
                },
                {
                    "type": "video",
                    "durations": [15, 30, 60],
                    "sizes": ["640x360", "640x480", "1920x1080"],
                },
                {
                    "type": "native",
                    "components": ["title", "description", "image", "cta_button"],
                },
                {"type": "audio", "durations": [15, 30], "formats": ["mp3", "ogg"]},
            ],
            "properties": {
                "monthly_impressions": 50000000,
                "unique_visitors": 10000000,
                "content_categories": [
                    "news",
                    "sports",
                    "business",
                    "technology",
                    "entertainment",
                ],
                "viewability_average": 0.65,
                "premium_inventory_percentage": 0.3,
            },
        }
