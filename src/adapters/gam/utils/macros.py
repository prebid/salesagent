"""GAM macro substitution utilities.

Maps AdCP universal macros to GAM-specific macros in tracking URLs.

GAM uses multiple macro formats:
- %%MACRO%% - Standard tracking macros
- ${MACRO} - GDPR/TCF macros
- %macro! - Expand macros (for IDs)

See:
- AdCP macros: https://docs.adcontextprotocol.org/docs/creative/universal-macros
- GAM macros: https://support.google.com/admanager/answer/2376981
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# AdCP Universal Macro -> GAM Macro mapping
# Based on official GAM docs: https://support.google.com/admanager/answer/2376981
#
# None means no GAM equivalent - macro will pass through unchanged.
#
ADCP_TO_GAM_MACRO_MAP: dict[str, str | None] = {
    # ==========================================================================
    # Common Macros
    # ==========================================================================
    "{MEDIA_BUY_ID}": "%ebuy!",  # GAM Order ID
    "{PACKAGE_ID}": "%eaid!",  # GAM Line Item ID
    "{CREATIVE_ID}": "%ecid!",  # GAM Creative ID
    "{CACHEBUSTER}": "%%CACHEBUSTER%%",
    "{TIMESTAMP}": None,  # Not in GAM docs (use CACHEBUSTER for cache-busting)
    "{CLICK_URL}": "%%CLICK_URL_ESC%%",
    "{CLICK_URL_UNESC}": "%%CLICK_URL_UNESC%%",
    # ==========================================================================
    # Privacy & Compliance Macros
    # ==========================================================================
    "{GDPR}": "${GDPR}",
    "{GDPR_CONSENT}": "${GDPR_CONSENT_XXX}",  # TCF consent string
    "{US_PRIVACY}": "${US_PRIVACY}",  # CCPA string
    "{GPP_STRING}": "${GPP_STRING}",  # GPP consent string
    "{GPP_SID}": "${GPP_SID}",  # GPP Section IDs
    "{LIMIT_AD_TRACKING}": "%%ADVERTISING_IDENTIFIER_IS_LAT%%",
    # ==========================================================================
    # Device & Environment Macros
    # ==========================================================================
    "{DEVICE_TYPE}": None,  # No GAM equivalent
    "{OS}": None,
    "{OS_VERSION}": None,
    "{DEVICE_MAKE}": None,
    "{DEVICE_MODEL}": None,
    "{USER_AGENT}": None,
    "{APP_BUNDLE}": None,
    "{APP_NAME}": None,
    # ==========================================================================
    # Geographic Macros
    # ==========================================================================
    "{COUNTRY}": None,
    "{REGION}": None,
    "{CITY}": None,
    "{ZIP}": None,
    "{DMA}": None,
    "{LAT}": None,
    "{LONG}": None,
    # ==========================================================================
    # Identity Macros
    # ==========================================================================
    "{DEVICE_ID}": "%%ADVERTISING_IDENTIFIER_PLAIN%%",
    "{DEVICE_ID_TYPE}": "%%ADVERTISING_IDENTIFIER_TYPE%%",
    # ==========================================================================
    # Web Context Macros
    # ==========================================================================
    "{DOMAIN}": "%%SITE%%",  # Domain of the URL parameter in ad tag
    "{PAGE_URL}": None,  # Use %%DESCRIPTION_URL%% for video context only
    "{REFERRER}": "%%REFERRER_URL_ESC%%",
    "{KEYWORDS}": None,
    # ==========================================================================
    # Placement & Position Macros
    # ==========================================================================
    "{PLACEMENT_ID}": "%epid!",  # Ad Unit ID
    "{FOLD_POSITION}": None,
    "{AD_WIDTH}": "%%WIDTH%%",
    "{AD_HEIGHT}": "%%HEIGHT%%",
    # ==========================================================================
    # Video Content Macros
    # ==========================================================================
    "{VIDEO_ID}": "%%VIDEO_ID%%",
    "{VIDEO_TITLE}": "%%VIDEO_TITLE%%",
    "{VIDEO_DURATION}": "%%VIDEO_DURATION%%",  # In milliseconds
    "{VIDEO_CATEGORY}": None,
    "{CONTENT_GENRE}": None,
    "{CONTENT_RATING}": None,
    "{PLAYER_WIDTH}": None,
    "{PLAYER_HEIGHT}": None,
    # ==========================================================================
    # Video Ad Pod Macros
    # ==========================================================================
    "{POD_POSITION}": "%%TAG_PARAM:ppos%%",
    "{POD_SIZE}": None,
    "{AD_BREAK_ID}": None,
    # ==========================================================================
    # Other Macros
    # ==========================================================================
    "{AXEM}": "%%PATTERN:axem%%",  # Filled by prebid RTD module at render time
}


def substitute_macros(url: str) -> str:
    """Substitute AdCP universal macros with GAM equivalents in a tracking URL.

    Macros without a GAM equivalent are left as-is (passthrough).

    Args:
        url: Tracking URL potentially containing AdCP macros like {CACHEBUSTER}

    Returns:
        URL with AdCP macros replaced by GAM macros where mappings exist

    Example:
        >>> substitute_macros("https://t.com/pixel?cb={CACHEBUSTER}&pid={PLACEMENT_ID}")
        'https://t.com/pixel?cb=%%CACHEBUSTER%%&pid=%epid!'
    """
    result = url
    unmapped_found: list[str] = []

    for adcp_macro, gam_macro in ADCP_TO_GAM_MACRO_MAP.items():
        if adcp_macro not in result:
            continue

        if gam_macro is not None:
            result = result.replace(adcp_macro, gam_macro)
        else:
            unmapped_found.append(adcp_macro)

    if unmapped_found:
        logger.debug(
            "Tracking URL contained unmapped AdCP macros (passed through)",
            extra={"unmapped_macros": unmapped_found},
        )

    return result


def substitute_tracking_urls(tracking_urls: list[str]) -> list[str]:
    """Apply macro substitution to a list of tracking URLs.

    Args:
        tracking_urls: List of tracking URLs

    Returns:
        List of URLs with macros substituted
    """
    return [substitute_macros(url) for url in tracking_urls]
