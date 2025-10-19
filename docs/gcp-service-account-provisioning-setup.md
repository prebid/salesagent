# GCP Service Account Provisioning - Deployment Setup

## Overview

This guide explains how to set up the automatic service account provisioning feature for production deployment on Fly.io (or any cloud platform).

## Architecture

```
Your Sales Agent (Fly.io)
    ↓
Uses "Management Service Account"
    ↓
Creates "Partner Service Accounts" in your GCP project
    ↓
Partners add these to their GAM
```

## Prerequisites

1. A Google Cloud Platform (GCP) project
2. Access to create service accounts in that project
3. Fly.io CLI installed and authenticated

## Step-by-Step Setup

### Step 1: Create a GCP Project (if needed)

```bash
# Create a new GCP project (or use existing)
gcloud projects create adcp-sales-agent-prod --name="AdCP Sales Agent Production"

# Set as default project
gcloud config set project adcp-sales-agent-prod
```

### Step 2: Create the "Management" Service Account

This is the service account that your application will run as to create other service accounts:

```bash
# Create the management service account
gcloud iam service-accounts create adcp-manager \
    --display-name="AdCP Service Account Manager" \
    --description="Service account used by AdCP Sales Agent to create partner service accounts"

# Get the email
export SA_EMAIL="adcp-manager@adcp-sales-agent-prod.iam.gserviceaccount.com"
echo "Management Service Account: $SA_EMAIL"
```

### Step 3: Grant IAM Permissions

The management service account needs permission to create other service accounts:

```bash
# Grant Service Account Admin role (to create service accounts)
gcloud projects add-iam-policy-binding adcp-sales-agent-prod \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountAdmin"

# Grant Service Account Key Admin role (to create service account keys)
gcloud projects add-iam-policy-binding adcp-sales-agent-prod \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountKeyAdmin"
```

**Why these roles?**
- `roles/iam.serviceAccountAdmin` - Allows creating and managing service accounts
- `roles/iam.serviceAccountKeyAdmin` - Allows creating service account keys

### Step 4: Generate Service Account Key

```bash
# Create a JSON key for the management service account
gcloud iam service-accounts keys create ~/adcp-manager-key.json \
    --iam-account=$SA_EMAIL

# The key is saved to ~/adcp-manager-key.json
```

**⚠️ IMPORTANT:** Keep this key secure! It has permission to create service accounts in your project.

### Step 5: Configure Fly.io

#### Set the GCP Project ID (in fly.toml)

Edit `fly.toml` and uncomment/set:

```toml
[env]
  # Other env vars...
  GCP_PROJECT_ID = "adcp-sales-agent-prod"  # Your actual project ID
```

Commit and push this change.

#### Set the Service Account Key (as Fly secret)

```bash
# Read the key file and set as Fly secret
fly secrets set GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat ~/adcp-manager-key.json)" \
    --app adcp-sales-agent

# Verify it was set (you won't see the value, just the name)
fly secrets list --app adcp-sales-agent
```

### Step 6: Deploy

```bash
# Deploy the application with the new configuration
fly deploy --app adcp-sales-agent
```

### Step 7: Verify Setup

Check the application logs to ensure credentials are loading:

```bash
fly logs --app adcp-sales-agent
```

Look for:
```
GCP credentials loaded from GOOGLE_APPLICATION_CREDENTIALS_JSON
```

### Step 8: Test the Feature

1. Log into Admin UI: https://sales-agent.scope3.com/
2. Navigate to **Tenant Settings** → **Ad Server**
3. Select **Google Ad Manager**
4. Scroll to **Service Account Integration**
5. Click **🔑 Create Service Account**
6. You should see a service account email created!

## Verification Checklist

- [ ] GCP project created/identified
- [ ] Management service account created
- [ ] IAM roles granted (serviceAccountAdmin + serviceAccountKeyAdmin)
- [ ] Service account key generated
- [ ] `GCP_PROJECT_ID` set in fly.toml
- [ ] `GOOGLE_APPLICATION_CREDENTIALS_JSON` set as Fly secret
- [ ] Application deployed
- [ ] Logs show credentials loaded
- [ ] Test service account creation works

## Troubleshooting

### Error: "GCP_PROJECT_ID not configured"
**Cause:** Environment variable not set in fly.toml

**Fix:**
```toml
[env]
  GCP_PROJECT_ID = "your-project-id"
```

### Error: "Permission denied" or "IAM API not enabled"
**Cause:** Missing IAM permissions or API not enabled

**Fix:**
```bash
# Enable IAM API
gcloud services enable iam.googleapis.com --project=adcp-sales-agent-prod

# Re-grant permissions
gcloud projects add-iam-policy-binding adcp-sales-agent-prod \
    --member="serviceAccount:adcp-manager@adcp-sales-agent-prod.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountAdmin"
```

### Error: "No explicit GCP credentials provided"
**Cause:** GOOGLE_APPLICATION_CREDENTIALS_JSON secret not set

**Fix:**
```bash
fly secrets set GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat ~/adcp-manager-key.json)" \
    --app adcp-sales-agent
```

### Verify What Service Account Is Being Used

```bash
# In the application, log the credentials
# The service account email will appear in logs when IAMClient is initialized

fly logs --app adcp-sales-agent | grep "service_account"
```

## Security Best Practices

1. **Rotate Keys Regularly**: Create new keys every 90 days
   ```bash
   # Create new key
   gcloud iam service-accounts keys create ~/new-key.json --iam-account=$SA_EMAIL

   # Update Fly secret
   fly secrets set GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat ~/new-key.json)"

   # Delete old key (get key ID from console)
   gcloud iam service-accounts keys delete KEY_ID --iam-account=$SA_EMAIL
   ```

2. **Least Privilege**: Only grant the minimum required roles

3. **Monitor Usage**: Check GCP IAM audit logs for service account creation activity

4. **Separate Projects**: Consider using a dedicated GCP project for service account creation

## Cost Considerations

- Service account creation is **free**
- Service account keys are **free**
- IAM API calls are **free** (within quota)
- No ongoing costs for this feature

## Alternative: Using Workload Identity (Advanced)

If you're running on GCP (not Fly.io), you can use Workload Identity instead of service account keys:

```bash
# This is more secure but only works on GCP environments
# Not applicable for Fly.io deployments
```

## Support

If you encounter issues:
1. Check Fly.io logs: `fly logs --app adcp-sales-agent`
2. Verify IAM permissions in GCP Console
3. Ensure IAM API is enabled in your project
4. Check that the service account key JSON is valid

## Summary

Once configured, the flow is:
1. Your app runs as the "management" service account (credentials in Fly secret)
2. When a partner clicks "Create Service Account" in Admin UI
3. Your app creates a new service account: `adcp-sales-tenant123@project.iam.gserviceaccount.com`
4. Partner adds that email to their GAM
5. Done! No credential sharing needed.
