"""Policy check service for analyzing advertising briefs."""

import logging
from datetime import UTC, datetime
from enum import Enum

from adcp import BrandManifest
from pydantic import BaseModel, Field

from src.core.schemas import Product
from src.services.ai import AIServiceFactory, TenantAIConfig
from src.services.ai.agents.policy_agent import (
    check_policy_compliance,
    create_policy_agent,
)

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "not provided" from "explicitly None"
_UNSET = object()


class PolicyStatus(str, Enum):
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

    Uses Pydantic AI for multi-model support. Configuration priority:
    1. Explicit tenant_ai_config parameter
    2. Platform defaults from environment variables
    """

    def __init__(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
        gemini_api_key: str | None | object = _UNSET,  # Deprecated, kept for backward compatibility
    ):
        """Initialize the policy check service.

        Args:
            tenant_ai_config: Tenant AI configuration for model selection.
            gemini_api_key: DEPRECATED - Use tenant_ai_config instead. Kept for backward compatibility.
        """
        self._factory = AIServiceFactory()

        # Handle backward compatibility with gemini_api_key parameter
        if gemini_api_key is not _UNSET and gemini_api_key is not None:
            # Legacy usage - create a minimal config with just the API key
            if isinstance(gemini_api_key, str):
                tenant_ai_config = TenantAIConfig(
                    provider="gemini",
                    model="gemini-2.0-flash",
                    api_key=gemini_api_key,
                )
        elif gemini_api_key is None:
            # Explicit None means disable AI
            self.ai_enabled = False
            self._agent = None
            return

        # Get effective configuration
        effective_config = self._factory.get_effective_config(tenant_ai_config)
        self.ai_enabled = effective_config["has_api_key"]

        if self.ai_enabled:
            model_string = self._factory.create_model(tenant_ai_config)
            self._agent = create_policy_agent(model_string)
        else:
            logger.warning("No AI API key configured. Policy checks will use basic rules only.")
            self._agent = None

    async def check_brief_compliance(
        self,
        brief: str,
        promoted_offering: str | None = None,
        brand_manifest: BrandManifest | str | None = None,
        tenant_policies: dict | None = None,
    ) -> PolicyCheckResult:
        """Check if an advertising brief complies with policies.

        Args:
            brief: The advertising brief description
            promoted_offering: DEPRECATED: Use brand_manifest instead (still supported)
            brand_manifest: BrandManifest model or URL string (preferred over promoted_offering)
            tenant_policies: Optional tenant-specific policy overrides

        Returns:
            PolicyCheckResult with compliance status and details
        """
        # Extract brand info from brand_manifest if provided
        brand_info = None
        if brand_manifest:
            if isinstance(brand_manifest, BrandManifest):
                # Extract name and tagline from typed model
                brand_name = brand_manifest.name
                brand_tagline = brand_manifest.tagline or ""
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
