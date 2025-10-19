# Google Ad Manager Service Account Authentication

## Overview

The AdCP Sales Agent supports two authentication methods for Google Ad Manager:

1. **OAuth (Refresh Token)** - User-based authentication requiring manual token refresh
2. **Service Account** - Automated authentication using Google Cloud service accounts (recommended for production)

## 🚀 New: Automatic Service Account Provisioning (Recommended for Partners)

**For partner integrations, we now create and manage service accounts for you automatically!**

Instead of partners sending us their service account JSON credentials, we:
1. Create a service account in our GCP project
2. Provide you with the service account email
3. You add this email as a user in your GAM with Trafficker role
4. We handle all credential management securely

## Service Account Benefits

- ✅ **No manual refresh**: Service accounts don't expire like OAuth tokens
- ✅ **Better for automation**: No user interaction needed
- ✅ **Granular permissions**: Can scope access to specific advertisers
- ✅ **Audit trail**: Service account shows in GAM audit logs
- ✅ **Multi-tenant**: Each tenant can have their own service account
- ✅ **Cloud-native**: Credentials stored encrypted in database, no file management

## Security Model

Service account authentication uses **two-factor control**:

1. **Private Key** (we control): Stored encrypted in our database, used to cryptographically sign API requests
2. **GAM User List** (partner controls): Partner must explicitly add the service account email to their GAM

**Both are required for access:**
- Just knowing the email is NOT enough - API calls must be signed with the private key
- Just having the private key is NOT enough - partner must grant permissions in their GAM

**Partner maintains control:**
- Can revoke access anytime by removing email from GAM
- Controls what permissions to grant (Trafficker, Salesperson, etc.)
- Can restrict to specific advertisers via GAM teams
- All activity appears in their GAM audit logs

## Required Service Account Roles

### Recommended: "Trafficker" Role

The **Trafficker** role is recommended for most deployments. It provides:

- ✅ Create and manage orders (campaigns)
- ✅ Create and manage line items
- ✅ Upload and associate creatives
- ✅ Read inventory (ad units, placements)
- ✅ Read and write custom targeting
- ✅ Generate reports
- ❌ Cannot modify network settings
- ❌ Cannot create new advertisers

### Alternative: "Salesperson" Role

If you want read-only with limited write access:

- ✅ Create proposals (if using Programmatic Guaranteed)
- ✅ View orders and line items
- ❌ Cannot create orders directly
- ❌ Limited creative management

### Custom Role (Minimum Permissions)

If creating a custom role, the service account needs these specific permissions:

```
Orders:
  - Create
  - Read
  - Update

Line Items:
  - Create
  - Read
  - Update

Creatives:
  - Create
  - Read
  - Update
  - Associate

Ad Units:
  - Read (for inventory sync)

Placements:
  - Read

Custom Targeting:
  - Read
  - Write

Reports:
  - Run

Network:
  - Read (to get timezone and network info)
```

## Prerequisites for Automatic Service Account Creation

The automatic service account creation feature requires:

1. **GCP Project Configuration**: The `GCP_PROJECT_ID` environment variable must be set to your Google Cloud Project ID
2. **Service Account Permissions**: The application's default credentials must have permissions to create service accounts and keys in that project (requires `roles/iam.serviceAccountAdmin` or `roles/iam.serviceAccountKeyAdmin`)

If you haven't set these up yet, see your system administrator or cloud platform documentation.

## Setup Instructions (New Automatic Flow - Recommended)

### Step 1: Request Service Account Creation

1. Log into the Admin UI (http://localhost:8001 or your production URL)
2. Navigate to **Tenant Settings** → **Ad Server**
3. Select **Google Ad Manager** as your adapter
4. Scroll to the **Service Account Integration** section
5. Click **🔑 Create Service Account**
6. Wait a few seconds while we create the service account in our GCP project
7. Copy the service account email that appears

### Step 2: Add Service Account to Your GAM

1. Log into your [Google Ad Manager](https://admanager.google.com/) account
2. Navigate to **Admin** → **Access & authorization** → **Users**
3. Click **New user**
4. Paste the service account email from Step 1
5. Assign role: **Trafficker** (recommended)
6. Under **Teams**, select specific advertisers (optional but recommended for security)
7. Click **Save**

### Step 3: Test Connection

1. Return to the Admin UI settings page
2. Click **Test Connection** button
3. If successful, you're done! If not, verify:
   - Service account email was added correctly in GAM
   - Trafficker role was assigned
   - You clicked Save in GAM

---

## Alternative: Manual Service Account Setup (Legacy)

**Note**: The automatic flow above is recommended. Use this only if you need to create your own service account.

### Step 1: Create Service Account in Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **IAM & Admin** → **Service Accounts**
4. Click **Create Service Account**
5. Enter details:
   - **Name**: `adcp-sales-agent` (or your preferred name)
   - **Description**: `Service account for AdCP Sales Agent GAM integration`
6. Click **Create and Continue**
7. Skip role assignment (will assign in GAM directly)
8. Click **Done**

### Step 2: Create and Download Service Account Key

1. Click on the newly created service account
2. Go to the **Keys** tab
3. Click **Add Key** → **Create new key**
4. Select **JSON** format
5. Click **Create**
6. The JSON key file will download automatically
7. **⚠️ Important**: Store this file securely - it cannot be recovered if lost

### Step 3: Grant Access in Google Ad Manager

1. Go to [Google Ad Manager](https://admanager.google.com/)
2. Navigate to **Admin** → **Access & authorization** → **Users**
3. Click **New user**
4. Enter the service account email (format: `adcp-sales-agent@PROJECT-ID.iam.gserviceaccount.com`)
5. Assign role: **Trafficker** (recommended)
6. Under **Teams**, select the specific advertisers the service account should manage (recommended)
   - This restricts access to only those advertisers for better security
7. Click **Save**

### Step 4: Configure in AdCP Sales Agent

#### Via Admin UI:

1. Log into the Admin UI (http://localhost:8001 or your production URL)
2. Navigate to **Tenant Settings** → **Ad Server**
3. Select **Service Account** as authentication method
4. Upload or paste the contents of the JSON key file downloaded in Step 2
5. Enter the **Network Code** (if not auto-detected)
6. Click **Test Connection** to verify
7. Click **Save**

#### Via API:

```bash
curl -X POST http://localhost:8001/tenant/{tenant_id}/gam/configure \
  -H "Content-Type: application/json" \
  -d '{
    "auth_method": "service_account",
    "service_account_json": "{...JSON key contents...}",
    "network_code": "12345678"
  }'
```

## Security Considerations

### Credential Storage

- Service account JSON is **encrypted at rest** using Fernet symmetric encryption
- Encryption key must be set via `ENCRYPTION_KEY` environment variable
- Generate a key: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
- Credentials are **decrypted only when needed** for API calls

### Access Control

- Service accounts should be granted **minimum required permissions**
- Use **team-based access** in GAM to restrict to specific advertisers
- Each tenant can have a **separate service account** for isolation
- Service account email appears in GAM **audit logs** for accountability

### Best Practices

1. **Rotate keys regularly**: Create new keys and delete old ones every 90 days
2. **Use separate service accounts per tenant**: Better isolation and security
3. **Monitor audit logs**: Check GAM audit logs for unexpected service account activity
4. **Restrict advertiser access**: Don't grant network-wide access if not needed
5. **Secure the encryption key**: Store `ENCRYPTION_KEY` in secrets manager, not in code

## Troubleshooting

### "Invalid service account JSON" Error

**Cause**: The JSON format is incorrect or incomplete

**Solution**: Ensure the JSON contains all required fields:
- `type: "service_account"`
- `project_id`
- `private_key_id`
- `private_key`
- `client_email`

### "Permission Denied" Errors

**Cause**: Service account doesn't have required permissions in GAM

**Solution**:
1. Verify the service account is added as a user in GAM
2. Check that "Trafficker" role (or equivalent) is assigned
3. Ensure the service account has access to the specific advertiser

### "Network Code Not Found" Error

**Cause**: Service account doesn't have access to the specified network

**Solution**:
1. Verify the network code is correct
2. Check that the service account is added to the correct GAM network
3. If managing multiple networks, ensure the correct one is selected

### Connection Test Fails

**Cause**: Authentication or network issues

**Solution**:
1. Check that the service account JSON is complete and valid
2. Verify internet connectivity to Google APIs
3. Check firewall rules allow outbound HTTPS (port 443)
4. Verify the service account hasn't been deleted or disabled

## Migration from OAuth to Service Account

To migrate an existing tenant from OAuth to service account authentication:

1. Create and configure the service account (Steps 1-3 above)
2. In Admin UI, go to **Tenant Settings** → **Ad Server**
3. Change authentication method to **Service Account**
4. Upload the service account JSON key
5. Test connection to verify
6. Save configuration

The system will automatically:
- Clear the old OAuth refresh token
- Encrypt and store the service account JSON
- Update the authentication method in the database

## Comparison: OAuth vs Service Account

| Feature | OAuth (Refresh Token) | Service Account |
|---------|----------------------|-----------------|
| **Setup complexity** | Medium (manual OAuth flow) | Low (JSON key download) |
| **Token expiration** | Yes (requires refresh) | No (permanent) |
| **User dependency** | Requires Google account | Independent |
| **Automation** | Difficult | Easy |
| **Audit trail** | User email | Service account email |
| **Credential storage** | Token string | JSON key (encrypted) |
| **Best for** | Development, testing | Production, automation |

## API Reference

### AdapterConfig Model Fields

```python
gam_auth_method: str                    # "oauth" or "service_account"
gam_refresh_token: str | None           # OAuth refresh token (if using OAuth)
gam_service_account_json: str | None    # Encrypted service account JSON (if using service account)
```

### GAMAuthManager Methods

```python
# Check authentication method
auth_manager.is_oauth_configured() -> bool
auth_manager.is_service_account_configured() -> bool
auth_manager.get_auth_method() -> str  # "oauth" or "service_account"

# Get credentials (handles both methods)
auth_manager.get_credentials() -> Credentials
```

### Helper Functions

```python
from src.adapters.gam import build_gam_config_from_adapter

# Build config dict from AdapterConfig model
config = build_gam_config_from_adapter(adapter_config)
# Returns dict with appropriate auth credentials based on method
```

## FAQ

**Q: Can I use the same service account for multiple tenants?**

A: Yes, but it's not recommended. Each tenant should have its own service account for better isolation and security.

**Q: What happens if the service account key is leaked?**

A: Delete the compromised key immediately in Google Cloud Console, create a new key, and update the configuration in AdCP Sales Agent.

**Q: Can I switch between OAuth and service account without losing data?**

A: Yes, the authentication method only affects how we connect to GAM. Your campaigns, creatives, and other data remain unchanged.

**Q: Do I need to rotate service account keys?**

A: Yes, Google recommends rotating keys every 90 days. Create a new key, update the configuration, then delete the old key.

**Q: Can I use a service account for development/testing?**

A: Yes, but OAuth is often easier for development since you can use your own Google account. Service accounts are recommended for production.

## Support

For issues with service account authentication:

1. Check the [Troubleshooting](#troubleshooting) section above
2. Review GAM audit logs for permission errors
3. Verify the service account configuration in Google Cloud Console
4. Contact support with:
   - Error messages from the Admin UI
   - Service account email
   - GAM network code
   - Authentication method being used
