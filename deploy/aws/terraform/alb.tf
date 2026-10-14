# One ALB terminates TLS for every host:
#   - var.domain and *.var.domain on one ACM certificate (apex + wildcard), so a new
#     subdomain tenant needs no certificate and, with the wildcard DNS record below, no DNS;
#   - each custom tenant domain on its own ACM certificate, added to the listener by SNI.
# Host and path rules send creative.<domain>/api/creative-agent* to the creative agent and
# everything else to the sales agent, whose own nginx routes by Host from there.

resource "aws_lb" "main" {
  name               = var.project
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.subnet_ids

  # MCP's streamable HTTP holds a response open; the 60-second default cuts long calls.
  idle_timeout               = 300
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "app" {
  name                 = "${var.project}-app"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = data.aws_vpc.default.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 4
  }
}

resource "aws_lb_target_group" "creative_agent" {
  name                 = "${var.project}-creative"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = data.aws_vpc.default.id
  deregistration_delay = 30

  health_check {
    path                = "/api/creative-agent/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 4
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener_rule" "creative_agent" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    host_header {
      values = [local.creative_agent_host]
    }
  }
  condition {
    path_pattern {
      values = ["/api/creative-agent", "/api/creative-agent/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.creative_agent.arn
  }
}

# The rest of the creative agent (its registry UI, its dev-user session) stays unreachable.
resource "aws_lb_listener_rule" "creative_agent_other" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  condition {
    host_header {
      values = [local.creative_agent_host]
    }
  }

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

# The app's debug and internal routes are for test deployments; refuse them at the edge too.
resource "aws_lb_listener_rule" "internal_paths" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 30

  condition {
    path_pattern {
      values = ["/debug/*", "/_internal/*"]
    }
  }

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}
