variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (production, staging, etc.)"
  type        = string
  default     = "production"
}

variable "db_instance_class" {
  description = "RDS instance class. Default is db.t4g.micro for Free Tier compatibility. For paid accounts, use db.t4g.medium (4GB RAM, 2 vCPU) or larger for better performance."
  type        = string
  default     = "db.t4g.micro" # Free Tier compatible. Change to db.t4g.medium or larger after upgrading account.
}

variable "db_allocated_storage" {
  description = "Initial allocated storage for RDS (GB)"
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "Maximum allocated storage for RDS auto-scaling (GB)"
  type        = number
  default     = 100
}

variable "db_multi_az" {
  description = "Enable multi-AZ deployment (false for cost savings, acceptable for internal app)"
  type        = bool
  default     = false
}

variable "db_backup_retention" {
  description = "Number of days to retain automated backups (1 day for free tier accounts, 7+ for paid accounts)"
  type        = number
  default     = 1 # Set to 1 to avoid free tier restrictions, increase to 7+ after free tier expires
}

variable "db_auto_minor_version_upgrade" {
  description = "Enable automatic minor version upgrades"
  type        = bool
  default     = true
}

variable "db_performance_insights" {
  description = "Enable Performance Insights (false to save costs)"
  type        = bool
  default     = false
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "15.4"
}

variable "db_name" {
  description = "Name of the database to create"
  type        = string
  default     = "hostaway"
}

variable "db_username" {
  description = "Master username for RDS (will be stored in Secrets Manager)"
  type        = string
  default     = "hostaway_admin"
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket for file storage (optional, not used with Lightsail deployment)"
  type        = string
  default     = ""
}

variable "s3_lifecycle_enabled" {
  description = "Enable S3 lifecycle policies for cost optimization"
  type        = bool
  default     = true
}

variable "s3_intelligent_tiering" {
  description = "Enable S3 Intelligent-Tiering for automatic cost optimization"
  type        = bool
  default     = true
}

variable "allowed_cidr_blocks" {
  description = "List of CIDR blocks allowed to access RDS (leave empty for VPC-only)"
  type        = list(string)
  default     = []
}

variable "vpc_id" {
  description = "VPC ID for RDS subnet group (optional, will create if not provided)"
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "List of subnet IDs for RDS subnet group (optional)"
  type        = list(string)
  default     = []
}


