#!/bin/bash
# Docker cleanup script for test resources
# Safe to run - only removes test containers/images/volumes, not your local dev environment

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧹 AdCP Test Docker Cleanup Script${NC}"
echo ""
echo "This script removes:"
echo "  • Stopped test containers (adcp-test-*)"
echo "  • Dangling/unused volumes"
echo "  • Old workspace images (keeps latest)"
echo ""
echo -e "${YELLOW}⚠️  Your local dev environment (docker-compose up) will NOT be affected${NC}"
echo ""

# Ask for confirmation
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo -e "${BLUE}Step 1: Stopping and removing test containers...${NC}"
# Remove all containers with adcp-test prefix
TEST_CONTAINERS=$(docker ps -a --filter "name=adcp-test-" --format "{{.ID}}" | wc -l | tr -d ' ')
if [ "$TEST_CONTAINERS" -gt 0 ]; then
    echo "Found $TEST_CONTAINERS test containers"
    docker ps -a --filter "name=adcp-test-" --format "{{.ID}}" | xargs -r docker rm -f 2>/dev/null || true
    echo -e "${GREEN}✓ Removed test containers${NC}"
else
    echo "No test containers found"
fi

echo ""
echo -e "${BLUE}Step 2: Removing dangling volumes...${NC}"
# Prune volumes not attached to any container
BEFORE_VOLUMES=$(docker volume ls -q | wc -l | tr -d ' ')
docker volume prune -f --filter "label!=preserve" 2>/dev/null || true
AFTER_VOLUMES=$(docker volume ls -q | wc -l | tr -d ' ')
REMOVED_VOLUMES=$((BEFORE_VOLUMES - AFTER_VOLUMES))
echo -e "${GREEN}✓ Removed $REMOVED_VOLUMES dangling volumes${NC}"

echo ""
echo -e "${BLUE}Step 3: Cleaning up old workspace images...${NC}"
# List all adcp-server images sorted by date
echo "Current workspace images:"
docker images --filter "reference=*-adcp-server" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

echo ""
echo "Keeping only the 2 most recent workspace images..."

# Get all workspace image IDs except the 2 most recent
OLD_IMAGES=$(docker images --filter "reference=*-adcp-server" --format "{{.ID}}" | tail -n +3)
if [ -n "$OLD_IMAGES" ]; then
    echo "$OLD_IMAGES" | xargs -r docker rmi -f 2>/dev/null || true
    echo -e "${GREEN}✓ Removed old workspace images${NC}"
else
    echo "No old workspace images to remove"
fi

echo ""
echo -e "${BLUE}Step 4: Pruning dangling images...${NC}"
# Remove dangling images (intermediate layers not used by any container)
docker image prune -f 2>/dev/null || true
echo -e "${GREEN}✓ Pruned dangling images${NC}"

echo ""
echo -e "${GREEN}✅ Cleanup complete!${NC}"
echo ""
echo "Current Docker usage:"
docker system df

echo ""
echo -e "${BLUE}💡 Tips to prevent accumulation:${NC}"
echo "  • Run this script weekly: ./scripts/cleanup_docker.sh"
echo "  • Use './run_all_tests.sh quick' for fast iteration (no Docker)"
echo "  • Use './run_all_tests.sh ci' only when needed (full Docker stack)"
echo "  • Docker volumes for dev (adcp_global_*_cache) are preserved"
