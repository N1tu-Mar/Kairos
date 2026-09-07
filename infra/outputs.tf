# Outputs are printed on every apply and stored in state. Nothing secret
# goes here — not the API token, not a connection string, not anything that
# would be a credential if read aloud from a terminal in a shared room. The
# token's *ARN* is safe; reading the value behind it is a separate, audited
# call the operator makes deliberately.

output "environment" {
  description = "Which deployment this is. Printed first so a demo is never mistaken for production."
  value       = var.environment
}

output "backend_url" {
  description = "Base URL of the deployed API. Set KAIROS_API_URL to this in the frontend host."
  value       = "${local.backend_protocol}://${aws_lb.backend.dns_name}"
}

output "transport" {
  description = <<-EOT
    Whether the bearer token crosses TLS. "https" is the only acceptable
    answer for anything with a real founder behind it; "http (DEMO ONLY —
    the bearer token is in the clear)" says exactly what you are accepting.
  EOT
  value = (
    var.certificate_arn == ""
    ? "http (DEMO ONLY — the bearer token is in the clear)"
    : "https"
  )
}

output "bedrock_access" {
  description = "Whether the task role is scoped to specific models or holds a wildcard."
  value = (
    length(var.bedrock_model_arns) > 0
    ? "scoped to ${length(var.bedrock_model_arns)} model ARN(s)"
    : "WILDCARD — the task may invoke every model in the account"
  )
}

output "spend_cap" {
  description = "Whether the daily USD cap can actually fire, or is decorative because prices are zero."
  value = (
    tonumber(var.daily_usd_cap) <= 0
    ? "off (daily_usd_cap is 0; only the per-run token ceiling applies)"
    : (
      tonumber(var.price_reasoning_out_per_mtok) > 0 && tonumber(var.price_classify_out_per_mtok) > 0
      ? "enforced at $${var.daily_usd_cap}/day"
      : "UNENFORCEABLE — daily_usd_cap is set but token prices are 0, so every call costs $0.00"
    )
  )
}

output "ecr_repository_url" {
  description = "Push the backend image here (see infra/README.md)."
  value       = aws_ecr_repository.backend.repository_url
}

output "scheduler_token_secret_arn" {
  description = <<-EOT
    Secrets Manager ARN holding KAIROS_SCHEDULER_TOKEN. EventBridge reads
    this; the dashboard must not. The ARN, not the value:

      aws secretsmanager get-secret-value --secret-id <arn> \
        --query SecretString --output text
  EOT
  value       = aws_secretsmanager_secret.scheduler_token.arn
}

output "api_token_secret_arn" {
  description = <<-EOT
    Demo-only Secrets Manager ARN holding KAIROS_API_TOKEN. Empty in
    production — never place this value in Vercel on a real deployment.
  EOT
  value       = try(aws_secretsmanager_secret.api_token[0].arn, "")
}

output "log_group" {
  description = "CloudWatch log group for the backend, structured run logs included."
  value       = aws_cloudwatch_log_group.backend.name
}

output "alarm_topic_arn" {
  description = "SNS topic every alarm publishes to. Subscribe something to it or nothing pages."
  value       = aws_sns_topic.alarms.arn
}

output "alarm_subscription" {
  description = <<-EOT
    Whether anyone is actually subscribed. An email subscription stays
    unconfirmed until the recipient clicks the link, and Terraform reports
    it as created either way — so confirm it before believing you are
    covered.
  EOT
  value = (
    var.alarm_email == ""
    ? "NOBODY SUBSCRIBED — alarms fire into the topic and page no one"
    : "email pending confirmation: check the inbox and click the link"
  )
}

output "scheduler_dlq_url" {
  description = "Where a scheduled invocation lands when its retries are exhausted."
  value       = aws_sqs_queue.scheduler_dlq.url
}

output "task_networking" {
  description = "Whether the backend task is addressable from the internet."
  value = (
    local.task_public_ip
    ? "public IP (demo) — the task has an internet-routable address; only the ALB security group admits traffic"
    : "private subnets behind NAT — the task has no inbound path from the internet"
  )
}
