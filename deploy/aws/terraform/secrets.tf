# Every secret the tasks read, generated here and stored in Secrets Manager. The task
# definitions name each JSON key; ECS injects them as environment variables at start.
#
# ENCRYPTION_KEY and the signing KEK decrypt what the application stores. Never let
# Terraform replace them on a live deployment: every encrypted value becomes unreadable.

resource "random_bytes" "encryption_key" {
  length = 32
}

resource "random_password" "flask_secret_key" {
  length  = 64
  special = false
}

resource "random_password" "signing_kek" {
  length  = 64
  special = false
}

resource "random_password" "creative_agent_token_secret" {
  length  = 48
  special = false
}

locals {
  db_host = aws_db_instance.main.address

  # Fernet wants 32 bytes in URL-safe base64; random_bytes gives standard base64.
  fernet_key = replace(replace(random_bytes.encryption_key.base64, "+", "-"), "/", "_")

  app_secret_values = merge(var.app_secrets, {
    # sslmode=require: RDS PostgreSQL 15+ refuses unencrypted connections (rds.force_ssl).
    DATABASE_URL           = "postgresql://salesagent:${random_password.db_app.result}@${local.db_host}:5432/salesagent?sslmode=require"
    ENCRYPTION_KEY         = local.fernet_key
    FLASK_SECRET_KEY       = random_password.flask_secret_key.result
    SALESAGENT_SIGNING_KEK = random_password.signing_kek.result
  })

  creative_agent_secret_values = {
    # The agent's pg client takes TLS from DATABASE_SSL, not from sslmode in the URL.
    DATABASE_URL                  = "postgresql://creative_agent:${random_password.db_creative_agent.result}@${local.db_host}:5432/adcp_registry"
    AGENT_TOKEN_ENCRYPTION_SECRET = random_password.creative_agent_token_secret.result
  }

  db_admin_secret_values = {
    PGHOST                     = local.db_host
    PGUSER                     = aws_db_instance.main.username
    PGPASSWORD                 = random_password.db_master.result
    APP_DB_PASSWORD            = random_password.db_app.result
    CREATIVE_AGENT_DB_PASSWORD = random_password.db_creative_agent.result
  }

  # A literal key set: for_each cannot take keys from a map holding sensitive values.
  secret_names = toset(["app", "creative-agent", "db-admin"])
  secrets = {
    app            = local.app_secret_values
    creative-agent = local.creative_agent_secret_values
    db-admin       = local.db_admin_secret_values
  }
}

resource "aws_secretsmanager_secret" "this" {
  for_each                = local.secret_names
  name                    = "${var.project}/${each.key}"
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each      = local.secret_names
  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = jsonencode(local.secrets[each.key])
}
