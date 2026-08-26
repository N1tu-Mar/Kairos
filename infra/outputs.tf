output "backend_url" {
  description = "Base URL of the deployed API. Set KAIROS_API_URL to this in the frontend host."
  value       = "${var.certificate_arn == "" ? "http" : "https"}://${aws_lb.backend.dns_name}"
}

output "ecr_repository_url" {
  description = "Push the backend image here (see infra/README.md)."
  value       = aws_ecr_repository.backend.repository_url
}

output "api_token_secret_arn" {
  description = "Secrets Manager ARN holding KAIROS_API_TOKEN. Read it once to configure the frontend host; never commit the value."
  value       = aws_secretsmanager_secret.api_token.arn
}

output "log_group" {
  description = "CloudWatch log group for the backend, structured run logs included."
  value       = aws_cloudwatch_log_group.backend.name
}
