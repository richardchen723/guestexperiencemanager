# DEPRECATED: This file is no longer used.
# The application now uses local filesystem storage instead of AWS S3.
# This file is kept for reference only.
# For Lightsail deployment, files are stored in the conversations/ directory on the instance.

# S3 Bucket for file storage
resource "aws_s3_bucket" "main" {
  bucket        = var.s3_bucket_name
  force_destroy = false # Set to true if you want to allow bucket deletion with contents

  tags = {
    Name        = "${var.environment}-hostaway-storage"
    Environment = var.environment
  }
}

# Enable versioning (disabled by default for cost savings)
# Uncomment if versioning is required
# resource "aws_s3_bucket_versioning" "main" {
#   bucket = aws_s3_bucket.main.id
#   versioning_configuration {
#     status = "Enabled"
#   }
# }

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "main" {
  bucket = aws_s3_bucket.main.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CORS configuration (if needed for web access)
resource "aws_s3_bucket_cors_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST", "DELETE", "HEAD"]
    allowed_origins = ["*"] # Restrict to specific origins in production
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# S3 Intelligent-Tiering for automatic cost optimization
resource "aws_s3_bucket_intelligent_tiering_configuration" "main" {
  count  = var.s3_intelligent_tiering ? 1 : 0
  bucket = aws_s3_bucket.main.id
  name   = "EntireBucket"

  # Filter is optional - omitting it applies to entire bucket
  # Removed empty filter block as it's not needed

  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90
  }

  tiering {
    access_tier = "DEEP_ARCHIVE_ACCESS"
    days        = 180
  }
}

# Lifecycle policies for cost optimization
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  count  = var.s3_lifecycle_enabled ? 1 : 0
  bucket = aws_s3_bucket.main.id

  # Rule 1: Move to Standard-IA after 90 days
  rule {
    id     = "transition-to-standard-ia"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }

  # Rule 2: Move to Glacier Flexible Retrieval after 180 days
  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = 180
      storage_class = "GLACIER"
    }
  }

  # Rule 3: Delete incomplete multipart uploads after 7 days
  rule {
    id     = "delete-incomplete-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Rule 4: Delete old delete markers after 30 days (if versioning enabled)
  # Uncomment if versioning is enabled
  # rule {
  #   id     = "delete-old-delete-markers"
  #   status = "Enabled"
  #
  #   filter {
  #     prefix = ""
  #   }
  #
  #   noncurrent_version_expiration {
  #     noncurrent_days = 30
  #   }
  # }
}

# IAM Policy for application access to S3
resource "aws_iam_policy" "s3_access" {
  name        = "${var.environment}-hostaway-s3-access"
  description = "Policy for Hostaway application to access S3 bucket"

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
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
      }
    ]
  })
}

# Output IAM policy ARN for attachment to application role
output "s3_access_policy_arn" {
  value       = aws_iam_policy.s3_access.arn
  description = "ARN of IAM policy for S3 access (attach to application IAM role)"
}


