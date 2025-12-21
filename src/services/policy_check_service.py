"""Policy check service for analyzing advertising briefs."""

import logging
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

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
        brand_manifest: dict | str | None = None,
        tenant_policies: dict | None = None,
    ) -> PolicyCheckResult:
        """Check if an advertising brief complies with policies.

        Args:
            brief: The advertising brief description
            promoted_offering: DEPRECATED: Use brand_manifest instead (still supported)
            brand_manifest: Brand manifest dict or URL string (preferred over promoted_offering)
            tenant_policies: Optional tenant-specific policy overrides

        Returns:
            PolicyCheckResult with compliance status and details
        """
        # Extract brand info from brand_manifest if provided
        brand_info = None
        if brand_manifest:
            if isinstance(brand_manifest, dict):
                # Extract name and description from manifest
                brand_name = brand_manifest.get("name", "")
                brand_description = brand_manifest.get("description", "")
                brand_info = f"{brand_name} - {brand_description}" if brand_description else brand_name
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

    def check_product_eligibility(
        self, policy_result: PolicyCheckResult, product: dict, advertiser_category: str | None = None
    ) -> tuple[bool, str | None]:
        """Check if a product is eligible based on policy result and audience compatibility.

        Args:
            policy_result: Result from brief compliance check
            product: Product dictionary with audience characteristic fields
            advertiser_category: Optional advertiser category (e.g., 'alcohol', 'gambling')

        Returns:
            Tuple of (is_eligible, reason_if_not)
        """
        # Blocked briefs can't use any products
        if policy_result.status == PolicyStatus.BLOCKED:
            return False, policy_result.reason

        # Check age-based compatibility
        targeted_ages = product.get("targeted_ages")
        verified_minimum_age = product.get("verified_minimum_age")

        # Extract advertiser category from restrictions if not provided
        if not advertiser_category and policy_result.restrictions:
            # Try to infer category from restrictions
            restriction_text = " ".join(policy_result.restrictions).lower()
            if any(term in restriction_text for term in ["alcohol", "beer", "wine", "liquor"]):
                advertiser_category = "alcohol"
            elif any(term in restriction_text for term in ["gambling", "casino", "betting"]):
                advertiser_category = "gambling"
            elif any(term in restriction_text for term in ["tobacco", "cigarettes", "vaping"]):
                advertiser_category = "tobacco"
            elif any(term in restriction_text for term in ["cannabis", "marijuana", "cbd"]):
                advertiser_category = "cannabis"

        # Age-restricted categories require appropriate audience
        age_restricted_categories = ["alcohol", "gambling", "tobacco", "cannabis"]

        if advertiser_category in age_restricted_categories:
            # Cannot run on child-focused content
            if targeted_ages == "children":
                return False, f"{advertiser_category} advertising cannot run on child-focused content"

            # Check minimum age requirements
            if advertiser_category == "alcohol":
                required_age = 21
            else:  # gambling, tobacco, cannabis
                required_age = 18

            # Check if product has appropriate age verification
            if verified_minimum_age and verified_minimum_age >= required_age:
                # Product has age gating that meets requirements
                pass
            elif targeted_ages == "teens":
                # Teens content without age verification cannot have restricted ads
                return False, f"{advertiser_category} advertising requires {required_age}+ audience or age verification"
            elif not verified_minimum_age and targeted_ages != "adults":
                # No age verification and not explicitly adults-only
                return False, f"{advertiser_category} advertising requires age-gated content or adults-only audience"

        # Check for compatibility with restricted content
        if policy_result.status == PolicyStatus.RESTRICTED and targeted_ages == "children":
            # Children's content may not be suitable for restricted advertisers
            return False, "Children's content not compatible with restricted advertising"

        return True, None
