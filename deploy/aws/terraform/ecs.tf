resource "aws_ecr_repository" "this" {
  for_each             = toset(["app", "creative-agent"])
  name                 = "${var.project}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(["app", "creative-agent", "db-admin"])
  name              = "/ecs/${var.project}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = var.project

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

# --- IAM: the execution role pulls images, writes logs and reads the three secrets. The
# applications themselves call no AWS API, so the tasks get no task role.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.project}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.this : s.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# --- Task definitions

locals {
  app_url              = "https://${var.domain}"
  admin_host           = "admin.${var.domain}"
  creative_agent_host  = "creative.${var.domain}"
  creative_agent_url   = "https://${local.creative_agent_host}/api/creative-agent"
  creative_agent_image = "${aws_ecr_repository.this["creative-agent"].repository_url}:${var.creative_agent_image_tag}"
  app_image            = "${aws_ecr_repository.this["app"].repository_url}:${var.app_image_tag}"

  app_environment = merge({
    ENVIRONMENT        = "production"
    ADCP_TESTING       = "false"
    ADCP_MULTI_TENANT  = "true"
    SALES_AGENT_DOMAIN = var.domain
    ADMIN_DOMAIN       = local.admin_host
    ADMIN_UI_URL       = "${local.app_url}/admin"
    ALLOWED_ORIGINS    = "${local.app_url},https://${local.admin_host}"
    SUPPORT_EMAIL      = var.support_email
    SUPER_ADMIN_EMAILS = var.super_admin_emails
    # Formats come from the self-hosted reference agent below, not the public one.
    CREATIVE_AGENT_URL = local.creative_agent_url
    # The name of the variable holding the KEK that encrypts tenants' signing keys.
    ADCP_SIGNING_KEY_PASSPHRASE_ENV = "SALESAGENT_SIGNING_KEK"
  }, var.app_environment)

  app_secret_keys = concat(
    ["DATABASE_URL", "ENCRYPTION_KEY", "FLASK_SECRET_KEY", "SALESAGENT_SIGNING_KEK"],
    nonsensitive(keys(var.app_secrets)),
  )

  log_config = { for name, group in aws_cloudwatch_log_group.this : name => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = group.name
      awslogs-region        = var.region
      awslogs-stream-prefix = name
    }
  } }

  # Creates both roles and both databases; safe to re-run (it also re-applies the passwords).
  db_admin_script = <<-EOT
    set -eu
    psql -v ON_ERROR_STOP=1 -v app_pw="$APP_DB_PASSWORD" -v ca_pw="$CREATIVE_AGENT_DB_PASSWORD" <<'SQL'
    SELECT 'CREATE ROLE salesagent LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'salesagent')\gexec
    SELECT 'CREATE ROLE creative_agent LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'creative_agent')\gexec
    ALTER ROLE salesagent PASSWORD :'app_pw';
    ALTER ROLE creative_agent PASSWORD :'ca_pw';
    GRANT salesagent, creative_agent TO CURRENT_USER;
    SELECT 'CREATE DATABASE salesagent OWNER salesagent' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'salesagent')\gexec
    SELECT 'CREATE DATABASE adcp_registry OWNER creative_agent' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'adcp_registry')\gexec
    REVOKE CONNECT ON DATABASE salesagent, adcp_registry FROM PUBLIC;
    SQL
    echo "databases ready"
  EOT
}

# The sales agent container, shared by the service and by one-off tasks.
locals {
  app_container = {
    name        = "app"
    image       = local.app_image
    essential   = true
    environment = [for k, v in local.app_environment : { name = k, value = v }]
    secrets = [for k in local.app_secret_keys : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.this["app"].arn}:${k}::"
    }]
    logConfiguration = local.log_config["app"]
  }

  app_task_definitions = {
    # The service: the image's entrypoint (migrations, then MCP, A2A, Admin, nginx, cron).
    app = merge(local.app_container, {
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }
    })
    # One-off commands (tenant setup, scripts/ops/*). run-task can override the command
    # but not the entrypoint, and the image's entrypoint starts the whole server.
    app-task = merge(local.app_container, {
      entryPoint = ["/usr/bin/env"]
      command    = ["python", "scripts/ops/check_tenants.py"]
    })
  }
}

resource "aws_ecs_task_definition" "app" {
  for_each                 = local.app_task_definitions
  family                   = "${var.project}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([each.value])
}

resource "aws_ecs_task_definition" "creative_agent" {
  family                   = "${var.project}-creative-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.creative_agent_cpu
  memory                   = var.creative_agent_memory
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "creative-agent"
    image        = local.creative_agent_image
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [for k, v in {
      # Since adcp v3.1.1 the agent refuses DEV_USER_* under NODE_ENV=production, and
      # without them it needs WorkOS. The ALB exposes only /api/creative-agent*.
      NODE_ENV                         = "development"
      PORT                             = "8080"
      RUN_MIGRATIONS                   = "true"
      DATABASE_SSL                     = "true"
      DATABASE_SSL_REJECT_UNAUTHORIZED = "false"
      DEV_USER_EMAIL                   = var.support_email
      DEV_USER_ID                      = "ops"
      WORKOS_API_KEY                   = "sk_test_unused"
      WORKOS_CLIENT_ID                 = "client_unused"
    } : { name = k, value = v }]
    secrets = [for k in ["DATABASE_URL", "AGENT_TOKEN_ENCRYPTION_SECRET"] : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.this["creative-agent"].arn}:${k}::"
    }]
    healthCheck = {
      command     = ["CMD", "node", "-e", "require('http').get('http://localhost:8080/api/creative-agent/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 120
    }
    logConfiguration = local.log_config["creative-agent"]
  }])
}

resource "aws_ecs_task_definition" "db_admin" {
  family                   = "${var.project}-db-admin"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([{
    name        = "db-admin"
    image       = "public.ecr.aws/docker/library/postgres:17-alpine"
    essential   = true
    command     = ["sh", "-c", local.db_admin_script]
    environment = [{ name = "PGSSLMODE", value = "require" }, { name = "PGDATABASE", value = "postgres" }]
    secrets = [for k in keys(local.db_admin_secret_values) : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.this["db-admin"].arn}:${k}::"
    }]
    logConfiguration = local.log_config["db-admin"]
  }])
}

# --- Services

resource "aws_ecs_service" "creative_agent" {
  name            = "creative-agent"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.creative_agent.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 180

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.creative_agent.id]
    assign_public_ip = true # no NAT gateway: the public IP is the way out
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.creative_agent.arn
    container_name   = "creative-agent"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "app" {
  name            = "app"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app["app"].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Migrations run when the container starts; a cold database takes a few minutes.
  health_check_grace_period_seconds = 600

  # One task at a time: migrations must not run from two containers at once.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https]
}
