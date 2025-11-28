# Generate random password for database
resource "random_password" "db_password" {
  length  = 32
  special = true
}

# Store database credentials in AWS Secrets Manager
resource "aws_secretsmanager_secret" "db_credentials" {
  name                    = "${var.environment}-hostaway-db-credentials"
  description             = "Database credentials for Hostaway application"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db_password.result
    engine   = "postgres"
    host     = aws_db_instance.main.endpoint
    port     = aws_db_instance.main.port
    dbname   = var.db_name
  })
}

# DB Subnet Group (use default VPC if not specified)
data "aws_vpc" "default" {
  count   = var.vpc_id == "" ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.vpc_id == "" && length(var.subnet_ids) == 0 ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.environment}-hostaway-db-subnet-group"
  subnet_ids = length(var.subnet_ids) > 0 ? var.subnet_ids : (var.vpc_id == "" ? data.aws_subnets.default[0].ids : [])

  tags = {
    Name = "${var.environment}-hostaway-db-subnet-group"
  }
}

# Security Group for RDS
resource "aws_security_group" "rds" {
  name        = "${var.environment}-hostaway-rds-sg"
  description = "Security group for Hostaway RDS instance"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = length(var.allowed_cidr_blocks) > 0 ? var.allowed_cidr_blocks : ["10.0.0.0/8"]
    description = "PostgreSQL access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = {
    Name = "${var.environment}-hostaway-rds-sg"
  }
}

# DB Parameter Group (optimized for medium workloads)
resource "aws_db_parameter_group" "main" {
  name   = "${var.environment}-hostaway-postgres15"
  family = "postgres15"

  # Static parameters (require restart) - use pending-reboot
  # Note: These values are optimized for db.t4g.micro (1GB RAM) - Free Tier compatible
  # For db.t4g.medium (4GB RAM) or larger, increase shared_buffers and effective_cache_size proportionally
  parameter {
    name         = "shared_buffers"
    value        = "32768" # 256MB in 8KB pages (32768 * 8KB = 256MB) - 25% of 1GB RAM for micro instance
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "effective_cache_size"
    value        = "98304" # 768MB in 8KB pages (98304 * 8KB = 768MB) - ~75% of 1GB RAM for micro instance
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "wal_buffers"
    value        = "2048" # 16MB in 8KB pages (2048 * 8KB = 16MB)
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "min_wal_size"
    value        = "65536" # 512MB in 8KB pages (65536 * 8KB = 512MB) - Reduced for micro instance
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "max_wal_size"
    value        = "131072" # 1GB in 8KB pages (131072 * 8KB = 1GB) - Reduced for micro instance
    apply_method = "pending-reboot"
  }

  # Dynamic parameters (can be applied immediately)
  parameter {
    name         = "maintenance_work_mem"
    value        = "16384" # 16MB in KB (16384 KB = 16MB) - Reduced for micro instance
    apply_method = "immediate"
  }

  parameter {
    name         = "checkpoint_completion_target"
    value        = "0.9"
    apply_method = "immediate"
  }

  parameter {
    name         = "default_statistics_target"
    value        = "100"
    apply_method = "immediate"
  }

  parameter {
    name         = "random_page_cost"
    value        = "1.1"
    apply_method = "immediate"
  }

  parameter {
    name         = "effective_io_concurrency"
    value        = "100" # Reduced from 200 - RDS may have constraints on this value
    apply_method = "immediate"
  }

  parameter {
    name         = "work_mem"
    value        = "4096" # 4MB in KB (4096 KB = 4MB) - Appropriate for micro instance (1GB RAM)
    apply_method = "immediate"
  }

  tags = {
    Name = "${var.environment}-hostaway-postgres15-params"
  }
}

# RDS PostgreSQL Instance
resource "aws_db_instance" "main" {
  identifier = "${var.environment}-hostaway-db"

  # Engine configuration
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  # Storage configuration
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  # Database configuration
  db_name  = var.db_name
  username = var.db_username
  password = random_password.db_password.result

  # Network configuration
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = length(var.allowed_cidr_blocks) > 0 ? true : false

  # Backup configuration
  # Note: Free tier accounts are limited to 1 day backup retention
  # Increase to 7+ days after free tier expires
  backup_retention_period = var.db_backup_retention
  backup_window           = "03:00-04:00"
  copy_tags_to_snapshot   = true

  # Maintenance configuration
  maintenance_window         = "sun:04:00-sun:05:00"
  auto_minor_version_upgrade = var.db_auto_minor_version_upgrade

  # Performance and monitoring
  performance_insights_enabled    = var.db_performance_insights
  monitoring_interval             = 0 # Disable enhanced monitoring to save costs
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # High availability (disabled for cost savings)
  multi_az = var.db_multi_az

  # Parameter group
  parameter_group_name = aws_db_parameter_group.main.name

  # Deletion protection (enable in production)
  deletion_protection = var.environment == "production" ? true : false
  skip_final_snapshot = var.environment != "production"

  tags = {
    Name = "${var.environment}-hostaway-db"
  }
}


