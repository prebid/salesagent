# Docker Hub Rate Limit Solution for Fly.io Deployment

## Problem
Fly.io deployments fail with Docker Hub rate limit errors:
```
429 Too Many Requests - Server message: toomanyrequests: You have reached your unauthenticated pull rate limit.
```

## Implemented Solution ✅
**Use AWS Public ECR instead of Docker Hub.**

The `Dockerfile.fly` now uses:
```dockerfile
FROM public.ecr.aws/docker/library/python:3.12-slim
```

**Benefits:**
- ✅ No rate limits for public images
- ✅ No authentication required
- ✅ Mirrors official Docker images (same SHA)
- ✅ No configuration needed in Fly.io
- ✅ High availability and fast pulls

AWS Public ECR provides a mirror of Docker's official images without rate limiting or authentication requirements. This is the simplest and most reliable solution for Fly.io deployments.

## Alternative: Docker Hub Authentication (Advanced)
If you specifically need Docker Hub, you can configure authentication (note: this is more complex and requires additional setup).

## Setup Steps

### 1. Create Docker Hub Access Token
1. Log in to [Docker Hub](https://hub.docker.com/)
2. Go to Account Settings → Security → Access Tokens
3. Click "New Access Token"
4. Name it "Fly.io Deployment"
5. Copy the token (you'll only see it once)

### 2. Configure Fly.io Secrets
Set your Docker Hub credentials as Fly.io secrets:

```bash
fly secrets set DOCKER_HUB_USERNAME="your-dockerhub-username" --app adcp-sales-agent
fly secrets set DOCKER_HUB_TOKEN="your-token-here" --app adcp-sales-agent
```

### 3. Verify Configuration
Check that secrets are set:

```bash
fly secrets list --app adcp-sales-agent
```

### 4. Deploy
The next deployment will automatically use these credentials:

```bash
fly deploy --app adcp-sales-agent
```

## Alternative: GitHub Actions Authentication
If deploying via GitHub Actions, add Docker Hub credentials to repository secrets:
1. Go to repository Settings → Secrets and variables → Actions
2. Add `DOCKER_HUB_USERNAME` and `DOCKER_HUB_TOKEN`
3. Update workflow to use these secrets

## Testing Locally
To test Docker builds locally without hitting rate limits:

```bash
# Login to Docker Hub
docker login

# Build the image
docker build -f Dockerfile.fly -t adcp-sales-agent .
```

## Troubleshooting

### Still Getting Rate Limit Errors?
- Verify credentials are correct: `fly secrets list --app adcp-sales-agent`
- Check Docker Hub account status at https://hub.docker.com/
- Consider upgrading Docker Hub plan if needed

### Need to Update Token?
```bash
fly secrets set DOCKER_HUB_TOKEN="new-token-here" --app adcp-sales-agent
```

### Remove Authentication
```bash
fly secrets unset DOCKER_HUB_USERNAME DOCKER_HUB_TOKEN --app adcp-sales-agent
```

## Docker Hub Rate Limits
- **Unauthenticated**: 100 pulls per 6 hours per IP
- **Free account**: 200 pulls per 6 hours
- **Pro account**: Unlimited pulls

For production deployments, authentication is strongly recommended.
