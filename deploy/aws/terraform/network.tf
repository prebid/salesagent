# The default VPC's public subnets, and no NAT gateway: Fargate tasks get a public IP and
# reach ECR, Secrets Manager, CloudWatch and the internet directly. Security groups, not
# subnets, keep them closed: nothing reaches a task except from the ALB, and nothing
# reaches the database except from the tasks.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

locals {
  subnet_ids = sort(data.aws_subnets.default.ids)
}

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "Public HTTP and HTTPS to the load balancer"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "Redirected to HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "alb_to_creative_agent" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.creative_agent.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

# The tasks. Each one accepts its port from the ALB only, and sends HTTPS anywhere (ECR,
# Secrets Manager, CloudWatch, the creative agent through the ALB, buyers' webhooks,
# OAuth providers) plus PostgreSQL to the database.

resource "aws_security_group" "app" {
  name        = "${var.project}-app"
  description = "Sales agent tasks"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_security_group" "creative_agent" {
  name        = "${var.project}-creative-agent"
  description = "Reference creative agent tasks"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_security_group" "db_admin" {
  name        = "${var.project}-db-admin"
  description = "One-off task that creates the databases and roles"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "creative_agent_from_alb" {
  security_group_id            = aws_security_group.creative_agent.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

locals {
  task_security_groups = {
    app            = aws_security_group.app.id
    creative_agent = aws_security_group.creative_agent.id
    db_admin       = aws_security_group.db_admin.id
  }
}

resource "aws_vpc_security_group_egress_rule" "task_https" {
  for_each          = local.task_security_groups
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "task_to_db" {
  for_each                     = local.task_security_groups
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "PostgreSQL from the tasks only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_tasks" {
  for_each                     = local.task_security_groups
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
