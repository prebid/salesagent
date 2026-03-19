"""Shared OAuth user info extraction.

Single source of truth for normalizing user claims from different OIDC
providers (Google, Microsoft, Okta, Auth0, Keycloak).
"""

import logging

logger = logging.getLogger(__name__)


def extract_user_info(token: dict) -> dict | None:
    """Extract user info from OAuth token, handling different provider formats.

    Different OIDC providers return user info in different claim formats:
    - Google: email, name, picture
    - Microsoft: email OR preferred_username, name, picture
    - Okta: email, name, picture (or custom claims)
    - Auth0: email, name, picture
    - Keycloak: email, preferred_username, name, picture

    Args:
        token: OAuth token response dict containing userinfo or id_token.

    Returns:
        Dict with normalized keys (email, name, picture) or None if
        user info cannot be extracted.
    """
    import jwt

    user = token.get("userinfo")

    if not user:
        # Try to decode from ID token
        id_token = token.get("id_token")
        if id_token:
            try:
                user = jwt.decode(id_token, options={"verify_signature": False})
            except Exception as e:
                logger.warning(f"Failed to decode ID token: {e}")
                return None

    if not user:
        return None

    # Extract email - try multiple claim names
    email = (
        user.get("email")
        or user.get("preferred_username")
        or user.get("upn")  # Microsoft UPN
        or user.get("sub")  # Fallback to subject
    )

    if not email:
        logger.error(f"Could not extract email from user claims: {list(user.keys())}")
        return None

    # Extract name - try multiple claim names
    name = user.get("name") or user.get("display_name")
    if not name:
        # Try constructing from given/family names
        given = user.get("given_name", "")
        family = user.get("family_name", "")
        if given or family:
            name = f"{given} {family}".strip()
    if not name:
        # Fallback to email prefix
        name = email.split("@")[0]

    # Extract picture - try multiple claim names
    picture = user.get("picture") or user.get("avatar_url") or user.get("photo") or ""

    return {
        "email": email.lower(),
        "name": name,
        "picture": picture,
    }
