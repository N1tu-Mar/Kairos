# Kairos on AWS.
#
# One Fargate task runs the FastAPI backend and executes pipeline runs.
# SQLite and the run state live on EFS, which is why desired_count is 1 and
# must stay 1 until the storage story changes (infra/README.md).
# EventBridge Scheduler triggers the daily run by calling the same
# POST /founders/{id}/runs endpoint a person uses, authenticated with a
# scheduler-only bearer token. That credential cannot read profiles or
# trigger a demo-catalog run. Human traffic uses Supabase user JWTs.
#
# `var.environment` is the switch that separates a demo from a deployment.
# Production requires HTTPS, keeps the task off a public IP, and refuses to
# plan with a wildcard Bedrock grant. Every relaxation is a `demo` relaxation
# and is tagged as such, so nothing here is ambiguous about which one it is.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is deliberately not configured here — the bucket name is
  # account-specific and belongs in a backend config file the operator
  # supplies (`terraform init -backend-config=...`). What matters is that it
  # is configured *somewhere* before a real apply: this state file contains
  # the generated API token, and local state means that credential lives in
  # a file on somebody's laptop with no encryption and no locking.
  # infra/README.md has the bucket + DynamoDB lock table setup.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name       = "${var.project}-${var.environment}"
  production = var.environment == "production"

  # A demo deployment carries the word in every name, so a console full of
  # resources never leaves you guessing which one takes real traffic.
  is_demo = var.environment == "demo"
}

data "aws_caller_identity" "current" {}

# ── Guardrails ───────────────────────────────────────────────────────────────
#
# These fail `terraform plan`, not `terraform apply`. A misconfiguration
# caught in review costs a minute; the same one caught after apply has
# already put a credential on the wire.

resource "terraform_data" "production_requires_tls" {
  count = local.production ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.certificate_arn != ""
      error_message = <<-EOT
        environment = "production" requires certificate_arn.

        The API authenticates with a bearer token in an Authorization
        header. Over plain HTTP that credential is readable at every hop
        between the browser and the load balancer. Request a certificate:

          aws acm request-certificate --domain-name api.example.com \
            --validation-method DNS
      EOT
    }
  }
}

resource "terraform_data" "production_scopes_bedrock" {
  count = local.production ? 1 : 0

  lifecycle {
    precondition {
      condition     = length(var.bedrock_model_arns) > 0
      error_message = <<-EOT
        environment = "production" requires bedrock_model_arns.

        Without them the task role carries bedrock:InvokeModel on
        Resource = "*", which is permission to invoke every model in the
        account — including ones nobody has priced. See the variable's
        description for the discovery commands.
      EOT
    }
  }
}

resource "terraform_data" "production_requires_supabase" {
  count = local.production ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.supabase_issuer != ""
      error_message = <<-EOT
        environment = "production" requires supabase_issuer.

        Production authenticates humans with Supabase user JWTs
        (KAIROS_AUTH_MODE=supabase). A shared KAIROS_API_TOKEN in the
        frontend is how an unauthenticated visitor used to act as the
        founder. Set supabase_issuer to the project URL plus /auth/v1,
        for example https://abcdefghijklm.supabase.co/auth/v1.
      EOT
    }
  }
}

resource "terraform_data" "production_prices_its_tokens" {
  count = local.production ? 1 : 0

  lifecycle {
    precondition {
      condition = (
        tonumber(var.daily_usd_cap) <= 0 ||
        (tonumber(var.price_reasoning_out_per_mtok) > 0 &&
        tonumber(var.price_classify_out_per_mtok) > 0)
      )
      error_message = <<-EOT
        environment = "production" with daily_usd_cap > 0 requires real
        prices for both tiers.

        At price 0 every call costs $0.00, so the daily cap can never trip
        and the only live control is the per-run token ceiling. A cap that
        cannot fire is worse than no cap: it reads like a control in the
        dashboard while doing nothing. Set the price variables, or set
        daily_usd_cap = "0" to say out loud that the dollar cap is off.
      EOT
    }
  }
}

# ── Network ──────────────────────────────────────────────────────────────────
#
# Default VPC, because a bespoke one is not what makes this system safe and
# it is a lot of Terraform. What does matter is where the task sits: in
# production it has no public IP and reaches the internet through a NAT
# gateway, so nothing can address it except the load balancer.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Private subnets for the task in production. Created here rather than
# assumed, because the default VPC has none.
resource "aws_subnet" "private" {
  count = local.production ? 2 : 0

  vpc_id            = data.aws_vpc.default.id
  availability_zone = data.aws_availability_zones.available.names[count.index]
  # /24s carved out of the default VPC's 172.31.0.0/16, high enough not to
  # collide with the default /20 public subnets.
  cidr_block              = cidrsubnet(data.aws_vpc.default.cidr_block, 8, 200 + count.index)
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-private-${count.index}" }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_eip" "nat" {
  count  = local.production ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${local.name}-nat" }
}

resource "aws_nat_gateway" "main" {
  count = local.production ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  # The NAT itself must live in a public subnet.
  subnet_id = data.aws_subnets.default.ids[0]
  tags      = { Name = "${local.name}-nat" }
}

resource "aws_route_table" "private" {
  count  = local.production ? 1 : 0
  vpc_id = data.aws_vpc.default.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[0].id
  }

  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  count = local.production ? 2 : 0

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

locals {
  # Where the task runs. Private and IP-less in production; the default
  # public subnets in a demo, where there is no NAT gateway to pay for.
  task_subnet_ids  = local.production ? aws_subnet.private[*].id : data.aws_subnets.default.ids
  task_public_ip   = !local.production
  efs_subnet_ids   = local.production ? aws_subnet.private[*].id : data.aws_subnets.default.ids
  backend_protocol = var.certificate_arn == "" ? "http" : "https"
}

# ── Image registry ───────────────────────────────────────────────────────────

resource "aws_ecr_repository" "backend" {
  name = "${local.name}-backend"
  # Immutable in production: a tag that can be overwritten is a tag that can
  # silently change what a task restart deploys.
  image_tag_mutability = local.production ? "IMMUTABLE" : "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images; older ones are not rollback targets anyone uses."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

# ── Scheduler token: EventBridge only, never the dashboard ───────────────────
#
# Humans authenticate with Supabase JWTs. This secret is the one credential
# EventBridge holds, scoped in code to `run:trigger` for one founder. It
# must not be copied into Vercel.

resource "random_password" "scheduler_token" {
  length  = 43
  special = false
}

resource "aws_secretsmanager_secret" "scheduler_token" {
  name                    = "${local.name}/scheduler-token"
  recovery_window_in_days = local.production ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "scheduler_token" {
  secret_id     = aws_secretsmanager_secret.scheduler_token.id
  secret_string = random_password.scheduler_token.result

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Demo-only shared token for the laptop-like `local_shared` posture. Production
# does not create this secret and must not put any equivalent in Vercel.

resource "random_password" "api_token" {
  count   = local.production ? 0 : 1
  length  = 43
  special = false
}

resource "aws_secretsmanager_secret" "api_token" {
  count                   = local.production ? 0 : 1
  name                    = "${local.name}/api-token"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "api_token" {
  count         = local.production ? 0 : 1
  secret_id     = aws_secretsmanager_secret.api_token[0].id
  secret_string = random_password.api_token[0].result

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ── Persistent state: EFS for SQLite, the leases and the spend ledger ────────

resource "aws_efs_file_system" "state" {
  encrypted = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name = "${local.name}-state"
  }
}

# Deny anything that is not the task role, and deny unencrypted transport.
# Without this, any principal in the account that can reach the mount target
# can read the database.
resource "aws_efs_file_system_policy" "state" {
  file_system_id = aws_efs_file_system.state.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowOnlyTheTaskRole"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.task.arn }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
        ]
        Resource = aws_efs_file_system.state.arn
      },
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "*"
        Resource  = aws_efs_file_system.state.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

resource "aws_efs_backup_policy" "state" {
  file_system_id = aws_efs_file_system.state.id

  backup_policy {
    # A backup you never took is not a rollback plan. AWS Backup's default
    # EFS plan keeps daily recovery points for 35 days.
    status = local.production ? "ENABLED" : "DISABLED"
  }
}

resource "aws_security_group" "efs" {
  name        = "${local.name}-efs"
  description = "NFS from the Kairos task only."
  vpc_id      = data.aws_vpc.default.id

  # No egress rule at all: a file system does not initiate connections, and
  # an empty egress set is how you say that rather than inheriting the
  # allow-everything default.
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_service" {
  description                  = "NFS from the backend task"
  security_group_id            = aws_security_group.efs.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
}

resource "aws_efs_mount_target" "state" {
  # `count`, not `for_each`: in production the subnet ids do not exist until
  # apply, and `for_each` over unknown values cannot be planned. The number
  # of subnets is known either way, so an index is the addressable thing.
  count = length(local.efs_subnet_ids)

  file_system_id  = aws_efs_file_system.state.id
  subnet_id       = local.efs_subnet_ids[count.index]
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "state" {
  file_system_id = aws_efs_file_system.state.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/kairos"
    creation_info {
      owner_uid = 1000
      owner_gid = 1000
      # 700: the task's user and nobody else. It holds the database, the
      # spend ledger and the run leases.
      permissions = "700"
    }
  }
}

# ── Logs ─────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}-backend"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_kms_key" "logs" {
  description             = "Encrypts the ${local.name} backend log group."
  enable_key_rotation     = true
  deletion_window_in_days = local.production ? 30 : 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountRoot"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${local.name}-backend"
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${local.name}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

# ── IAM ──────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    # Without this, any ECS task in any account that guesses the role ARN can
    # assume it. The confused-deputy fix costs two lines.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# Pulls the image, writes logs, injects the token secret.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-auth-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = concat(
          [aws_secretsmanager_secret.scheduler_token.arn],
          aws_secretsmanager_secret.api_token[*].arn,
        )
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.logs.arn]
      },
    ]
  })
}

# What the running code may do: invoke the two models, mount its own EFS.
# Nothing else.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task_permissions" {
  name = "runtime"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        # Scoped the moment ARNs are supplied, which production requires.
        # A wildcard here is permission to invoke every model in the account,
        # including ones nobody has priced.
        Resource = length(var.bedrock_model_arns) > 0 ? var.bedrock_model_arns : ["*"]
      },
      {
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
        ]
        Resource = [aws_efs_file_system.state.arn]
        Condition = {
          StringEquals = {
            "elasticfilesystem:AccessPointArn" = aws_efs_access_point.state.arn
          }
        }
      },
    ]
  })
}

# ── ECS: cluster, task, single-task service ──────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = local.production ? "enabled" : "disabled"
  }
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "state"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.state.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.state.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "backend"
    image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    mountPoints = [{ sourceVolume = "state", containerPath = "/data" }]

    # Not root. UID 1000 matches the EFS access point's owner, which is what
    # lets the task write its own database through a mode-700 directory.
    user = "1000:1000"

    # readonlyRootFilesystem is deliberately NOT set. Fargate supports no
    # tmpfs mounts, so a read-only root means every scratch path — uv's
    # cache, Python's temp files, SQLite's journal and WAL — has to be on a
    # volume, and getting that wrong produces a task that starts and then
    # fails on its first write. It is worth doing and it is worth doing
    # against a real task, so it is written down in incomplete.md rather
    # than switched on here untested.

    linuxParameters = {
      initProcessEnabled = true
    }

    environment = [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "KAIROS_ENV", value = var.environment },
      { name = "KAIROS_AUTH_MODE", value = local.production ? "supabase" : "local_shared" },
      { name = "KAIROS_SUPABASE_ISSUER", value = var.supabase_issuer },
      { name = "KAIROS_SCHEDULER_FOUNDER_ID", value = var.founder_id },
      { name = "KAIROS_ENABLE_BROWSER", value = "false" },
      { name = "BEDROCK_MODEL_REASONING", value = var.bedrock_model_reasoning },
      { name = "BEDROCK_MODEL_CLASSIFY", value = var.bedrock_model_classify },
      { name = "KAIROS_DB_URL", value = "sqlite:////data/kairos.db" },
      { name = "KAIROS_STATE_DIR", value = "/data/state" },
      { name = "KAIROS_DAILY_USD_CAP", value = var.daily_usd_cap },
      { name = "KAIROS_PRICE_REASONING_IN_PER_MTOK", value = var.price_reasoning_in_per_mtok },
      { name = "KAIROS_PRICE_REASONING_OUT_PER_MTOK", value = var.price_reasoning_out_per_mtok },
      { name = "KAIROS_PRICE_CLASSIFY_IN_PER_MTOK", value = var.price_classify_in_per_mtok },
      { name = "KAIROS_PRICE_CLASSIFY_OUT_PER_MTOK", value = var.price_classify_out_per_mtok },
    ]

    # Read by ARN at container start, so rotating the secret's value and
    # restarting the task is the whole rotation procedure — no new ARN, no
    # Terraform apply, nothing else to update.
    secrets = concat(
      [
        {
          name      = "KAIROS_SCHEDULER_TOKEN"
          valueFrom = aws_secretsmanager_secret.scheduler_token.arn
        },
      ],
      local.production ? [] : [
        {
          name      = "KAIROS_API_TOKEN"
          valueFrom = aws_secretsmanager_secret.api_token[0].arn
        },
      ],
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.backend.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "backend"
      }
    }

    # Liveness from inside the container, so a wedged process is replaced
    # even while the ALB still has a passing target.
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }])
}

# The task and load-balancer groups reference each other, so their rules
# live outside the group resources. Inline blocks on both would be a
# dependency cycle Terraform cannot plan.
resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "Kairos backend task."
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  description                  = "API traffic from the load balancer only"
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

# Egress stays open: the task calls Bedrock, Grants.gov, Secrets Manager and
# CloudWatch, and pinning those to prefix lists is a maintenance burden that
# buys little when nothing can reach *in* except the ALB.
resource "aws_vpc_security_group_egress_rule" "service_out" {
  description       = "Bedrock, Grants.gov, Secrets Manager, CloudWatch"
  security_group_id = aws_security_group.service.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  launch_type     = "FARGATE"

  # SQLite on EFS is single-writer in practice. One task, and a deploy stops
  # the old task before starting the new one, for the same reason. The run
  # lease (agent/scheduler.py) is the second line of defence, not the first.
  desired_count                      = 1
  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0

  # Give the container time to come up before the ALB starts judging it.
  health_check_grace_period_seconds = 60

  enable_execute_command = !local.production

  network_configuration {
    subnets         = local.task_subnet_ids
    security_groups = [aws_security_group.service.id]
    # Public IP only in a demo, where there is no NAT gateway. In production
    # the task is unaddressable from the internet.
    assign_public_ip = local.task_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# ── ALB ──────────────────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point for the Kairos API."
  vpc_id      = data.aws_vpc.default.id
}

# Port 80 exists in production only to redirect. The listener sends a 301 and
# never forwards, so nothing authenticated crosses it.
resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  description       = "HTTP (redirects to HTTPS in production)"
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count = var.certificate_arn == "" ? 0 : 1

  description       = "HTTPS"
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# The load balancer talks to the task and to nothing else — not to the
# internet, not to the rest of the VPC.
resource "aws_vpc_security_group_egress_rule" "alb_to_service" {
  description                  = "To the backend task"
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_lb" "backend" {
  name               = "${local.name}-backend"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids

  drop_invalid_header_fields = true
  enable_deletion_protection = local.production
}

resource "aws_lb_target_group" "backend" {
  name        = "${local.name}-backend"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    # /ready, not /health: the ALB should stop sending traffic to a task
    # whose database is unreachable, and /health deliberately cannot tell.
    # Both are exempt from the bearer-token gate for exactly this.
    path                = "/ready"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.backend.arn
  port              = 80
  protocol          = "HTTP"

  # With a certificate, port 80 only ever redirects — no authenticated
  # request crosses it. Without one, it serves, which is a demo-only posture
  # and is why production's precondition requires a certificate.
  default_action {
    type = var.certificate_arn == "" ? "forward" : "redirect"

    target_group_arn = var.certificate_arn == "" ? aws_lb_target_group.backend.arn : null

    dynamic "redirect" {
      for_each = var.certificate_arn == "" ? [] : [1]
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.backend.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

# ── Alarms ───────────────────────────────────────────────────────────────────
#
# Failure reporting used to stop at CloudWatch logs, which means it stopped
# at "somebody thought to look". These fire.

resource "aws_sns_topic" "alarms" {
  name              = "${local.name}-alarms"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "no_healthy_task" {
  alarm_name          = "${local.name}-no-healthy-task"
  alarm_description   = "The API has no healthy target. Nothing is serving and no run can start."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  # Missing data means the load balancer is reporting nothing, which is not
  # a reason to assume everything is fine.
  treat_missing_data = "breaching"

  dimensions = {
    LoadBalancer = aws_lb.backend.arn_suffix
    TargetGroup  = aws_lb_target_group.backend.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "scheduled_run_failed" {
  alarm_name          = "${local.name}-scheduled-run-failed"
  alarm_description   = <<-EOT
    An EventBridge invocation of the run endpoint failed after its retry.
    Nothing was searched last night — this is the alarm that separates
    "Kairos was quiet" from "Kairos has been broken for four days".
  EOT
  namespace           = "AWS/Events"
  metric_name         = "InvocationsFailedToBeSentToDlq"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    RuleName = aws_scheduler_schedule.daily_run.name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "dead_letter_received" {
  alarm_name          = "${local.name}-dead-letter-received"
  alarm_description   = "A scheduled invocation exhausted its retries and landed in the DLQ."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.scheduler_dlq.name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "run_halted" {
  alarm_name        = "${local.name}-run-halted"
  alarm_description = <<-EOT
    A run halted on a cap or a throttle. Not an outage — a halted run is a
    reported, designed outcome — but repeated halts mean the caps are wrong
    or something upstream is failing, and both deserve a human.
  EOT

  metric_name         = "RunsHalted"
  namespace           = "Kairos"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 2
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_log_metric_filter" "run_halted" {
  name           = "${local.name}-run-halted"
  log_group_name = aws_cloudwatch_log_group.backend.name
  pattern        = "run_halted"

  metric_transformation {
    name      = "RunsHalted"
    namespace = "Kairos"
    value     = "1"
    # Absence of halts must read as zero, not as missing data.
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "task_errors" {
  alarm_name          = "${local.name}-backend-5xx"
  alarm_description   = "The API is returning 5xx. Something in the request path is broken."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.backend.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# ── The schedule: the loop that runs while the founder is asleep ─────────────
#
# EventBridge Scheduler → API destination → POST /founders/{id}/runs, with
# the same Authorization header the dashboard's proxy sends. One code path
# into a run, whoever asks for it.

resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "${local.name}-scheduler-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "scheduler_dlq" {
  queue_url = aws_sqs_queue.scheduler_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.scheduler_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/default/${local.name}-daily-run"
          }
        }
      },
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "*"
        Resource  = aws_sqs_queue.scheduler_dlq.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_event_connection" "backend" {
  name               = "${local.name}-backend"
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      key   = "Authorization"
      value = "Bearer ${random_password.scheduler_token.result}"
    }
  }

  lifecycle {
    # Same reason as the secret version: rotation happens outside Terraform,
    # and an apply must not reset the header back to the value in state.
    ignore_changes = [auth_parameters]
  }
}

resource "aws_cloudwatch_event_api_destination" "trigger_run" {
  name                             = "${local.name}-trigger-run"
  connection_arn                   = aws_cloudwatch_event_connection.backend.arn
  http_method                      = "POST"
  invocation_endpoint              = "${local.backend_protocol}://${aws_lb.backend.dns_name}/founders/${var.founder_id}/runs"
  invocation_rate_limit_per_second = 1
}

resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-api-destination"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["events:InvokeApiDestination"]
        Resource = [aws_cloudwatch_event_api_destination.trigger_run.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.scheduler_dlq.arn]
      },
    ]
  })
}

resource "aws_scheduler_schedule" "daily_run" {
  name                = "${local.name}-daily-run"
  schedule_expression = var.run_schedule

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_cloudwatch_event_api_destination.trigger_run.arn
    role_arn = aws_iam_role.scheduler.arn

    # The idempotency key makes a retry resolve to the *same* logical run
    # rather than starting a second one — the run lease would refuse the
    # second anyway, but a 409 is a worse answer than "here is your job".
    # <aws.scheduler.execution-id> is substituted by the scheduler.
    input = jsonencode({
      use_demo_catalog   = false
      include_grants_gov = true
      source             = "scheduled"
      idempotency_key    = "<aws.scheduler.execution-id>"
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }

    dead_letter_config {
      # Where an invocation goes when the retries are exhausted. Without
      # this, a failed schedule is a log line nobody reads.
      arn = aws_sqs_queue.scheduler_dlq.arn
    }
  }
}
