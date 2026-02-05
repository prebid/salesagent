# Single Sign-On (SSO) Setup Guide

This guide walks you through configuring SSO for your tenant using OpenID Connect (OIDC). SSO is the recommended authentication method for all deployments.

## First-Time Setup

New tenants start in **Setup Mode**, which enables test credentials for initial configuration:

1. **Start the system** with `docker compose up -d`
2. **Log in** with test credentials:
   - Email: `test_super_admin@example.com`
   - Password: `test123`
3. **Configure SSO** following this guide
4. **Test your SSO login** works
5. **Disable Setup Mode** - after this, only SSO authentication works

> **Important**: Setup Mode is only for initial configuration. Always disable it once SSO is working to ensure production security.

## Overview

The Prebid Sales Agent supports any OIDC-compliant identity provider:

- **Google Workspace** - For organizations using Google
- **Microsoft Entra ID (Azure AD)** - For Microsoft 365 organizations
- **Okta** - Enterprise identity management
- **Auth0** - Developer-friendly identity platform
- **Keycloak** - Open-source identity server
- Any other OIDC-compliant provider

## Prerequisites

Before configuring SSO, you'll need:

1. **Admin access** to your identity provider
2. **Your tenant's redirect URI** - shown on the Users & Access page
3. **Permission** to create OAuth/OIDC applications

## Quick Start

1. Go to **Users & Access** in your tenant dashboard
2. Note your **Redirect URI** (you'll need this when creating the OAuth app)
3. Create an OAuth application in your identity provider (see provider guides below)
4. Enter the **Client ID** and **Client Secret** in the SSO configuration form
5. **Add yourself**: Either add your email as a user OR add your email domain to Allowed Domains
6. Click **Save Configuration**, then **Test Connection**
7. Complete the login flow - SSO is automatically enabled on success
8. Click **Disable Setup Mode** to require SSO for all logins

> **Important**: You must add yourself as a user or add your email domain BEFORE testing. Otherwise you'll see "Access denied" after authenticating with your identity provider.

---

## Provider Setup Guides

### Google Workspace

**Best for**: Organizations already using Google Workspace (Gmail, Google Drive, etc.)

#### Step 1: Create OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select or create a project
3. Navigate to **APIs & Services** > **Credentials**
4. Click **Create Credentials** > **OAuth client ID**
5. If prompted, configure the OAuth consent screen first:
   - User Type: **Internal** (for your organization only) or **External**
   - App name: "Prebid Sales Agent" (or your preferred name)
   - User support email: Your admin email
   - Authorized domains: Add your domain
   - Scopes: Add `openid`, `email`, `profile`

#### Step 2: Configure OAuth Client

1. Application type: **Web application**
2. Name: "Prebid Sales Agent SSO"
3. Authorized redirect URIs: Add your tenant's redirect URI
   - Example: `https://your-tenant.sales-agent.example.com/admin/auth/oidc/callback`
4. Click **Create**
5. Copy the **Client ID** and **Client Secret**

#### Step 3: Enter in AdCP

| Field | Value |
|-------|-------|
| Provider | Google |
| Discovery URL | `https://accounts.google.com/.well-known/openid-configuration` |
| Client ID | Your client ID from step 2 |
| Client Secret | Your client secret from step 2 |

---

### Microsoft Entra ID (Azure AD)

**Best for**: Organizations using Microsoft 365, Azure, or Windows-based identity

#### Step 1: Register Application

1. Go to [Azure Portal](https://portal.azure.com/)
2. Navigate to **Microsoft Entra ID** > **App registrations**
3. Click **New registration**
4. Configure:
   - Name: "Prebid Sales Agent"
   - Supported account types: Choose based on your needs
     - **Single tenant**: Only your organization
     - **Multitenant**: Any Microsoft organization
   - Redirect URI: Select **Web** and enter your tenant's redirect URI
5. Click **Register**

#### Step 2: Configure Authentication

1. In your app registration, go to **Authentication**
2. Under **Web** > **Redirect URIs**, verify your URI is listed
3. Under **Implicit grant and hybrid flows**, ensure these are **unchecked** (we use authorization code flow)
4. Click **Save**

#### Step 3: Create Client Secret

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Add a description and choose expiration
4. Click **Add**
5. **Copy the secret value immediately** (it won't be shown again)

#### Step 4: Get Your Tenant ID

1. Go to **Overview**
2. Copy your **Directory (tenant) ID**

#### Step 5: Enter in AdCP

| Field | Value |
|-------|-------|
| Provider | Microsoft |
| Discovery URL | `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration` |
| Client ID | Application (client) ID from Overview |
| Client Secret | Secret value from step 3 |

Replace `{tenant-id}` with your Directory (tenant) ID.

---

### Okta

**Best for**: Enterprise organizations with centralized identity management

#### Step 1: Create OIDC Application

1. Log in to your [Okta Admin Console](https://your-domain-admin.okta.com)
2. Go to **Applications** > **Applications**
3. Click **Create App Integration**
4. Select:
   - Sign-in method: **OIDC - OpenID Connect**
   - Application type: **Web Application**
5. Click **Next**

#### Step 2: Configure Application

1. App integration name: "Prebid Sales Agent"
2. Grant type: Ensure **Authorization Code** is selected
3. Sign-in redirect URIs: Add your tenant's redirect URI
4. Sign-out redirect URIs: (optional) Add your logout URL
5. Assignments: Choose who can access (e.g., specific groups or everyone)
6. Click **Save**

#### Step 3: Get Credentials

1. On the application page, go to **General** tab
2. Copy the **Client ID** and **Client Secret**

#### Step 4: Enter in AdCP

| Field | Value |
|-------|-------|
| Provider | Okta |
| Discovery URL | `https://your-domain.okta.com/.well-known/openid-configuration` |
| Client ID | Your client ID from step 3 |
| Client Secret | Your client secret from step 3 |

Replace `your-domain` with your Okta domain.

---

### Auth0

**Best for**: Developers and organizations wanting flexible identity options

#### Step 1: Create Application

1. Log in to [Auth0 Dashboard](https://manage.auth0.com/)
2. Go to **Applications** > **Applications**
3. Click **Create Application**
4. Configure:
   - Name: "Prebid Sales Agent"
   - Application Type: **Regular Web Applications**
5. Click **Create**

#### Step 2: Configure Settings

1. Go to the **Settings** tab
2. Note your **Domain**, **Client ID**, and **Client Secret**
3. Under **Application URIs**:
   - Allowed Callback URLs: Add your tenant's redirect URI
   - Allowed Logout URLs: (optional) Add your logout URL
4. Click **Save Changes**

#### Step 3: Enter in AdCP

| Field | Value |
|-------|-------|
| Provider | Auth0 |
| Discovery URL | `https://your-tenant.auth0.com/.well-known/openid-configuration` |
| Client ID | Your client ID from step 2 |
| Client Secret | Your client secret from step 2 |

Replace `your-tenant` with your Auth0 tenant name.

---

### Keycloak

**Best for**: Self-hosted identity management, organizations wanting full control

#### Step 1: Create Client

1. Log in to your Keycloak Admin Console
2. Select your realm (or create one)
3. Go to **Clients** > **Create client**
4. Configure:
   - Client type: **OpenID Connect**
   - Client ID: "adcp-sales-agent"
5. Click **Next**

#### Step 2: Configure Authentication

1. Client authentication: **On**
2. Authorization: **Off** (unless you need fine-grained permissions)
3. Authentication flow: Ensure **Standard flow** is checked
4. Click **Next**

#### Step 3: Configure URIs

1. Valid redirect URIs: Add your tenant's redirect URI
2. Web origins: Add your tenant's base URL (for CORS)
3. Click **Save**

#### Step 4: Get Credentials

1. Go to the **Credentials** tab
2. Copy the **Client secret**

#### Step 5: Enter in AdCP

| Field | Value |
|-------|-------|
| Provider | Keycloak |
| Discovery URL | `https://your-server/realms/your-realm/.well-known/openid-configuration` |
| Client ID | adcp-sales-agent (or your chosen client ID) |
| Client Secret | Secret from step 4 |

Replace `your-server` and `your-realm` with your Keycloak server URL and realm name.

---

## Testing Your Configuration

After entering your SSO configuration:

1. **Add yourself first**: Add your email as a user OR add your email domain to Allowed Domains
2. Click **Save Configuration**
3. Click **Test Connection** - this redirects you to your identity provider
4. Complete the login in your identity provider
5. On success, you'll see a confirmation and SSO is automatically enabled

> **Note**: SSO is automatically enabled when you successfully complete the test flow. No separate "Enable SSO" step is required.

## Transitioning to Production

Once SSO is working:

1. **Verify test logins work** - Have team members test the SSO flow (add them as users or add their domain first)
2. **Click "Disable Setup Mode"** on the Users & Access page
3. After disabling setup mode:
   - Test credentials no longer work
   - Only SSO authentication is allowed
   - You can re-enable setup mode if needed for troubleshooting

## Troubleshooting

### "Invalid redirect URI" Error

- Verify the redirect URI in your identity provider exactly matches what's shown in AdCP
- Check for trailing slashes - they must match exactly
- Ensure you're using HTTPS in production

### "Invalid client" Error

- Double-check your Client ID and Client Secret
- Ensure the OAuth application is not disabled
- Verify the application type is "Web application"

### "Access denied" Error

This typically means you haven't added yourself as an authorized user:

1. **Add yourself first**: Go to Users & Access and either:
   - Add your email address under "Add User", OR
   - Add your email domain under "Allowed Domains"
2. Try the SSO test again

If you've already added yourself:
- Check that the user is authorized to access the OAuth application in your IdP
- For Azure AD: Verify the user has been assigned to the application
- For Okta: Check group assignments

### Users Not Recognized After SSO

- Ensure the identity provider returns the `email` claim
- Add the user's email to authorized domains or emails in tenant settings
- Check that the email domain matches your authorized domains

### SSO Works But Can't Disable Setup Mode

- SSO must be **enabled** before setup mode can be disabled
- SSO is automatically enabled when you successfully complete the test flow
- If SSO shows as "Not Verified", click **Test Connection** and complete the login flow

## Security Best Practices

1. **Use Internal/Single-tenant** when possible to restrict to your organization
2. **Rotate client secrets** periodically (every 6-12 months)
3. **Limit scopes** to only what's needed (`openid`, `email`, `profile`)
4. **Monitor sign-in logs** in your identity provider for unusual activity
5. **Configure session timeouts** in your identity provider for security

## Need Help?

- [Environment Variables Reference](../deployment/environment-variables.md)
- [Security Documentation](../security.md)
- [GitHub Issues](https://github.com/prebid/salesagent/issues)
