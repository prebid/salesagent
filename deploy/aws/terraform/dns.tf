# --- The deployment domain: apex + wildcard on one certificate, and one wildcard record.

resource "aws_acm_certificate" "main" {
  domain_name               = var.domain
  subject_alternative_names = ["*.${var.domain}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ACM validates a name and its wildcard through the same record, so one record covers both.
# Keyed by domain_name, which is known at plan time (the record name is not).
resource "aws_route53_record" "main_validation" {
  for_each = { for o in aws_acm_certificate.main.domain_validation_options : o.domain_name => o if o.domain_name == var.domain }

  zone_id = var.route53_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for r in aws_route53_record.main_validation : r.fqdn]
}

resource "aws_route53_record" "main" {
  for_each = toset([var.domain, "*.${var.domain}"])

  zone_id = var.route53_zone_id
  name    = each.key
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}

# --- Custom tenant domains: one certificate each, attached to the HTTPS listener by SNI.
# An ALB takes 25 such certificates by default (a quota you can raise to 100).

resource "aws_acm_certificate" "custom" {
  for_each          = var.custom_domains
  domain_name       = each.key
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

locals {
  managed_custom_domains = { for d, c in var.custom_domains : d => c if c.route53_zone_id != null }
}

resource "aws_route53_record" "custom_validation" {
  for_each = local.managed_custom_domains

  zone_id = each.value.route53_zone_id
  name    = one(aws_acm_certificate.custom[each.key].domain_validation_options).resource_record_name
  type    = one(aws_acm_certificate.custom[each.key].domain_validation_options).resource_record_type
  records = [one(aws_acm_certificate.custom[each.key].domain_validation_options).resource_record_value]
  ttl     = 300
}

resource "aws_route53_record" "custom" {
  for_each = local.managed_custom_domains

  zone_id = each.value.route53_zone_id
  name    = each.key
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}

# Waits until the certificate is issued. For a domain whose DNS someone else holds, that is
# after they publish the validation record from the custom_domain_dns output.
resource "aws_acm_certificate_validation" "custom" {
  for_each        = var.custom_domains
  certificate_arn = aws_acm_certificate.custom[each.key].arn

  timeouts {
    create = "2h"
  }

  depends_on = [aws_route53_record.custom_validation]
}

resource "aws_lb_listener_certificate" "custom" {
  for_each        = var.custom_domains
  listener_arn    = aws_lb_listener.https.arn
  certificate_arn = aws_acm_certificate_validation.custom[each.key].certificate_arn
}
