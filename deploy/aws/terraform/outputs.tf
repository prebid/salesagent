output "ecr_repositories" {
  description = "Push the two images here."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "alb_dns_name" {
  description = "CNAME target for a custom tenant domain."
  value       = aws_lb.main.dns_name
}

output "custom_domain_dns" {
  description = "The records a custom domain's owner publishes: the ACM validation record, and the host itself pointing at the ALB."
  value = { for d, c in aws_acm_certificate.custom : d => {
    validation = [for o in c.domain_validation_options : "${o.resource_record_name} ${o.resource_record_type} ${o.resource_record_value}"]
    host       = "${d}. CNAME ${aws_lb.main.dns_name}"
  } }
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "run_task_network_configuration" {
  description = "Pass to aws ecs run-task --network-configuration for a one-off task (db-admin uses the db_admin group)."
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = local.subnet_ids
      securityGroups = [aws_security_group.app.id]
      assignPublicIp = "ENABLED"
    }
  })
}

output "db_admin_network_configuration" {
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = local.subnet_ids
      securityGroups = [aws_security_group.db_admin.id]
      assignPublicIp = "ENABLED"
    }
  })
}

output "task_definitions" {
  value = {
    app            = aws_ecs_task_definition.app["app"].family
    app_task       = aws_ecs_task_definition.app["app-task"].family
    creative_agent = aws_ecs_task_definition.creative_agent.family
    db_admin       = aws_ecs_task_definition.db_admin.family
  }
}

output "region" {
  value = var.region
}

output "log_groups" {
  value = { for k, g in aws_cloudwatch_log_group.this : k => g.name }
}
