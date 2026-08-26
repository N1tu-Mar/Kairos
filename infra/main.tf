# Kairos on AWS.
#
# One Fargate task runs the FastAPI backend and executes pipeline runs.
# SQLite and the daily spend ledger live on EFS, which is why desired_count
# is 1 and must stay 1 until the storage story changes (infra/README.md).
# EventBridge Scheduler triggers the daily run by calling the same
# POST /founders/{id}/runs endpoint a person uses, authenticated with the
# same bearer token, so there is exactly one code path into a run.

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
}

provider "aws" {
  region = var.aws_region
}

locals {
  name = var.project
}

# ── Network: default VPC, kept deliberately boring ───────────────────────────

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ── Image registry ───────────────────────────────────────────────────────────

resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ── The API token: generated here, read by both the task and the scheduler ──

resource "random_password" "api_token" {
  length  = 43
  special = false
}

resource "aws_secretsmanager_secret" "api_token" {
  name = "${local.name}/api-token"
}

resource "aws_secretsmanager_secret_version" "api_token" {
  secret_id     = aws_secretsmanager_secret.api_token.id
  secret_string = random_password.api_token.result
}

# ── Persistent state: EFS for SQLite and the spend ledger ────────────────────

resource "aws_efs_file_system" "state" {
  encrypted = true

  tags = {
    Name = "${local.name}-state"
  }
}

resource "aws_security_group" "efs" {
  name   = "${local.name}-efs"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.service.id]
  }
}

resource "aws_efs_mount_target" "state" {
  for_each        = toset(data.aws_subnets.default.ids)
  file_system_id  = aws_efs_file_system.state.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "state" {
  file_system_id = aws_efs_file_system.state.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/kairos"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "750"
    }
  }
}

# ── Logs ─────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}-backend"
  retention_in_days = 30
}

# ── IAM ──────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
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
  name = "read-api-token"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.api_token.arn]
    }]
  })
}

# What the running code may do: invoke Bedrock, mount its own EFS. Nothing else.
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
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = [aws_efs_file_system.state.arn]
      },
    ]
  })
}

# ── ECS: cluster, task, single-task service ──────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
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

    environment = [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "BEDROCK_MODEL_REASONING", value = var.bedrock_model_reasoning },
      { name = "BEDROCK_MODEL_CLASSIFY", value = var.bedrock_model_classify },
      { name = "KAIROS_DB_URL", value = "sqlite:////data/kairos.db" },
      { name = "KAIROS_STATE_DIR", value = "/data/state" },
      { name = "KAIROS_DAILY_USD_CAP", value = var.daily_usd_cap },
    ]

    secrets = [
      { name = "KAIROS_API_TOKEN", valueFrom = aws_secretsmanager_secret.api_token.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.backend.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "backend"
      }
    }
  }])
}

resource "aws_security_group" "service" {
  name   = "${local.name}-service"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  launch_type     = "FARGATE"

  # SQLite on EFS is single-writer in practice. One task, and a deploy stops
  # the old task before starting the new one, for the same reason.
  desired_count                      = 1
  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true
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
  name   = "${local.name}-alb"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "backend" {
  name               = "${local.name}-backend"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "backend" {
  name        = "${local.name}-backend"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    # The one endpoint the bearer-token gate leaves open, for exactly this.
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.backend.arn
  port              = 80
  protocol          = "HTTP"

  # With a certificate, port 80 only redirects. Without one, it serves —
  # demo only, and the tradeoff is written down in infra/README.md.
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

# ── The schedule: the loop that runs while the founder is asleep ─────────────
#
# EventBridge Scheduler → API destination → POST /founders/{id}/runs, with
# the same Authorization header the dashboard's proxy sends. One code path
# into a run, whoever asks for it.

resource "aws_cloudwatch_event_connection" "backend" {
  name               = "${local.name}-backend"
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      key   = "Authorization"
      value = "Bearer ${random_password.api_token.result}"
    }
  }
}

resource "aws_cloudwatch_event_api_destination" "trigger_run" {
  name                             = "${local.name}-trigger-run"
  connection_arn                   = aws_cloudwatch_event_connection.backend.arn
  http_method                      = "POST"
  invocation_endpoint              = "${var.certificate_arn == "" ? "http" : "https"}://${aws_lb.backend.dns_name}/founders/${var.founder_id}/runs"
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
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-api-destination"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["events:InvokeApiDestination"]
      Resource = [aws_cloudwatch_event_api_destination.trigger_run.arn]
    }]
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

    input = jsonencode({
      use_demo_catalog   = false
      include_grants_gov = true
    })

    retry_policy {
      # A failed run retries once, shortly after. More would risk the
      # overlap the single-writer note above exists to prevent.
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}
