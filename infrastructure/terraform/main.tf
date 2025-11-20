# Terraform and provider requirements are in versions.tf

provider "aws" {
  region = var.aws_region
  
  default_tags {
    tags = {
      Project     = "hostaway-messages"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Data source for default VPC
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# RDS Subnet Group
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = data.aws_subnets.default.ids
  
  tags = {
    Name = "${var.project_name}-db-subnet-group"
  }
}

# RDS Security Group
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Security group for RDS PostgreSQL instance"
  vpc_id      = data.aws_vpc.default.id
  
  # Allow PostgreSQL from anywhere (needed for Vercel)
  # In production, you may want to restrict this to Vercel IP ranges
  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  tags = {
    Name = "${var.project_name}-rds-sg"
  }
}

# RDS Parameter Group (optional optimizations for small workloads)
resource "aws_db_parameter_group" "main" {
  name   = "${var.project_name}-postgres15"
  family = "postgres15"
  
  # Optimize for small workloads
  # Note: Both parameters are static and require pending-reboot
  parameter {
    name         = "max_connections"
    value        = "50"  # Small instance limit
    apply_method = "pending-reboot"
  }
  
  parameter {
    name         = "shared_buffers"
    value        = "{DBInstanceClassMemory/4}"  # Auto-calculated
    apply_method = "pending-reboot"  # Static parameter requires reboot
  }
}

# RDS PostgreSQL Instance
resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-db"
  
  # Engine configuration
  engine         = "postgres"
  engine_version = var.postgres_version
  instance_class = var.db_instance_class
  
  # Database configuration
  db_name  = "postgres"  # Initial database
  username = var.db_username
  password = var.db_password
  
  # Storage configuration (minimal for cost)
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage  # Auto-scaling
  storage_type          = "gp3"
  storage_encrypted      = true
  
  # Network configuration
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = true  # Required for Vercel
  port                   = 5432
  
  # Backup configuration (minimal for cost)
  # Free tier allows max 1 day, paid tier can use 7
  backup_retention_period = 1  # Free tier limit
  backup_window          = "03:00-04:00"
  maintenance_window     = "mon:04:00-mon:05:00"
  
  # Performance and cost optimizations
  parameter_group_name = aws_db_parameter_group.main.name
  performance_insights_enabled = false  # Disable for cost savings
  monitoring_interval          = 0      # Disable enhanced monitoring for cost
  
  # Deletion protection (set to false for easy cleanup during development)
  deletion_protection = var.deletion_protection
  skip_final_snapshot = !var.deletion_protection
  
  # Enable automated backups
  enabled_cloudwatch_logs_exports = ["postgresql"]
  
  tags = {
    Name = "${var.project_name}-postgresql"
  }
}

# Create databases in PostgreSQL
# Note: Terraform cannot create databases inside RDS, so we use a local provisioner
# If psql is not available, you can create databases manually using the script:
# ./scripts/create_databases.sh <endpoint> <username> <password>
resource "null_resource" "create_databases" {
  depends_on = [aws_db_instance.main]
  
  provisioner "local-exec" {
    command = <<-EOT
      if command -v psql &> /dev/null; then
        PGPASSWORD='${replace(var.db_password, "'", "'\"'\"'")}' psql \
          -h ${aws_db_instance.main.endpoint} \
          -U ${var.db_username} \
          -d postgres \
          -c "CREATE DATABASE hostaway_main;" \
          -c "CREATE DATABASE hostaway_users;" \
          -c "CREATE DATABASE hostaway_ai_cache;" 2>&1 || echo "Database creation skipped (may already exist or psql not available)"
      else
        echo "psql not found. Please create databases manually:"
        echo "  ./scripts/create_databases.sh ${aws_db_instance.main.endpoint} ${var.db_username} <password>"
      fi
    EOT
  }
  
  triggers = {
    db_endpoint = aws_db_instance.main.endpoint
  }
}

# S3 Bucket for conversation files
resource "aws_s3_bucket" "conversations" {
  bucket = "${var.project_name}-conversations-${random_id.bucket_suffix.hex}"
  
  tags = {
    Name = "${var.project_name}-conversations"
  }
}

# Random suffix for unique bucket name
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# S3 Bucket Versioning (optional, for safety)
resource "aws_s3_bucket_versioning" "conversations" {
  bucket = aws_s3_bucket.conversations.id
  
  versioning_configuration {
    status = var.enable_s3_versioning ? "Enabled" : "Disabled"
  }
}

# S3 Bucket Encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "conversations" {
  bucket = aws_s3_bucket.conversations.id
  
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 Bucket Public Access Block (keep private)
resource "aws_s3_bucket_public_access_block" "conversations" {
  bucket = aws_s3_bucket.conversations.id
  
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# IAM User for S3 Access
resource "aws_iam_user" "s3_user" {
  name = "${var.project_name}-s3-user"
  
  tags = {
    Name = "${var.project_name}-s3-user"
  }
}

# IAM Policy for S3 Access
resource "aws_iam_user_policy" "s3_access" {
  name = "${var.project_name}-s3-access"
  user = aws_iam_user.s3_user.name
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.conversations.arn}/*",
          aws_s3_bucket.conversations.arn
        ]
      }
    ]
  })
}

# IAM Access Key for S3 User
resource "aws_iam_access_key" "s3_user" {
  user = aws_iam_user.s3_user.name
}

# Outputs
output "rds_endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.main.endpoint
}

output "rds_port" {
  description = "RDS instance port"
  value       = aws_db_instance.main.port
}

output "database_urls" {
  description = "PostgreSQL connection strings"
  value = {
    main  = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_main"
    users = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_users"
    cache = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_ai_cache"
  }
  sensitive = true
}

output "s3_bucket_name" {
  description = "S3 bucket name for conversations"
  value       = aws_s3_bucket.conversations.id
}

output "s3_bucket_region" {
  description = "S3 bucket region"
  value       = aws_s3_bucket.conversations.region
}

output "aws_access_key_id" {
  description = "AWS Access Key ID for S3"
  value       = aws_iam_access_key.s3_user.id
  sensitive   = true
}

output "aws_secret_access_key" {
  description = "AWS Secret Access Key for S3"
  value       = aws_iam_access_key.s3_user.secret
  sensitive   = true
}

output "connection_info" {
  description = "All connection information (sensitive)"
  value = {
    rds_endpoint           = aws_db_instance.main.endpoint
    rds_port              = aws_db_instance.main.port
    database_urls          = {
      main  = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_main"
      users = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_users"
      cache = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_ai_cache"
    }
    s3_bucket_name         = aws_s3_bucket.conversations.id
    s3_bucket_region       = aws_s3_bucket.conversations.region
    aws_access_key_id      = aws_iam_access_key.s3_user.id
    aws_secret_access_key  = aws_iam_access_key.s3_user.secret
  }
  sensitive = true
}

