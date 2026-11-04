"""Policy check service for analyzing advertising briefs."""

import logging
from datetime import UTC, datetime
from enum import StrEnum

from adcp.types.generated_poc.brand import Brand  # TODO: no stable alias in adcp.types
from pydantic import BaseModel, Field

from src.core.schemas import Product
from src.services.ai import TenantAIConfig, get_factory
from src.services.ai.agents.policy_agent import (
    check_policy_compliance,
    create_policy_agent,
)

logger = logging.getLogger(__name__)


class PolicyStatus(StrEnum):
    """Policy compliance status options."""

    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"


class PolicyCheckResult(BaseModel):
    """Result of policy compliance check."""

    status: PolicyStatus
    reason: str | None = None
    restrictions: list[str] | None = Field(default_factory=list)
    warnings: list[str] | None = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyCheckService:
    """Service for checking advertising briefs against policy compliance rules.

    Uses Pydantic AI for multi-model support, on the seller's own AI configuration only.
    """

    def __init__(self, tenant_ai_config: TenantAIConfig | None = None):
        """Initialize the policy check service.

        Args:
            tenant_ai_config: The seller's own AI configuration, which the caller has
                already gated on ``AIServiceFactory.is_ai_enabled``, so building its model
                cannot fail on configuration grounds. ``None`` runs basic rules only.
        """
        if tenant_ai_config is None:
            logger.warning("No AI configuration. Policy checks will use basic rules only.")
            self.ai_enabled = False
            self._agent = None
            return

        self.ai_enabled = True
        self._agent = create_policy_agent(get_factory().create_model(tenant_ai_config))

    async def check_brief_compliance(
        self,
        brief: str,
        promoted_offering: str | None = None,
        brand_manifest: Brand | str | None = None,
        tenant_policies: dict | None = None,
    ) -> PolicyCheckResult:
        """Check if an advertising brief complies with policies.

        Args:
            brief: The advertising brief description
            promoted_offering: DEPRECATED: Use brand_manifest instead (still supported)
            brand_manifest: Brand model or URL string (preferred over promoted_offering)
            tenant_policies: Optional tenant-specific policy overrides

        Returns:
            PolicyCheckResult with compliance status and details
        """
        # Extract brand info from brand_manifest if provided
        brand_info = None
        if brand_manifest:
            if isinstance(brand_manifest, Brand):
                # Extract name and tagline from adcp 3.12 Brand model
                # names is list[LocalizedName] where LocalizedName is RootModel[dict[str, str]]
                brand_name = None
                if brand_manifest.names:
                    first_name = brand_manifest.names[0]
                    brand_name = next(iter(first_name.root.values()), None)
                brand_tagline = ""
                if brand_manifest.tagline:
                    if isinstance(brand_manifest.tagline, str):
                        brand_tagline = brand_manifest.tagline
                    else:
                        # Tagline is RootModel[list[LocalizedName]]
                        first_tl = brand_manifest.tagline.root[0] if brand_manifest.tagline.root else None
                        brand_tagline = next(iter(first_tl.root.values()), "") if first_tl else ""
                brand_info = f"{brand_name} - {brand_tagline}" if brand_tagline else brand_name
            elif isinstance(brand_manifest, str):
                # URL string - use as-is
                brand_info = f"Brand manifest URL: {brand_manifest}"

        # Fall back to promoted_offering if brand_manifest not provided
        if not brand_info and promoted_offering:
            brand_info = promoted_offering

        # Combine brief and brand info for analysis
        full_context = brief
        if brand_info:
            full_context = f"Brief: {brief}\n\nAdvertiser/Product: {brand_info}"

        # Use AI analysis when available
        if self.ai_enabled and self._agent:
            analysis = await check_policy_compliance(self._agent, full_context, tenant_policies)
            return PolicyCheckResult(
                status=PolicyStatus(analysis.status),
                reason=analysis.reason,
                restrictions=analysis.restrictions,
                warnings=analysis.warnings,
            )
        else:
            # Fallback if no AI is available - allow with warning
            return PolicyCheckResult(
                status=PolicyStatus.ALLOWED, warnings=["Policy check unavailable - AI service not configured"]
            )

    def _check_basic_rules(self, text: str) -> PolicyCheckResult:
        """Apply basic policy rules (deprecated - kept for compatibility).

        Args:
            text: Text to check

        Returns:
            PolicyCheckResult
        """
        # This method is deprecated since we always use AI analysis
        # Return allowed by default
        return PolicyCheckResult(status=PolicyStatus.ALLOWED, warnings=[])

    def check_product_eligibility(self, policy_result: PolicyCheckResult, product: Product) -> tuple[bool, str | None]:
        """Check if a product is eligible based on policy result.

        Args:
            policy_result: Result from brief compliance check
            product: Product model instance

        Returns:
            Tuple of (is_eligible, reason_if_not)
        """
        # Blocked briefs can't use any products
        if policy_result.status == PolicyStatus.BLOCKED:
            return False, policy_result.reason

        return True, None
