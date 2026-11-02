#!/usr/bin/env bash
# Write /opt/salesagent/.env once, with every secret generated here. Guide: docs/deployment/vm.md.
#
#   DOMAIN=agents.example.com SUPPORT_EMAIL=ops@example.com SUPER_ADMIN_EMAILS=you@example.com \
#   OAUTH_DISCOVERY_URL=https://idp.example.com/.well-known/openid-configuration \
#   OAUTH_CLIENT_ID=salesagent OAUTH_CLIENT_SECRET=... ./make-env.sh
#
# It refuses to touch an existing .env. ENCRYPTION_KEY and the signing KEK decrypt values already
# stored in the database; a new one strands every one of them. Back the file up instead.
set -euo pipefail
cd "$(dirname "$0")"
umask 077

if [[ -e .env ]]; then
    echo ".env already exists; not overwriting it (its keys decrypt stored data)" >&2
    exit 1
fi

: "${DOMAIN:?the apex host, e.g. agents.example.com}"
: "${SUPPORT_EMAIL:?an operator mailbox; Caddy also registers it with Let's Encrypt}"
: "${SUPER_ADMIN_EMAILS:?comma-separated emails allowed to administer every tenant}"
: "${OAUTH_DISCOVERY_URL:?your OIDC provider's /.well-known/openid-configuration URL}"
: "${OAUTH_CLIENT_ID:?the client id registered at your OIDC provider}"
: "${OAUTH_CLIENT_SECRET:?the client secret registered at your OIDC provider}"
CREATIVE_AGENT="${CREATIVE_AGENT:-yes}"

hex() { openssl rand -hex "$1"; }
# A Fernet key is 32 random bytes in URL-safe base64.
fernet=$(openssl rand 32 | base64 | tr '+/' '-_')

if [[ "$CREATIVE_AGENT" == yes ]]; then
    creative_url="CREATIVE_AGENT_URL=https://${DOMAIN}/api/creative-agent"
else
    creative_url="# CREATIVE_AGENT_URL=  (unset: the public agent at https://creative.adcontextprotocol.org)"
fi

cat > .env <<EOF
# Generated $(date -u +%FT%TZ) by make-env.sh. Mode 600. Back this file up somewhere safe:
# without ENCRYPTION_KEY and SALESAGENT_SIGNING_KEK the database's encrypted values are lost.

POSTGRES_PASSWORD=$(hex 24)

ENVIRONMENT=production
ADCP_MULTI_TENANT=true
SALES_AGENT_DOMAIN=${DOMAIN}
ADMIN_DOMAIN=admin.${DOMAIN}
ADMIN_UI_URL=https://${DOMAIN}/admin
ALLOWED_ORIGINS=https://${DOMAIN},https://admin.${DOMAIN}
SUPPORT_EMAIL=${SUPPORT_EMAIL}

# Encrypts stored secrets (OIDC client secrets, adapter credentials). Never regenerate.
ENCRYPTION_KEY=${fernet}
FLASK_SECRET_KEY=$(hex 32)

# Admin UI login through your OIDC provider. Register the redirect URI below with it.
OAUTH_DISCOVERY_URL=${OAUTH_DISCOVERY_URL}
OAUTH_CLIENT_ID=${OAUTH_CLIENT_ID}
OAUTH_CLIENT_SECRET=${OAUTH_CLIENT_SECRET}
OAUTH_SCOPES=openid email profile
GOOGLE_OAUTH_REDIRECT_URI=https://${DOMAIN}/admin/auth/google/callback
SUPER_ADMIN_EMAILS=${SUPER_ADMIN_EMAILS}

# Request signing: the KEK that encrypts every tenant's private signing key. Never regenerate.
ADCP_SIGNING_KEY_PASSPHRASE_ENV=SALESAGENT_SIGNING_KEK
SALESAGENT_SIGNING_KEK=$(hex 32)

# The scheduled ad-server inventory sync authenticates with this.
SYNC_API_KEY=$(hex 32)

# Creative formats: the pinned reference creative agent (compose profile creative-agent).
${creative_url}
CREATIVE_PG_PASSWORD=$(hex 24)
CREATIVE_AGENT_TOKEN_SECRET=$(hex 32)
EOF
echo "wrote $(pwd)/.env"
