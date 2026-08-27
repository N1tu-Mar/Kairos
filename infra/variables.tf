variable "aws_region" {
  description = "Region for everything, including Bedrock model invocation."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix on every resource."
  type        = string
  default     = "kairos"
}

variable "environment" {
  description = <<-EOT
    Which deployment this is. Drives every production-safe default:
    "production" requires HTTPS, keeps the task off a public IP, and refuses
    to plan without a certificate. "demo" relaxes those and labels every
    resource so nobody mistakes one for the other.

    There is no shared default on purpose — naming the environment is the
    one decision that should never be inherited from whatever was in the
    shell's history.
  EOT
  type        = string

  validation {
    condition     = contains(["production", "demo"], var.environment)
    error_message = "environment must be exactly \"production\" or \"demo\"."
  }
}

variable "image_tag" {
  description = <<-EOT
    Tag of the backend image in ECR to deploy. Use an immutable tag — a
    digest or a commit SHA. "latest" is a moving target, which means a task
    restart six weeks from now silently deploys whatever was pushed since.
  EOT
  type        = string
  default     = "latest"
}

variable "founder_id" {
  description = "The founder the scheduled run executes for. Single-founder by design."
  type        = string
  default     = "founder_demo"
}

variable "run_schedule" {
  description = "EventBridge Scheduler cron. Default: 07:00 UTC daily, before a founder's morning."
  type        = string
  default     = "cron(0 7 * * ? *)"
}

variable "bedrock_model_reasoning" {
  description = "Bedrock model ID for the Assessor/Drafter tier. Discover with the AWS CLI; never guessed (agent/config.py)."
  type        = string
}

variable "bedrock_model_classify" {
  description = "Bedrock model ID for the classification tier."
  type        = string
}

variable "bedrock_model_arns" {
  description = <<-EOT
    Exact ARNs the task may invoke. Supplying them replaces the
    Resource = "*" Bedrock grant with a scoped one, which is the difference
    between "may call Bedrock" and "may call these two models".

    Both foundation-model and inference-profile ARNs belong here when the
    models are reached through a profile. Discover them:

      aws bedrock list-foundation-models --region us-east-1 \
        --query 'modelSummaries[].modelArn'
      aws bedrock list-inference-profiles --region us-east-1 \
        --query 'inferenceProfileSummaries[].inferenceProfileArn'

    Empty keeps the wildcard, which is refused in production (see the
    precondition in main.tf).
  EOT
  type        = list(string)
  default     = []
}

variable "certificate_arn" {
  description = <<-EOT
    ACM certificate for HTTPS on the ALB. Required when environment is
    "production": the bearer token is in an Authorization header, and an
    Authorization header over plain HTTP is a credential shouted across
    every hop between the browser and the load balancer.
  EOT
  type        = string
  default     = ""
}

variable "allowed_frontend_origin" {
  description = "Origin of the deployed dashboard, for documentation next to the CORS list in api/main.py."
  type        = string
  default     = "https://kairos.vercel.app"
}

variable "daily_usd_cap" {
  description = "KAIROS_DAILY_USD_CAP inside the task."
  type        = string
  default     = "3.0"
}

variable "price_reasoning_in_per_mtok" {
  description = <<-EOT
    USD per 1M input tokens for the reasoning tier. Confirm against the live
    Bedrock pricing page for your region — there is deliberately no default
    price table anywhere in this repository, because a stale guess
    under-counts spend against a real cap.

    Left at "0" every call costs $0.00, so daily_usd_cap can never trip and
    only the token ceiling is doing work. /ready reports that as
    spend_cap: unenforceable in production.
  EOT
  type        = string
  default     = "0"
}

variable "price_reasoning_out_per_mtok" {
  description = "USD per 1M output tokens, reasoning tier. See price_reasoning_in_per_mtok."
  type        = string
  default     = "0"
}

variable "price_classify_in_per_mtok" {
  description = "USD per 1M input tokens, classification tier."
  type        = string
  default     = "0"
}

variable "price_classify_out_per_mtok" {
  description = "USD per 1M output tokens, classification tier."
  type        = string
  default     = "0"
}

variable "log_retention_days" {
  description = <<-EOT
    CloudWatch retention for the backend log group. Structured run logs are
    the operational record of what the agent decided, so this is a real
    retention decision rather than a cost knob. 0 means never expire, which
    Terraform will accept and your bill will notice.
  EOT
  type        = number
  default     = 90
}

variable "alarm_email" {
  description = <<-EOT
    Address subscribed to the alarm topic. Empty creates the topic and the
    alarms but subscribes nobody — the alarms still fire and are still
    visible in the console, they just page no one.

    A subscription created this way is unconfirmed until the recipient
    clicks the link in the confirmation email. Terraform reports it as
    created either way, so confirm it before believing you are covered.
  EOT
  type        = string
  default     = ""
}

variable "task_cpu" {
  description = "Fargate CPU units."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate memory, MiB."
  type        = number
  default     = 1024
}
