#!/bin/bash
# setup_conductor_workspace.sh - Automated setup for Conductor workspaces

# Add .venv/bin to PATH if it exists and not already added
if [ -d ".venv/bin" ] && [[ ":$PATH:" != *":.venv/bin:"* ]]; then
    export PATH="$(pwd)/.venv/bin:$PATH"
    echo "✓ Added .venv/bin to PATH for this session"
fi

# Check if Conductor environment variables are set
if [ -z "$CONDUCTOR_WORKSPACE_NAME" ]; then
    echo "Error: This script should be run within a Conductor workspace"
    echo "CONDUCTOR_WORKSPACE_NAME is not set"
    exit 1
fi

echo "Setting up Conductor workspace: $CONDUCTOR_WORKSPACE_NAME"
echo "Workspace path: $CONDUCTOR_WORKSPACE_PATH"
echo "Root path: $CONDUCTOR_ROOT_PATH"

# Check environment variables
echo ""
echo "Checking environment variables..."
MISSING_VARS=0

# Check SUPER_ADMIN_EMAILS (required)
if [ -n "$SUPER_ADMIN_EMAILS" ]; then
    echo "✓ SUPER_ADMIN_EMAILS configured: $SUPER_ADMIN_EMAILS"
else
    echo "✗ SUPER_ADMIN_EMAILS is NOT set (REQUIRED for Admin UI access)"
    MISSING_VARS=$((MISSING_VARS + 1))
fi

# Check GEMINI_API_KEY (required)
if [ -n "$GEMINI_API_KEY" ]; then
    echo "✓ GEMINI_API_KEY configured"
else
    echo "✗ GEMINI_API_KEY is NOT set (REQUIRED for creative generation)"
    MISSING_VARS=$((MISSING_VARS + 1))
fi

# Check Google OAuth (required)
if [ -n "$GOOGLE_CLIENT_ID" ] && [ -n "$GOOGLE_CLIENT_SECRET" ]; then
    echo "✓ Google OAuth configured via environment variables"
elif [ -f "$CONDUCTOR_ROOT_PATH/client_secret"*.json ]; then
    echo "✓ Google OAuth configured via client_secret.json file"
else
    echo "✗ Google OAuth is NOT configured (REQUIRED for Admin UI login)"
    echo "  Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables"
    MISSING_VARS=$((MISSING_VARS + 1))
fi

# Check GAM OAuth (optional but required for GAM functionality)
if [ -n "$GAM_OAUTH_CLIENT_ID" ] && [ -n "$GAM_OAUTH_CLIENT_SECRET" ]; then
    echo "✓ GAM OAuth configured via environment variables"
else
    echo "⚠️  GAM OAuth is NOT configured (OPTIONAL - only needed for Google Ad Manager functionality)"
    echo "  Set GAM_OAUTH_CLIENT_ID and GAM_OAUTH_CLIENT_SECRET environment variables"
fi

# Check SUPER_ADMIN_DOMAINS (optional)
if [ -n "$SUPER_ADMIN_DOMAINS" ]; then
    echo "✓ SUPER_ADMIN_DOMAINS configured: $SUPER_ADMIN_DOMAINS"
fi

if [ $MISSING_VARS -gt 0 ]; then
    echo ""
    echo "⚠️  Warning: $MISSING_VARS required environment variable(s) missing!"
    echo ""
    echo "To fix this, add the following to your ~/.bashrc or ~/.zshrc:"
    echo ""
    echo "# AdCP Conductor Configuration"
    [ -z "$SUPER_ADMIN_EMAILS" ] && echo "export SUPER_ADMIN_EMAILS='your-email@example.com'"
    [ -z "$GEMINI_API_KEY" ] && echo "export GEMINI_API_KEY='your-gemini-api-key'"
    [ -z "$GOOGLE_CLIENT_ID" ] && [ ! -f "$CONDUCTOR_ROOT_PATH/client_secret"*.json ] && echo "export GOOGLE_CLIENT_ID='your-client-id.apps.googleusercontent.com'"
    [ -z "$GOOGLE_CLIENT_SECRET" ] && [ ! -f "$CONDUCTOR_ROOT_PATH/client_secret"*.json ] && echo "export GOOGLE_CLIENT_SECRET='your-client-secret'"
    [ -z "$GAM_OAUTH_CLIENT_ID" ] && echo "export GAM_OAUTH_CLIENT_ID='your-gam-client-id.apps.googleusercontent.com'"
    [ -z "$GAM_OAUTH_CLIENT_SECRET" ] && echo "export GAM_OAUTH_CLIENT_SECRET='your-gam-client-secret'"
    echo ""
    echo "The workspace will be created but may not function properly."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo ""

# Check if port management script exists
PORT_MANAGER="./manage_conductor_ports.py"
PORT_CONFIG="./conductor_ports.json"

if [ -f "$PORT_MANAGER" ] && [ -f "$PORT_CONFIG" ]; then
    echo "Using Conductor port reservation system..."

    # Reserve ports for this workspace
    PORT_RESULT=$(python3 "$PORT_MANAGER" reserve "$CONDUCTOR_WORKSPACE_NAME" 2>&1)

    if [ $? -eq 0 ]; then
        # Extract ports from the output
        POSTGRES_PORT=$(echo "$PORT_RESULT" | grep "PostgreSQL:" | awk '{print $2}')
        ADCP_PORT=$(echo "$PORT_RESULT" | grep "MCP Server:" | awk '{print $3}')
        ADMIN_PORT=$(echo "$PORT_RESULT" | grep "Admin UI:" | awk '{print $3}')

        echo "$PORT_RESULT"
    else
        echo "Failed to reserve ports: $PORT_RESULT"
        echo "Falling back to smart port assignment..."

        # Smart port assignment function
        find_available_admin_port() {
            # Try preferred admin ports first (8001-8004)
            for port in 8001 8002 8003 8004; do
                if ! netstat -an 2>/dev/null | grep -q ":$port "; then
                    echo $port
                    return
                fi
            done

            # If preferred ports are taken, use hash-based fallback
            WORKSPACE_HASH=$(echo -n "$CONDUCTOR_WORKSPACE_NAME" | cksum | cut -f1 -d' ')
            WORKSPACE_NUM=$((($WORKSPACE_HASH % 100) + 1))
            echo $((8001 + $WORKSPACE_NUM))
        }

        # Find available admin port
        ADMIN_PORT=$(find_available_admin_port)

        # Calculate other ports based on workspace hash
        WORKSPACE_HASH=$(echo -n "$CONDUCTOR_WORKSPACE_NAME" | cksum | cut -f1 -d' ')
        WORKSPACE_NUM=$((($WORKSPACE_HASH % 100) + 1))
        POSTGRES_PORT=$((5432 + $WORKSPACE_NUM))
        ADCP_PORT=$((8080 + $WORKSPACE_NUM))

        echo "Using smart port assignment:"
        echo "  PostgreSQL: $POSTGRES_PORT"
        echo "  MCP Server: $ADCP_PORT"
        echo "  Admin UI: $ADMIN_PORT"
    fi
else
    echo "Port reservation system not found, using smart port assignment..."

    # Smart port assignment function
    find_available_admin_port() {
        # Try preferred admin ports first (8001-8004)
        for port in 8001 8002 8003 8004; do
            if ! netstat -an 2>/dev/null | grep -q ":$port "; then
                echo $port
                return
            fi
        done

        # If preferred ports are taken, use hash-based fallback
        WORKSPACE_HASH=$(echo -n "$CONDUCTOR_WORKSPACE_NAME" | cksum | cut -f1 -d' ')
        WORKSPACE_NUM=$((($WORKSPACE_HASH % 100) + 1))
        echo $((8001 + $WORKSPACE_NUM))
    }

    # Find available admin port
    ADMIN_PORT=$(find_available_admin_port)

    # Calculate other ports based on workspace hash
    WORKSPACE_HASH=$(echo -n "$CONDUCTOR_WORKSPACE_NAME" | cksum | cut -f1 -d' ')
    WORKSPACE_NUM=$((($WORKSPACE_HASH % 100) + 1))
    POSTGRES_PORT=$((5432 + $WORKSPACE_NUM))
    ADCP_PORT=$((8080 + $WORKSPACE_NUM))

    echo "Using smart port assignment:"
    echo "  PostgreSQL: $POSTGRES_PORT"
    echo "  MCP Server: $ADCP_PORT"
    echo "  Admin UI: $ADMIN_PORT"
fi

# Set up Docker caching infrastructure
echo ""
echo "Setting up Docker caching..."

# Create shared cache volumes if they don't exist
if docker volume inspect adcp_global_pip_cache >/dev/null 2>&1; then
    echo "✓ Docker pip cache volume already exists"
else
    docker volume create adcp_global_pip_cache >/dev/null
    echo "✓ Created shared pip cache volume"
fi

if docker volume inspect adcp_global_uv_cache >/dev/null 2>&1; then
    echo "✓ Docker uv cache volume already exists"
else
    docker volume create adcp_global_uv_cache >/dev/null
    echo "✓ Created shared uv cache volume"
fi

# Copy required files from root workspace
echo ""
echo "Copying files from root workspace..."

# Create .env file with secrets from multiple sources
echo "Creating .env file with secrets and workspace configuration..."

# Start with a fresh .env file with workspace-specific settings
cat > .env << EOF
# Environment configuration for Conductor workspace: $CONDUCTOR_WORKSPACE_NAME
# Generated on $(date)

# Docker BuildKit Caching (enabled by default)
DOCKER_BUILDKIT=1
COMPOSE_DOCKER_CLI_BUILD=1
EOF

# Load secrets from .env.secrets file (check current dir first, then root)
SECRETS_FILE=""
if [ -f ".env.secrets" ]; then
    SECRETS_FILE=".env.secrets"
    echo "Loading secrets from current directory (.env.secrets)..."
elif [ -f "$CONDUCTOR_ROOT_PATH/.env.secrets" ]; then
    SECRETS_FILE="$CONDUCTOR_ROOT_PATH/.env.secrets"
    echo "Loading secrets from root directory ($CONDUCTOR_ROOT_PATH/.env.secrets)..."
fi

if [ -n "$SECRETS_FILE" ]; then
    echo "" >> .env
    echo "# Secrets from $SECRETS_FILE" >> .env
    cat "$SECRETS_FILE" >> .env
    echo "✓ Loaded secrets from $SECRETS_FILE"
else
    echo "No .env.secrets file found in current or root directory, using environment variables..."

    # Add secrets from environment variables
    cat >> .env << EOF

# API Keys (from environment)
GEMINI_API_KEY=${GEMINI_API_KEY:-}

# OAuth Configuration (from environment)
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}
SUPER_ADMIN_EMAILS=${SUPER_ADMIN_EMAILS:-}
SUPER_ADMIN_DOMAINS=${SUPER_ADMIN_DOMAINS:-}

# GAM OAuth Configuration (from environment)
GAM_OAUTH_CLIENT_ID=${GAM_OAUTH_CLIENT_ID:-}
GAM_OAUTH_CLIENT_SECRET=${GAM_OAUTH_CLIENT_SECRET:-}
EOF

    echo "✓ Created .env file with environment variables"
    echo "ℹ️  Consider creating a .env.secrets file in current directory or $CONDUCTOR_ROOT_PATH/.env.secrets for consistent secrets across workspaces"
fi

# Copy OAuth credentials file if it exists (legacy method)
oauth_files=$(ls $CONDUCTOR_ROOT_PATH/client_secret*.json 2>/dev/null)
if [ -n "$oauth_files" ]; then
    echo "ℹ️  Found OAuth credentials file (legacy method)"
    for file in $oauth_files; do
        cp "$file" .
        echo "   Copied $(basename $file)"
    done
fi

# Update .env with unique ports
echo "" >> .env
echo "# Server Ports (unique for Conductor workspace: $CONDUCTOR_WORKSPACE_NAME)" >> .env
echo "POSTGRES_PORT=$POSTGRES_PORT" >> .env
echo "ADCP_SALES_PORT=$ADCP_PORT" >> .env
echo "ADMIN_UI_PORT=$ADMIN_PORT" >> .env
echo "DATABASE_URL=postgresql://adcp_user:secure_password_change_me@localhost:$POSTGRES_PORT/adcp" >> .env
echo "" >> .env
echo "# OAuth Configuration (optional - admin UI will work without it)" >> .env
echo "# GOOGLE_CLIENT_ID=your-client-id-here" >> .env
echo "# GOOGLE_CLIENT_SECRET=your-client-secret-here" >> .env
echo "# SUPER_ADMIN_EMAILS=admin@example.com" >> .env

echo "✓ Updated .env with unique ports"

# Note: docker-compose.yml is not modified - ports are configured via .env file
echo "✓ Port configuration saved to .env file"

# Create docker-compose.override.yml for development hot reloading
cat > docker-compose.override.yml << 'EOF'
# Docker Compose override for development with hot reloading
# This file is automatically loaded by docker-compose

services:
  adcp-server:
    volumes:
      # Mount source code for hot reloading, excluding .venv
      - .:/app
      - /app/.venv
      - ./audit_logs:/app/audit_logs
      # Mount shared cache volumes for faster builds
      - adcp_global_pip_cache:/root/.cache/pip
      - adcp_global_uv_cache:/cache/uv
    environment:
      # Enable development mode
      PYTHONUNBUFFERED: 1
      FLASK_ENV: development
      WERKZEUG_RUN_MAIN: true
    command: ["python", "run_server.py"]

  admin-ui:
    volumes:
      # Mount source code for hot reloading, excluding .venv
      - .:/app
      - /app/.venv
      - ./audit_logs:/app/audit_logs
      # Mount shared cache volumes for faster builds
      - adcp_global_pip_cache:/root/.cache/pip
      - adcp_global_uv_cache:/cache/uv
    environment:
      # Enable Flask development mode with auto-reload
      FLASK_ENV: development
      FLASK_DEBUG: 1
      PYTHONUNBUFFERED: 1
      WERKZEUG_RUN_MAIN: true

# Reference external cache volumes (shared across all workspaces)
volumes:
  adcp_global_pip_cache:
    external: true
  adcp_global_uv_cache:
    external: true
EOF
echo "✓ Created docker-compose.override.yml for development"

# Fix database.py indentation issues if they exist
if grep -q "for p in principals_data:" database.py && ! grep -B1 "for p in principals_data:" database.py | grep -q "^    "; then
    echo "Fixing database.py indentation issues..."
    # This is a simplified fix - in production you'd want a more robust solution
    echo "✗ Warning: database.py may have indentation issues that need manual fixing"
fi

# Set up Git hooks for this workspace
echo "Setting up Git hooks..."

# Configure git to use worktree-specific hooks
echo "Configuring worktree-specific hooks..."

# Enable worktree config
git config extensions.worktreeconfig true

# Get the worktree's git directory
WORKTREE_GIT_DIR=$(git rev-parse --git-dir)
WORKTREE_HOOKS_DIR="$WORKTREE_GIT_DIR/hooks"
MAIN_HOOKS_DIR="$(git rev-parse --git-common-dir)/hooks"

# Create hooks directory if it doesn't exist
mkdir -p "$WORKTREE_HOOKS_DIR"

# Configure this worktree to use its own hooks directory
git config --worktree core.hooksPath "$WORKTREE_HOOKS_DIR"
echo "✓ Configured worktree to use hooks at: $WORKTREE_HOOKS_DIR"

# Install pre-commit if available
if command -v pre-commit &> /dev/null && [ -f .pre-commit-config.yaml ]; then
    echo "Installing pre-commit hooks..."

    # Pre-commit doesn't like custom hooks paths, so temporarily unset it
    git config --worktree --unset core.hooksPath 2>/dev/null
    pre-commit install >/dev/null 2>&1
    PRECOMMIT_RESULT=$?

    # Copy the pre-commit hook to our worktree hooks directory
    if [ $PRECOMMIT_RESULT -eq 0 ] && [ -f "$MAIN_HOOKS_DIR/pre-commit" ]; then
        cp "$MAIN_HOOKS_DIR/pre-commit" "$WORKTREE_HOOKS_DIR/pre-commit"
        echo "✓ Pre-commit hooks installed in worktree"
    else
        echo "✗ Warning: Failed to install pre-commit hooks"
        echo "  To install manually, run: pre-commit install"
    fi

    # Restore the worktree hooks path
    git config --worktree core.hooksPath "$WORKTREE_HOOKS_DIR"
else
    echo "✗ Warning: pre-commit not found or config missing"
    echo "  To install pre-commit: pip install pre-commit"
    echo "  Then run: pre-commit install"
fi

# Set up pre-push hook
if [ -f run_all_tests.sh ]; then
    echo "✓ Test runner script found (./run_all_tests.sh)"

    # Create/update pre-push hook
    cat > "$WORKTREE_HOOKS_DIR/pre-push" << 'EOF'
#!/bin/bash
# Pre-push hook that works correctly with git worktrees
# This hook runs tests before allowing a push to remote

echo "Running tests before push..."

# Get the actual working directory (handles both regular repos and worktrees)
WORK_DIR="$(git rev-parse --show-toplevel)"
cd "$WORK_DIR"

echo "Working directory: $WORK_DIR"

# Check if test runner exists in the worktree
if [ -f "./run_all_tests.sh" ]; then
    # Run quick tests
    ./run_all_tests.sh quick
    TEST_RESULT=$?

    if [ $TEST_RESULT -ne 0 ]; then
        echo ""
        echo "❌ Tests failed! Push aborted."
        echo ""
        echo "To run full test suite:"
        echo "  ./run_all_tests.sh"
        echo ""
        echo "To push anyway (not recommended):"
        echo "  git push --no-verify"
        echo ""
        exit 1
    else
        echo "✅ All tests passed! Proceeding with push..."
    fi
else
    echo "⚠️  Test runner not found at: $WORK_DIR/run_all_tests.sh"
    echo "   Tests cannot be run automatically."
    echo "   Consider running tests manually before pushing."
    # Don't block the push if test runner is missing
    exit 0
fi

exit 0
EOF
    chmod +x "$WORKTREE_HOOKS_DIR/pre-push"
    echo "✓ Pre-push hook installed in worktree"
else
    echo "✗ Warning: run_all_tests.sh not found"
    echo "  Tests won't run automatically before push"
fi

# Install UI test dependencies if pyproject.toml has ui-tests extra
if grep -q "ui-tests" pyproject.toml 2>/dev/null; then
    echo ""
    echo "Installing UI test dependencies..."
    if command -v uv &> /dev/null; then
        uv sync --extra ui-tests
        echo "✓ UI test dependencies installed"

        # Configure UI test environment
        if [ -d "ui_tests" ]; then
            echo "export ADMIN_UI_PORT=$ADMIN_PORT" >> .env
            echo "✓ UI tests configured for Admin UI port $ADMIN_PORT"
        fi
    else
        echo "✗ Warning: uv not found, skipping UI test setup"
    fi
fi

# Activate the workspace environment directly
echo ""
echo "Activating workspace environment..."

# Load environment variables from .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
    echo "✓ Loaded environment variables from .env"
fi

echo "✓ Workspace environment activated"

echo ""
echo "Setup complete! Next steps:"
echo "1. Review .env file and ensure GEMINI_API_KEY is set"
echo "2. Build and start services:"
echo "   docker compose build"
echo "   docker compose up -d"
echo ""
echo "Services will be available at:"
echo "  MCP Server: http://localhost:$ADCP_PORT/mcp/"
echo "  Admin UI: http://localhost:$ADMIN_PORT/"
echo "  PostgreSQL: localhost:$POSTGRES_PORT"
echo ""
echo "✓ Docker caching is enabled automatically for faster builds!"
echo "✓ Environment variables from .env are now active in this shell"
echo ""
echo "You can now run commands directly:"
echo "  a2a send http://localhost:8091 'Hello'"
echo "  pytest"
echo "  pre-commit run --all-files"
if [ -d "ui_tests" ]; then
    echo ""
    echo "UI Testing:"
    echo "  Run tests: cd ui_tests && uv run python -m pytest"
    echo "  Claude subagent: cd ui_tests/claude_subagent && ./run_subagent.sh"
fi
