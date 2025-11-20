variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"  # Cheapest region typically
}

variable "project_name" {
  description = "Project name prefix for resources"
  type        = string
  default     = "hostaway-messages"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "postgres_version" {
  description = "PostgreSQL version"
  type        = string
  default     = "15.7"  # Available version in RDS
}

variable "db_instance_class" {
  description = "RDS instance class (use db.t3.micro or db.t4g.micro for free tier)"
  type        = string
  default     = "db.t3.micro"  # Free tier eligible, ~$15/month if not free tier
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB (minimum 20)"
  type        = number
  default     = 20  # Minimum, cost-effective
}

variable "db_max_allocated_storage" {
  description = "Maximum allocated storage for auto-scaling"
  type        = number
  default     = 100  # Auto-scale up to 100GB if needed
}

variable "db_username" {
  description = "Master database username"
  type        = string
  default     = "hostaway_admin"
  sensitive   = true
}

variable "db_password" {
  description = "Master database password (should be set via TF_VAR_db_password or .tfvars file)"
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.db_password) >= 8
    error_message = "Database password must be at least 8 characters long."
  }
}

variable "deletion_protection" {
  description = "Enable deletion protection for RDS instance"
  type        = bool
  default     = false  # Set to true in production
}

variable "enable_s3_versioning" {
  description = "Enable S3 bucket versioning (adds cost but provides safety)"
  type        = bool
  default     = false  # Disable for cost savings
}

