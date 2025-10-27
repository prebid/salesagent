# Docker Cleanup for AdCP Tests

## The Problem

Running AdCP tests can accumulate Docker resources over time:

1. **E2E tests** create full Docker Compose stacks (~1.5GB per run)
2. **Conductor workspaces** create unique Docker images per workspace (`amman-adcp-server`, `monaco-adcp-server`, etc.)
3. **Integration tests** create per-test PostgreSQL databases (cleaned up in Python but leave Docker state)
4. **Dangling volumes** accumulate from incomplete test runs

**Impact**: 100GB+ of Docker resources can accumulate over weeks of development.

## The Solution

### Automatic Cleanup (Built-in)

**✅ Fixed in this PR:**
- E2E tests now clean up Docker resources automatically after each test session
- `run_all_tests.sh` prunes dangling volumes on teardown
- Only test containers are affected - your local dev environment is safe

### Manual Cleanup Script

Run the cleanup script periodically:

```bash
./scripts/cleanup_docker.sh
```

This removes:
- ✅ Stopped test containers (`adcp-test-*`)
- ✅ Dangling/unused volumes
- ✅ Old workspace images (keeps 2 most recent)
- ✅ Dangling intermediate images

**Safe to run**: Your local dev environment (`docker-compose up`) is not affected.

### What Gets Cleaned

| Resource Type | Pattern | Cleanup Method | Safe? |
|--------------|---------|----------------|-------|
| Test containers | `adcp-test-*` | `docker rm -f` | ✅ Yes |
| Test volumes | Dangling/unused | `docker volume prune -f` | ✅ Yes |
| Workspace images | `*-adcp-server` | Keep 2 newest, remove rest | ✅ Yes |
| Cache volumes | `adcp_global_*_cache` | Preserved (labeled) | ✅ Never removed |
| Dev containers | `docker-compose.yml` | Not touched | ✅ Never removed |

### Best Practices

**1. Prefer Quick Mode for Iteration**
```bash
./run_all_tests.sh quick   # No Docker (fast, ~1 min)
```

**2. Use CI Mode Sparingly**
```bash
./run_all_tests.sh ci      # Full Docker stack (~5 min)
```

**3. Run Cleanup Weekly**
```bash
./scripts/cleanup_docker.sh
```

**4. Check Docker Usage**
```bash
docker system df           # See what's using space
```

### Understanding Docker Usage

```bash
# See all AdCP-related images
docker images --filter "reference=*adcp*"

# See all test containers (running and stopped)
docker ps -a --filter "name=adcp-test-"

# See all volumes
docker volume ls --filter "name=adcp"

# Total Docker disk usage
docker system df
```

### When to Run Cleanup

**Weekly maintenance:**
- After heavy development sessions
- Before/after working on different Conductor workspaces
- When Docker disk usage is high (`docker system df`)

**Symptoms of accumulation:**
- Docker using 50GB+ (`docker system df`)
- Slow Docker operations
- "No space left on device" errors

## Technical Details

### Why This Happened

1. **E2E tests (`tests/e2e/conftest.py`):**
   - Cleanup code at end of `docker_services_e2e` was commented out
   - Each E2E test run left containers and volumes behind
   - **Fixed**: Added proper cleanup in fixture teardown

2. **Conductor workspaces:**
   - Each workspace (`amman`, `monaco`, etc.) builds its own Docker image
   - Images are 1.4-1.7GB each
   - **Fixed**: Cleanup script removes old workspace images (keeps 2 most recent)

3. **Integration tests:**
   - Create unique PostgreSQL databases (`test_a3f8d92c`) per test
   - Databases cleaned in Python, but Docker metadata accumulates
   - **Fixed**: Added `docker volume prune` to teardown

### Implementation

**E2E Test Cleanup (`tests/e2e/conftest.py`):**
```python
# After yield in docker_services_e2e fixture
if not use_existing_services:
    print("\n🧹 Cleaning up Docker resources...")
    subprocess.run(["docker-compose", "down", "-v"], capture_output=True)
    subprocess.run(["docker", "volume", "prune", "-f"], capture_output=True)
```

**Test Runner Cleanup (`run_all_tests.sh`):**
```bash
teardown_docker_stack() {
    docker-compose -p "$COMPOSE_PROJECT_NAME" down -v
    docker volume prune -f --filter "label!=preserve"
}
```

**Manual Cleanup Script (`scripts/cleanup_docker.sh`):**
- Removes test containers by name pattern
- Prunes dangling volumes (except labeled as `preserve`)
- Removes old workspace images (keeps 2 most recent)
- Shows before/after disk usage

### Prevention

**1. Quick mode for development:**
Uses integration_db fixture with real PostgreSQL but no Docker Compose overhead.

**2. CI mode only when necessary:**
Full Docker stack is needed for E2E tests but not for rapid iteration.

**3. Automatic cleanup:**
Tests now clean up after themselves - no manual intervention needed.

**4. Periodic maintenance:**
Run `./scripts/cleanup_docker.sh` weekly to remove orphaned resources.

## References

- Issue: 100GB of Docker resources accumulated from test runs
- Root cause: E2E test cleanup was disabled, workspace images accumulated
- Fix: [This PR] Re-enabled cleanup + added maintenance script
- Prevention: Use quick mode for iteration, CI mode for validation
