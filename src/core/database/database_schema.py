"""Database schema definitions for PostgreSQL.

NOTE: This file contains legacy schema definitions from before Alembic migrations.
The schema is now managed via Alembic migrations in alembic/versions/.

This file is kept for reference only and should not be used for database initialization.
Use Alembic migrations instead:
    uv run python scripts/ops/migrate.py
"""

SCHEMA_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    subdomain VARCHAR(100) UNIQUE NOT NULL,
    config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    billing_plan VARCHAR(50) DEFAULT 'standard',
    billing_contact VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS creative_formats (
    format_id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50),  -- NULL for standard formats, populated for custom formats
    name VARCHAR(255) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('display', 'video', 'audio', 'native')),
    description TEXT,
    width INTEGER,
    height INTEGER,
    duration_seconds INTEGER,
    max_file_size_kb INTEGER,
    specs JSONB NOT NULL,
    is_standard BOOLEAN DEFAULT TRUE,
    is_foundational BOOLEAN DEFAULT FALSE,  -- True for base formats that can be extended
    extends VARCHAR(50),  -- Reference to foundational format_id
    modifications JSONB,  -- JSON with modifications to base format
    source_url TEXT,  -- URL where format was discovered
    platform_config JSONB,  -- Platform-specific config (e.g., GAM creative template IDs)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (extends) REFERENCES creative_formats(format_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS products (
    tenant_id VARCHAR(50) NOT NULL,
    product_id VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    formats JSONB NOT NULL,
    targeting_template JSONB NOT NULL,
    delivery_type VARCHAR(50) NOT NULL,
    is_fixed_price BOOLEAN NOT NULL,
    cpm DECIMAL(10,2),
    price_guidance JSONB,
    is_custom BOOLEAN DEFAULT FALSE,
    expires_at TIMESTAMPTZ,
    countries JSONB,
    implementation_config JSONB,
    PRIMARY KEY (tenant_id, product_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS principals (
    tenant_id VARCHAR(50) NOT NULL,
    principal_id VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    platform_mappings JSONB NOT NULL,
    access_token VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, principal_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(50) PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'manager', 'viewer')),
    google_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_buys (
    media_buy_id VARCHAR(100) PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    principal_id VARCHAR(100) NOT NULL,
    order_name VARCHAR(255) NOT NULL,
    advertiser_name VARCHAR(255) NOT NULL,
    campaign_objective VARCHAR(100),
    kpi_goal VARCHAR(255),
    budget DECIMAL(15,2),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    approved_by VARCHAR(255),
    raw_request JSONB NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, principal_id) REFERENCES principals(tenant_id, principal_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id VARCHAR(100) PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    media_buy_id VARCHAR(100) NOT NULL,
    task_type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    assigned_to VARCHAR(255),
    due_date TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    completed_by VARCHAR(255),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (media_buy_id) REFERENCES media_buys(media_buy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    operation VARCHAR(100) NOT NULL,
    principal_name VARCHAR(255),
    principal_id VARCHAR(100),
    adapter_id VARCHAR(50),
    success BOOLEAN NOT NULL,
    error_message TEXT,
    details JSONB,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subdomain ON tenants(subdomain);
CREATE INDEX IF NOT EXISTS idx_products_tenant ON products(tenant_id);
CREATE INDEX IF NOT EXISTS idx_principals_tenant ON principals(tenant_id);
CREATE INDEX IF NOT EXISTS idx_principals_token ON principals(access_token);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
CREATE INDEX IF NOT EXISTS idx_media_buys_tenant ON media_buys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_media_buys_status ON media_buys(status);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tasks_media_buy ON tasks(media_buy_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);

CREATE TABLE IF NOT EXISTS superadmin_config (
    config_key VARCHAR(100) PRIMARY KEY,
    config_value TEXT,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by VARCHAR(255)
);
"""


def get_schema(db_type: str = "postgresql") -> str:
    """Get the PostgreSQL schema.

    Args:
        db_type: Database type (only 'postgresql' is supported)

    Returns:
        PostgreSQL schema SQL

    Raises:
        ValueError: If db_type is not 'postgresql'

    Note:
        This function is deprecated. Use Alembic migrations instead:
            uv run python scripts/ops/migrate.py
    """
    if db_type != "postgresql":
        raise ValueError(
            f"Unsupported database type: {db_type}. "
            "This codebase uses PostgreSQL exclusively. "
            "See CLAUDE.md for architecture details."
        )

    return SCHEMA_POSTGRESQL
