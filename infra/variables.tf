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

variable "image_tag" {
  description = "Tag of the backend image in ECR to deploy."
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

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on the ALB. Empty serves plain HTTP — demo only."
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
