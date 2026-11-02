variable "project" {
  description = "Name prefix for every resource, and the value of the Project tag on each."
  type        = string
  default     = "salesagent"
}

variable "region" {
  description = "AWS region for everything except Route53 (global)."
  type        = string
  default     = "eu-central-1"
}

variable "domain" {
  description = "The deployment domain (SALES_AGENT_DOMAIN). The apex serves signup and the Admin UI, admin.<domain> the Admin UI, creative.<domain> the creative agent, and every other <name>.<domain> a subdomain tenant."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone that holds var.domain. Terraform adds the apex, wildcard and certificate-validation records to it."
  type        = string
}

variable "custom_domains" {
  description = <<-EOT
    Tenant hosts outside var.domain, each served with its own ACM certificate on the ALB
    listener (SNI). Set route53_zone_id when the host is in a zone this account manages and
    Terraform creates the validation and alias records; leave it null for a domain someone
    else's DNS holds, and publish the records from the custom_domain_dns output there.
  EOT
  type = map(object({
    route53_zone_id = optional(string)
  }))
  default = {}
}

variable "app_image_tag" {
  description = "Tag of the sales agent image in the app ECR repository."
  type        = string
}

variable "creative_agent_image_tag" {
  description = "Tag of the reference creative agent image in its ECR repository (the ADCP_PIN it was built from)."
  type        = string
}

variable "app_cpu" {
  description = "Fargate CPU units for the sales agent task."
  type        = number
  default     = 1024
}

variable "app_memory" {
  description = "Fargate memory (MiB) for the sales agent task."
  type        = number
  default     = 2048
}

variable "creative_agent_cpu" {
  type    = number
  default = 512
}

variable "creative_agent_memory" {
  type    = number
  default = 1024
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "GiB of gp3 storage."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "db_deletion_protection" {
  description = "Set false only for a throwaway deployment."
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Set true only for a throwaway deployment."
  type        = bool
  default     = false
}

variable "super_admin_emails" {
  description = "Comma-separated SUPER_ADMIN_EMAILS."
  type        = string
  default     = ""
}

variable "support_email" {
  type    = string
  default = "support@example.com"
}

variable "app_environment" {
  description = "Extra plain environment variables for the sales agent (for example OAUTH_DISCOVERY_URL). Overrides the defaults in ecs.tf."
  type        = map(string)
  default     = {}
}

variable "app_secrets" {
  description = "Extra secret environment variables for the sales agent (for example OAUTH_CLIENT_SECRET, GEMINI_API_KEY). Stored in the app secret in Secrets Manager."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window on delete. 0 deletes immediately (throwaway deployments)."
  type        = number
  default     = 7
}

variable "ecr_force_delete" {
  description = "Let terraform destroy delete ECR repositories that still hold images."
  type        = bool
  default     = false
}
